"""Step 1 NQDC gold, offline. Fixtures: Target's 2025 and 2026 proxies trimmed to the 402(i) table (styles and inline
tags removed) and its submissions JSON trimmed to the DEF 14A and 10-K rows; Comcast (no registrant column) and Cabot
(two plans per NEO) tables come from data_layer's fixtures; limit_sentences.json holds proxy sections of the
snapshot trimmed to the sentences stating deferral limits (and the plan names around them)."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from data_layer import edgar_fetch
from data_layer.edgar_fetch import archive_url, cache_path
from harness.schema import Question
from step1 import corpus as C
from step1 import nqdc_gold as m

FX = Path(__file__).parent / "fixtures" / "nqdc"
DL = Path(__file__).resolve().parents[2] / "data_layer" / "tests" / "fixtures" / "nqdc"
TGT = ("TGT", "Target Corporation", "Target", (27419,))
TGT_2026 = ("0001628280-26-027508", "tgt-20260426.htm", "tgt_2026_def14a.htm")
TGT_2025 = ("0001628280-25-020275", "tgt-20250428.htm", "tgt_2025_def14a.htm")


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """data/raw/ replaced by a directory holding only the Target fixtures; nothing is downloaded."""
    monkeypatch.setattr(edgar_fetch, "RAW", tmp_path)
    monkeypatch.setattr(m, "COMPANIES", m.COMPANIES + [TGT])
    monkeypatch.setattr(m, "SNAPSHOT", tmp_path / "no-snapshot.jsonl")
    monkeypatch.setenv("SEC_USER_AGENT", "")
    put = lambda url, src: (cache_path(url).parent.mkdir(parents=True, exist_ok=True), shutil.copy(src, cache_path(url)))
    put("https://data.sec.gov/submissions/CIK0000027419.json", FX / "CIK0000027419.json")
    for acc, doc, fx in (TGT_2026, TGT_2025):
        put(archive_url(27419, acc, doc), FX / fx)
    return put


def proxy(acc="X", fy="FY2025", fye="2025-12-31", rows=(), columns=None, status="table"):
    return {"key": "TGT", "cik": 27419, "accession": acc, "document": "d.htm", "filed": "2026-04-01", "fye": fye,
            "fy": fy, "url": "u", "status": status, "rows": list(rows), "tables": 1,
            "columns": columns if columns is not None else {f: f for f in m.AMOUNT_FIELDS}}


def test_fiscal_year_labels():
    assert m.fy_label("TGT", "2026-01-31") == "FY2025"     # Target and Home Depot name the year it starts in
    assert m.fy_label("HD", "2026-02-01") == "FY2025"
    assert m.fy_label("WMT", "2026-01-31") == "FY2026"     # Walmart and NVIDIA the year it ends in
    assert m.fy_label("AAPL", "2025-09-27") == "FY2025"
    assert m.fy_label("JNJ", "2023-01-01") == "FY2022"     # 52/53-week year ending just after 31 December


def test_proxies_take_the_fiscal_year_of_the_latest_10k(cache):
    ps = m.proxies(m.company("TGT"))
    assert [(p["filed"], p["fye"], p["fy"]) for p in ps] == [
        ("2023-05-01", "2023-01-28", "FY2022"), ("2024-04-29", "2024-02-03", "FY2023"),
        ("2025-04-28", "2025-02-01", "FY2024"), ("2026-04-27", "2026-01-31", "FY2025")]   # 2022 proxy: before 2023


def test_read_proxy_statuses(cache, tmp_path):
    ps = {p["fy"]: p for p in m.proxies(m.company("TGT"))}
    assert m.read_proxy(ps["FY2023"])["status"] == "not_cached"
    p = m.read_proxy(ps["FY2025"])
    assert p["status"] == "table" and p["tables"] == 1 and len(p["rows"]) == 6
    assert p["columns"]["registrant_contributions"] == "Registrant contributions in last FY(2)"
    cornell = p["rows"][0]
    assert (cornell.executive, cornell.registrant_contributions, cornell.aggregate_balance) == (
        "Brian C. Cornell", 168686, 8005585)
    no_table = tmp_path / "n.htm"
    no_table.write_text("<p>Summary Compensation Table</p><p>We do not maintain a nonqualified deferred compensation "
                        "plan for our named executive officers.</p>")
    cache(ps["FY2023"]["url"], no_table)
    n = m.read_proxy(ps["FY2023"])
    assert n["status"] == "no_table" and "do not maintain" in n["mentions"][0]
    no_table.write_text("<p>Special meeting to approve the merger.</p>")
    cache(ps["FY2022"]["url"], no_table)
    assert m.read_proxy(ps["FY2022"])["status"] == "special"


def test_comcast_table_without_registrant_column():
    rows, cols, _ = m.read_tables((DL / "cmcsa_def14a_2026_nqdc_table.html").read_text(), date(2025, 12, 31), "C")[0]
    assert "registrant_contributions" not in cols and set(cols) == set(m.AMOUNT_FIELDS) - {"registrant_contributions"}
    assert rows[0].executive == "Brian L. Roberts"


def test_roll_forward_matches_pilot(cache):
    read = [m.read_proxy(p) for p in m.proxies(m.company("TGT"))]
    res = {(r["fy"], r["executive"]): r for r in m.roll_forward(read)}
    assert res[("FY2025", "Brian C. Cornell")]["status"] == "ok"
    assert res[("FY2025", "Amy Tu")]["difference"] == -1
    assert res[("FY2025", "Melissa K. Kremer")]["status"] == "not_checkable"   # not an NEO the year before
    assert res[("FY2024", "Jim Lee")]["note"] == "prior-year proxy not_cached"
    assert m.neo_key("Brian Cornell") == m.neo_key("Brian C. Cornell, Jr.") == "B CORNELL"


def test_gold_402i_reported_not_reported_and_summed_over_plans():
    cabot = m.read_tables((DL / "cabot_def14a_fy2023_nqdc_table.html").read_text(), date(2023, 9, 30), "K")[0]
    fam = next(f for f in m.T.load_families() if f["id"] == "NQDC-402I")
    q = lambda neo: m.T.make_question(fam, {"key": "TGT", "name": "Target Corporation", "short": "Target"}, ("FY2023",),
                                      {"key": "X", "neo": neo})
    g = {s["slot"]: s for s in m.gold_402i(q("Sean D. Keohane"), proxy(fy="FY2023", rows=cabot[0], columns=cabot[1]))}
    bal = g["year_end_balance"]
    assert (bal["status"], bal["value"], bal["formula"]) == ("derived", 2967182 + 2700426, "sum over plans")
    assert [i["plan"] for i in bal["inputs"]] == ["Deferred Compensation Plan", "Supplemental 401(k) Plan"]
    zhu = {s["slot"]: s for s in m.gold_402i(q("Jeff Zhu"), proxy(rows=cabot[0], columns=cabot[1]))}
    assert zhu["year_end_balance"]["status"] == "reported" and zhu["year_end_balance"]["value"] == 321138
    cols = {f: f for f in m.AMOUNT_FIELDS if f != "registrant_contributions"}
    g = {s["slot"]: s for s in m.gold_402i(q("Jeff Zhu"), proxy(rows=cabot[0], columns=cols))}
    assert g["registrant_contributions"]["status"] == "not_reported" and g["registrant_contributions"]["value"] is None
    assert g["registrant_contributions"]["accept"] == ["0", "none"] and g["registrant_contributions"]["cell"] == "column omitted"
    g = m.gold_402i(q("Jeff Zhu"), proxy(status="no_table", rows=[], columns={}))
    assert {s["status"] for s in g} == {"not_reported"}


TGT_LIMIT = ("Tu $14,596 Participants in the EDCP may generally elect to defer up to 80% of their salary, bonus, and "
             "non-equity incentive plan payments.")   # Target's 2026 proxy: one percent for salary and bonus together


def test_build_stratifies_years_and_writes_anchorable_facts(cache, tmp_path):
    """Target has tables for FY2024 and FY2025 in the fixture cache. Its proxy states no limit a rule can split into
    salary and bonus, so its second question is a second NQDC-402I in the other year; each gold row comes from its
    own year's proxy."""
    (tmp_path / "docs.jsonl").write_text(json.dumps({"accession": TGT_2026[0], "doc_id": "tgt-def14a-fy2025-s001",
                                                     "form_type": "DEF 14A", "meta": {"section": 1}, "text": TGT_LIMIT}) + "\n")
    b = m.build(["TGT"])
    q, t = b["questions"]
    assert Question.model_validate_json(q.model_dump_json()) == q and q.family == t.family == "NQDC-402I"
    assert {q.period, t.period} == {"FY2024", "FY2025"} and b["limits"][("TGT", "FY2025")] == []
    assert m.proxy_limits(m.snapshot_proxies({TGT_2026[0]})[TGT_2026[0]]) == []
    neo = q.slots[0].scope.removeprefix("NEO:")
    assert neo in b["entities"][0]["neos"][q.period] and "Amy Tu" not in b["entities"][0]["neos"]["FY2025"]
    g402 = [g for g in b["gold"] if g["question_id"] == q.id]
    acc = {"FY2025": TGT_2026[0], "FY2024": TGT_2025[0]}[q.period]
    assert [g["slot"] for g in g402] == [s.slot_id for s in q.slots]
    assert all(g["accession"] == acc and g["fiscal_year"] == q.period and g["neo"] == neo for g in g402)
    assert len(b["facts"]) == 2 * 6 * 5
    f = next(f for f in b["facts"] if f["scope"] == "NEO:Brian C. Cornell" and f["concept"] == "nqdc:aggregate_balance"
             and f["period"] == "FY2025")
    assert (f["value"], f["entity"], f["column_header"]) == (8005585, "0000027419", "Aggregate balance at last FYE(4)")
    assert len(m.annotation_cells(b["asked"]["TGT"])) == 6 * 5 and len(b["asked_402i"]) == 2
    assert m.limit_rows(b) == []


def test_roll_question_gold_keeps_the_difference(cache):
    """Target FY2025 against FY2024: Amy Tu's balance misses the identity by $1, recorded as it is; a table without
    the withdrawals column leaves the identity open, so no question."""
    ps = {p["fy"]: m.read_proxy(p) for p in m.proxies(m.company("TGT"))}
    prm = m.roll_entry(ps["FY2025"], ps["FY2024"], "Amy Tu")
    assert prm == {"key": "TU", "neo": "Amy Tu", "neo_prior": "Amy Tu", "fy0": "2024"}
    fam = next(f for f in m.T.load_families() if f["id"] == "NQDC-ROLL")
    ent = {"key": "TGT", "name": "Target Corporation", "short": "Target", "kind": "company"}
    q = m.T.make_question(fam, ent, ("FY2025",), prm)
    assert q.id == "S1-NQDC-ROLL-TGT-TU-FY2025" and "end of fiscal 2024" in q.text and "reconcile" in q.text
    g = {x["slot"]: x for x in m.gold_roll(q, ps["FY2025"], ps["FY2024"], prm)}
    assert [x for x in g] == [s.slot_id for s in q.slots]
    assert g["prior_year_end_balance"]["accession"] == TGT_2025[0] and g["year_end_balance"]["accession"] == TGT_2026[0]
    assert g["reconciliation_difference"]["value"] == -1 and g["reconciliation_difference"]["status"] == "derived"
    row = next(r for r in m.roll_forward(list(ps.values())) if (r["fy"], r["executive"]) == ("FY2025", "Amy Tu"))
    assert row["difference"] == g["reconciliation_difference"]["value"]
    no_e = {**ps["FY2025"], "columns": {f: h for f, h in ps["FY2025"]["columns"].items() if f != "aggregate_withdrawals"}}
    assert m.roll_entry(no_e, ps["FY2024"], "Amy Tu") is None


def test_blank_cell_is_not_reported_with_its_evidence():
    fam = next(f for f in m.T.load_families() if f["id"] == "NQDC-402I")
    q = m.T.make_question(fam, {"key": "TGT", "name": "Target Corporation", "short": "Target"}, ("FY2025",),
                          {"key": "X", "neo": "Jane Doe"})
    row = m.NQDCTableRow(executive="Jane Doe", plan=None, fiscal_year_end=date(2025, 12, 31), source="X",
                         executive_contributions=None, registrant_contributions=5.0, aggregate_earnings=1.0,
                         aggregate_withdrawals=0.0, aggregate_balance=6.0)
    g = {x["slot"]: x for x in m.gold_402i(q, proxy(rows=[row]))}
    assert (g["executive_contributions"]["status"], g["executive_contributions"]["cell"], g["executive_contributions"]["value"]) == (
        "not_reported", "", None)
    assert g["registrant_contributions"]["status"] == "reported"


@pytest.mark.parametrize("sentence, condition", [
    ("The Nonqualified Savings Plan is designed to allow each employee to contribute up to 6 percent of his or her base "
     "salary and receive a company match, beyond IRS contribution limits.", "beyond IRS contribution limits"),
    ("The NQDC plan provides those employees whose earnings in a prior year were in excess of the dollar limit under "
     "Section 401(a)(17) of the Internal Revenue Code the ability to defer up to 20% of their base salary after deferrals "
     "to the ECAP (our tax-qualified defined contribution plan or 401(k) plan) have ceased due to qualified plan limits.",
     "after deferrals to the ECAP (our tax-qualified defined contribution plan or 401(k) plan) have ceased due to "
     "qualified plan limits"),
    ("This plan allows eligible employees to defer up to 50% of their base salary and 100% of their annual incentive.", None),
])
def test_limit_condition_text_is_verbatim(sentence, condition):
    (f, *_) = m.limits_in(sentence)
    assert f["condition_text"] == condition and (condition is None or condition in sentence)


def test_untagged_fund_numbers_record_the_full_text_search():
    e = {"plan_liability": {"value": None, "status": "not_reported", "notes": "no deferred compensation liability tag"},
         "coli_value": {"value": None, "status": "not_reported", "notes": "no company-owned life insurance tag"}}
    ba = ("As of December 31, 2024 and 2023, the deferred compensation liability which is being marked to market was "
          "$1,675 and $1,640.")                                                      # Boeing's 10-K for 2024
    m.full_text_note(e, [{"text": "Other text. " + ba}])
    assert "searched the 10-K full text, patterns:" in e["plan_liability"]["notes"] and e["plan_liability"]["evidence"] == [ba]
    assert e["coli_value"]["notes"].endswith("no sentence prints it") and "evidence" not in e["coli_value"]
    assert e["plan_liability"]["status"] == "not_reported"


def test_total_row_is_reported_with_the_rows_as_alt_derivation():
    """Honeywell prints a Total row under each NEO: the slots are read off it (reported); the plan rows' sum stays as
    alt_derivation, and where the printed Total differs from it by rounding both are accepted."""
    tables = m.read_tables((FX / "hon_2026_nqdc.htm").read_text(), date(2025, 12, 31), "H")
    rows = [x for rs, _, _ in tables for x in rs]
    marks = {k: v for _, _, mk in tables for k, v in mk.items()}
    p = proxy(rows=rows, columns=tables[0][1]) | {"key": "HON", "marks": marks}
    assert m.total_row(p, "Vimal Kapur")["aggregate_balance"] == (9061144.0, "9,061,144")
    fam = next(f for f in m.T.load_families() if f["id"] == "NQDC-402I")
    q = m.T.make_question(fam, {"key": "HON", "name": "Honeywell International Inc.", "short": "Honeywell"}, ("FY2025",),
                          {"key": "KAPUR", "neo": "Vimal Kapur"})
    g = {x["slot"]: x for x in m.gold_402i(q, p)}
    bal = g["year_end_balance"]
    assert (bal["status"], bal["value"], bal["row"], bal["alt_derivation"]["value"]) == ("reported", 9061144, "Total", 9061144)
    assert bal["source"]["locator"].endswith("Total row") and "accept" not in bal
    off = {**marks, ("Vimal Kapur", "Total"): {"totals": [{**marks[("Vimal Kapur", "Total")]["totals"][0],
                                                            "aggregate_earnings": (374689.0, "374,689")}]}}
    e = {x["slot"]: x for x in m.gold_402i(q, p | {"marks": off})}["aggregate_earnings"]
    assert e["value"] == 374689 and e["accept"] == ["374689", "374688"]
    fs = [f for f in m.facts(p, {"name": "Honeywell"}) if f["dimensions"]["plan"] == "Total"]
    assert {(f["dimensions"]["executive"], f["concept"]) for f in fs} >= {("Vimal Kapur", "nqdc:aggregate_balance")}
    grand = {**marks, ("Vimal Kapur", "Total"): {"totals": [{"aggregate_balance": (1.0, "1")}]}}
    assert m.total_row(p | {"marks": grand}, "Vimal Kapur") is None          # not the NEO's rows summed: not used


def test_derived_value_printed_in_another_table_is_listed():
    """ConocoPhillips FY2025: O'Brien's registrant contributions summed over two plans equal the All Other
    Compensation column (j); the 402(i) table's own row is not listed."""
    aoc = ("A.M. O’Brien | Personal Use of Company Aircraft (a): — | Matching Contributions Under the Tax-Qualified Savings "
           "Plans (i): 31,500 | Company Contributions to Non-Qualified Defined Contribution Plans (j): 45,634 | Total: 119,359")
    nqdc_row = ("A.M. O’Brien | DCMP | Beginning Balance: 184,110 | Executive Contributions in Last FY (2): — | Registrant "
                "Contributions in Last FY (3): 45,634 | Aggregate Earnings in Last FY (4): 33,919 | Aggregate Withdrawals/ "
                "Distributions: — | Aggregate Balance at Last FYE (5): 263,663")
    g = {"value": 45634.0, "neo": "A.M. O’Brien"}
    assert m.also_printed(g, [{"doc_id": "s029", "text": aoc}, {"doc_id": "s036", "text": nqdc_row}]) == [
        {"doc_id": "s029", "row": "A.M. O’Brien", "header": "Company Contributions to Non-Qualified Defined Contribution Plans (j)",
         "printed": "45,634"}]


def rows_of(name):
    return {(r.executive, r.plan): r for rs, _, _ in m.read_tables((FX / name).read_text(), date(2025, 12, 31), "X")
            for r in rs}


def test_name_rows_followed_by_plan_rows_and_na_cells():
    """Home Depot: the NEO on a row of its own, one row per plan under it; N/A and blank cells."""
    r = rows_of("hd_2026_nqdc.htm")
    dec = r[("Edward P. Decker", "THD Restoration Plan")]
    assert (dec.executive_contributions, dec.registrant_contributions, dec.aggregate_earnings, dec.aggregate_balance) == (
        0, 132774, -261721, 3728272)              # the cell prints N/A (pandas must not read it as empty)
    hd23 = {(x.executive, x.plan): mk for rows, _, marks in m.read_tables((FX / "hd_2024_nqdc.htm").read_text(),
                                                                         date(2024, 1, 28), "H") for x in rows
            for mk in [marks[(x.executive, x.plan)]]}
    assert hd23[("Ann-Marie Campbell", "THD Restoration Plan")]["na"] == ["executive_contributions"]   # "(5)" | "N/A"
    assert r[("Edward P. Decker", "Deferred Compensation Plan for Officers")].aggregate_balance == 1449199
    assert r[("Jordan Broggi", "THD Restoration Plan")].executive_contributions == 0   # N/A
    assert len({k[0] for k in r}) == 7


def test_negative_split_over_cells_and_column_letter_rows():
    cvx = rows_of("cvx_2024_nqdc.htm")
    assert cvx[("P.R. Breber", None)].aggregate_earnings == -1592179          # "($" | "1,592,179" | ")"
    jnj = rows_of("jnj_2026_nqdc.htm")                                        # header rows "A B C ..." and "Name ..."
    assert jnj[("J. Duato", None)].aggregate_balance == 10168011 and len(jnj) == 5


def test_tables_split_over_pages_totals_and_sct_reconciliation():
    """Honeywell: the 402(i) table continues on the next page; per-NEO Total rows are skipped; a third table
    reconciling contributions to the summary compensation table is not a 402(i) table."""
    tables = m.read_tables((FX / "hon_2026_nqdc.htm").read_text(), date(2025, 12, 31), "H")
    assert len(tables) == 2
    r = {(x.executive, x.plan): x for rows, _, _ in tables for x in rows}
    assert r[("Vimal Kapur", "DIC Plan")].executive_contributions == 1457754
    assert r[("Lucian Boldea", "SS Plan")].aggregate_balance == 642763
    assert not any(p and p.startswith("Total") for _, p in r)
    tot = m.neo_totals(list(r.values()))["Vimal Kapur"]
    assert tot.aggregate_balance == 9061144                                     # the printed Total row


def test_positioned_text_table_with_run_together_cells():
    """ExxonMobil prints the table as positioned text; its 2025 proxy runs '51,706' and '0' together."""
    (rows, cols, _), = m.read_tables((FX / "xom_2025_nqdc_text.htm").read_text(), date(2024, 12, 31), "X")
    woods = rows[0]
    assert (woods.executive, woods.registrant_contributions, woods.aggregate_earnings, woods.aggregate_withdrawals,
            woods.aggregate_balance) == ("D.W. Woods", 113680, 51706, 0, 1086812)
    assert len(rows) == 5 and set(cols) == set(m.AMOUNT_FIELDS)


def plan_sources(tmp_path, names):
    src = tmp_path / "sources.jsonl"
    src.write_text("".join(json.dumps({"key": "EXM", "cik": 1, "accession": f"0000000001-2{i}-00000{i}", "plan": {"role": "?"},
                                       "files": [{"path": f"step1/tests/fixtures/nqdc/{n}"}]}) + "\n"
                           for i, n in enumerate(names)))
    return src


def test_plan_versions_and_the_changed_section(tmp_path, monkeypatch):
    """Two versions of one plan are grouped by text similarity (the amendment and the other plan are not), dated by
    their opening line; the term is the first non-meta section whose text changed, quoted verbatim from both."""
    monkeypatch.setattr(m, "SNAPSHOT", plan_sources(tmp_path, ["plan_v2023.htm", "plan_amendment.htm", "plan_v2025.htm",
                                                                "other_plan.htm"]))
    monkeypatch.setattr(m, "tagged", lambda co, fy, tags: None)
    (vs,) = m.plan_versions().values()
    assert [(v["document"], v["effective"]) for v in vs] == [("plan_v2023.htm", "2023-01-01"), ("plan_v2025.htm", "2025-01-01")]
    docs = {d["document"]: d for d in m.plan_docs_all()}
    assert docs["plan_amendment.htm"]["amendment"] and not docs["other_plan.htm"]["amendment"]
    assert docs["plan_v2025.htm"]["name"] == "THE EXAMPLE CORPORATION DEFERRED COMPENSATION PLAN"
    e = m.fund_entry({"key": "EXM", "ciks": (1,)}, vs, "FY2024", "2024-12-31")
    assert e["force"]["document"] == "plan_v2023.htm" and e["term"]["ask"] == 'what does its section "Plan Administrator" provide'
    assert "U.S. Benefits" in e["term_gold"]["gold_text"]
    (stale,) = e["term_gold"]["stale_variants"]
    assert "Global Total Rewards" in stale["text"] and stale["role"] == "future"
    assert e["funding_status"]["gold_text"].startswith("The Plan is unfunded") and e["plan_liability"]["status"] == "not_found_by_rule"
    late = m.fund_entry({"key": "EXM", "ciks": (1,)}, vs, "FY2025", "2025-12-31")
    assert "Global Total Rewards" in late["term_gold"]["gold_text"] and late["term_gold"]["stale_variants"][0]["role"] == "superseded"
    assert m.fund_entry({"key": "EXM", "ciks": (1,)}, vs, "FY2022", "2022-12-31") is None     # nothing in force yet
    roles = {d["url"].rsplit("/", 1)[1]: d["role"] for d in m.corpus_documents({"EXM": e}, list(docs.values()))}
    assert roles == {"plan_v2023.htm": "in_force", "plan_v2025.htm": "future", "plan_amendment.htm": "amendment",
                     "other_plan.htm": "unclassified"}


def test_rules_for_first_neo_and_stated_nil():
    assert m.sct_first((FX / "sct.htm").read_text()) == "JANE Q. EXAMPLE"
    text = "The table shows the plan. We do not contribute to the deferred compensation plan. Other text."
    assert m.STATED_NIL["registrant_contributions"].search(text).group(0).strip() == \
        "We do not contribute to the deferred compensation plan."


LIMIT_SECTIONS = json.loads((FX / "limit_sentences.json").read_text())


def limit_sections(key, fy):
    return [s for s in LIMIT_SECTIONS if (s["key"], s["fy"]) == (key, fy)]


@pytest.mark.parametrize("sentence, want", [
    # Apple: the directors' retainer in the same sentence is not a salary or bonus limit
    ("Deferred\nCompensation Plan participants may elect to defer up to 50% of their annual base salary and commissions "
     "and up to 90% of their eligible\ncash bonus, or up to 100% of their annual cash retainer in the case of Non-Employee "
     "Directors, by timely completing a deferral election\nform.", [("salary", "50%"), ("bonus", "90%")]),
    # Microsoft: the limit in parentheses after the component
    ("It allows participants to defer a specified percentage of their base salary (up to 75%) and/or eligible cash "
     "incentives (up to 100%).", [("salary", "75%"), ("bonus", "100%")]),
    # Dow: "maximum deferral of", printed "percent"; the Performance Award is Dow's annual bonus
    ("The EDP allows eligible employees to voluntarily defer the receipt of base salary (maximum deferral of 75 percent) "
     "and Performance Award (maximum deferral of 100 percent).", [("salary", "75 percent"), ("bonus", "100 percent")]),
    # BlackRock: a range gives its maximum
    ("BlackRock maintains the VDCP, which allows participants to elect to defer between 1% and 100% of the Annual Cash "
     "Bonus payable to them that is not mandatorily deferred under another arrangement.", [("bonus", "100%")]),
    # Johnson & Johnson and ConocoPhillips: a list continued after "up to"; PSP awards are not bonus
    ("This plan allows eligible employees to defer up to 50% of their base salary and 100% of their annual incentive.",
     [("salary", "50%"), ("bonus", "100%")]),
    ("The KEDCP permits participants to defer up to 50%\nof base salary, 100% of VCIP and 100% of cash settled PSP awards "
     "for performance periods ending in 2015 or later.", [("salary", "50%"), ("bonus", "100%")]),
    # Chevron: a bulleted list in a table cell; LTIP performance shares ride along with CIP awards
    ("Participants can defer up to: • 90% of CIP awards and LTIP performance share payouts; and • 40% of base salary "
     "above the IRS limit (IRS §401(a)(17)) for payment after retirement or separation from service.",
     [("bonus", "90%"), ("salary", "40%")]),
    # UnitedHealth: the historical limit in parentheses is not cued
    ("Under the plan, employees may defer up to 80% of their eligible annual base salary (100% prior to January 1, 2007) "
     "and up to 100% of their annual cash incentive awards.", [("salary", "80%"), ("bonus", "100%")]),
    # Honeywell: "an additional 25%" of salary on top of the excess portion is not the plan's limit
    ("The Supplemental Savings Plan allows Honeywell executives, including the NEOs, to defer the portion of their annual "
     "base salary that cannot be contributed to the Company’s tax-qualified 401(k) plan due to the annual deferral and "
     "compensation limits imposed by the Internal Revenue Code and/or up to an additional 25% of base annual salary for "
     "the plan year.", []),
    # Target: one percent for salary and bonus together; Walmart: a matching rate, not a deferral
    (TGT_LIMIT, []),
    ("In addition, our company allocates to each participant’s Deferred Compensation Matching Plan account a matching "
     "contribution of up to 6% of the amount by which the participant’s base salary and cash incentive payment exceed the "
     "then-applicable limitation in Section 401(a)(17) of the Internal Revenue Code.", []),
    # Chevron: an election actually made, not a limit
    ("Bonner elected to defer 5% of her CIP award to the DCP, or $60,500;", []),
    # Lilly: no "defer", but "contribute up to" (the plan's executive deferrals); a matching rate is not a limit
    ("The Nonqualified Savings Plan is designed to allow each employee to contribute up to 6 percent of his or her base "
     "salary and receive a company match, beyond IRS contribution limits.", [("salary", "6 percent")]),
    ("Participants may elect to contribute a portion of their base salary to the plan, and the company provides matching "
     "contributions on employees’ contributions up to 6 percent of base salary, subject to IRS limits.", []),
])
def test_limits_in_sentences(sentence, want):
    got = m.limits_in(sentence)
    assert [(f["component"], f["printed"]) for f in got] == want
    assert all(sentence[f["start"]:f["end"]] == f["printed"] and f["value"] == float(f["printed"].split()[0].rstrip("%"))
               for f in got)


@pytest.mark.parametrize("key, fy, plans", [
    ("AAPL", "FY2024", ["Deferred Compensation Plan"] * 2),                              # name broken over two lines
    ("MSFT", "FY2023", ["Microsoft’s Deferred Compensation Plan"] * 2),                  # "It allows": one sentence back
    ("UNH", "FY2023", ["Executive Savings Plan"] * 2),                                   # "Under the plan"
    ("PG", "FY2024", ["Procter & Gamble Company Executive Deferred Compensation Plan"] * 4),   # heading run in; EDCP
    ("COP", "FY2024", ["Key Employee Deferred Compensation Plan"] * 2),                  # KEDCP defined
    ("BLK", "FY2025", ["Voluntary Deferred Compensation Plan"]),                         # VDCP defined in a footnote
    ("DOW", "FY2024", ["Dow Chemical Company Elective Deferral Plan"] * 2),              # "The EDP": one sentence back
    ("CVX", "FY2024", ["Deferred Compensation Plan"] * 2),                               # table row
    ("HON", "FY2025", [None]),                    # after "certain non-qualified deferred compensation plans": no plan
    ("UNH", "FY2022", [None] * 2),                # "Under the plan" with no plan named before it
    # "These plans allow ..." and "Like the EIDP, the DCP ..." bind to no plan; "... under the EIDP" binds to it
    ("JNJ", "FY2024", [None] * 2 + ["Executive Income Deferral Plan"] * 2 + [None] * 2),
    # Lilly: "a nonqualified savings plan" is described, not named, so the Nonqualified Pension Plan named just before
    # is not taken; the next sentence names the Nonqualified Savings Plan
    ("LLY", "FY2025", [None, "Nonqualified Savings Plan"]),
])
def test_proxy_limits_bind_each_limit_to_one_plan(key, fy, plans):
    assert [f["plan"] for f in m.proxy_limits(limit_sections(key, fy))] == plans


def test_past_period_sentence_binds_no_later_year():
    """J&J's 2025 proxy: "In 2023 and in prior years ... under the EIDP" binds the EIDP for FY2023, not for FY2024 or
    later (the DCP took new deferrals from 2024); a qualifier inside parentheses or after the limit does not count."""
    (jnj,) = limit_sections("JNJ", "FY2024")
    bg = [f for f in m.proxy_limits([jnj], "FY2024") if "In 2023 and in prior years" in f["sentence"]]
    assert {f["plan"] for f in bg} == {None} and bg[0]["unbound"] == "past-period qualifier ends 2023, before FY2024"
    assert {f["plan"] for f in m.proxy_limits([jnj], "FY2023") if "In 2023 and in prior years" in f["sentence"]} == {
        "Executive Income Deferral Plan"}
    (unh,) = limit_sections("UNH", "FY2022")                    # "(100% prior to January 1, 2007)"
    assert m.past_until(unh["text"], unh["text"].index("up to 100%")) is None
    cvx = ("DCP The DCP is an unfunded and nonqualified defined contribution plan that permits NEOs to defer up to 90% of "
           "CIP awards, up to 90% of LTIP performance share awards issued prior to 2024, and up to 40% of salary.")
    assert m.past_until(cvx, cvx.index("90%")) is None and m.past_until("Prior to 2024, executives could defer up to 50%", 40) == 2023


def test_limit_plans_merge_names_and_drop_conflicts():
    f = lambda plan, c, v: {"plan": plan, "component": c, "value": v, "printed": f"{v:.0f}%"}
    found = [f("Elective Deferral Plan", "salary", 75), f("Dow Chemical Company Elective Deferral Plan", "bonus", 100),
             f("Deferred Compensation Plan", "salary", 50), f("Executive Deferred Compensation Plan", "salary", 60),
             f("Officer Deferred Compensation Plan", "bonus", 90), f("Executive Deferred Compensation Plan", "salary", 70),
             f(None, "bonus", 100)]
    got = {p["plan"]: ({c: fs[0]["value"] for c, fs in p["limits"].items()}, p["conflicts"]) for p in m.limit_plans(found)}
    assert got == {"Dow Chemical Company Elective Deferral Plan": ({"salary": 75, "bonus": 100}, []),
                   # ends two different names: its own plan
                   "Deferred Compensation Plan": ({"salary": 50}, []),
                   "Officer Deferred Compensation Plan": ({"bonus": 90}, [])}
    # Executive Deferred Compensation Plan: salary stated as 60% and 70%, so not asked


def test_limit_question_gold_facts_and_anchor():
    """Apple FY2024: both components asked under the plan the proxy names; BlackRock: only the bonus. Facts anchor in
    the snapshot text, line breaks and all."""
    fam = next(f for f in m.T.load_families() if f["id"] == "NQDC-LIMIT")
    ent = {"key": "AAPL", "kind": "company", "name": "Apple Inc.", "short": "Apple", "fiscal_years": {"FY2024": "2024-09-28"}}
    p = {"key": "AAPL", "cik": 320193, "accession": "0001308179-25-000008", "document": "a.htm", "url": "u",
         "filed": "2025-01-10", "fye": "2024-09-28", "fy": "FY2024"}
    (plan,) = m.limit_plans(m.proxy_limits(limit_sections("AAPL", "FY2024")))
    (it,) = m.T.plan([fam], [{**ent, "plan_limits": {"FY2024": [m.limit_ask(fam, plan)]}}], m.T.quarters(m.WINDOW))
    q = m.limit_question(it, plan)
    assert q.id == "S1-NQDC-LIMIT-AAPL-MAX-FY2024" and q.text == (
        "According to Apple's proxy statement for fiscal 2024, under the Deferred Compensation Plan, up to what percentage "
        "of base salary and up to what percentage of annual bonus (cash incentive) could participants elect to defer?")
    g = {x["slot"]: x for x in m.gold_limit(q, p, plan)}
    assert (g["salary_deferral_max_pct"]["value"], g["bonus_deferral_max_pct"]["printed"]) == (50.0, "90%")
    assert g["salary_deferral_max_pct"]["scope"] == "plan:Deferred Compensation Plan" and g["bonus_deferral_max_pct"]["unit"] == "percent"
    assert g["salary_deferral_max_pct"]["source"]["locator"] == "aapl-def14a-fy2024-000008-s016"
    facts = m.limit_facts(p, {"name": "Apple Inc."}, [plan])
    doc = {"doc_id": "aapl-def14a-fy2024-000008-s016", "entity": "Apple Inc.", "period": "FY2024",
           "text": limit_sections("AAPL", "FY2024")[0]["text"]}
    for f in facts:
        line = {"fact": m.Fact.model_validate(f), "pattern": f["pattern"], "scale": f["scale"], "period": f["period"]}
        (a,), _ = C.prose_anchors(line, [doc])
        assert doc["text"][a.start:a.end] == f["printed"].rstrip("%")
    (blk,) = m.limit_plans(m.proxy_limits(limit_sections("BLK", "FY2025")))
    ask = m.limit_ask(fam, blk)
    assert ask["plan"] == "the Voluntary Deferred Compensation Plan" and ask["ask"] == fam["asks"]["bonus_deferral_max_pct"]
    q = m.limit_question((fam, {**ent, "key": "BLK", "short": "BlackRock"}, ("FY2024",), {"key": "MAX", **ask}), blk)
    assert [s.slot_id for s in q.slots] == ["bonus_deferral_max_pct"] and q.target_concepts == ["nqdc:BonusDeferralMaxPct"]
    assert m.limit_ask(fam, {"plan": "Intel’s non-qualified deferred compensation plan", "limits": {"salary": []}})["plan"] == \
        "Intel’s non-qualified deferred compensation plan"


@pytest.mark.parametrize("header, field", [
    ("Apple Contributions ($)", "registrant_contributions"),
    ("Intel Contributions in Last Fiscal Year ($)", "registrant_contributions"),
    ("Company Contributions in Last FY", "registrant_contributions"),
    ("Executive Contributions in Last FY ($)", "executive_contributions"),
    ("Aggregate Balance on 6/30/25", "aggregate_balance"),
    ("Beginning Balance", "prior_balance"),
])
def test_company_named_contribution_headers(header, field):
    assert m.field_of(header) == field


def test_na_cells_are_not_applicable_and_stated_nil_columns_accept_zero():
    tables = m.read_tables((FX / "hd_2026_nqdc.htm").read_text(), date(2026, 2, 1), "H")
    rows, cols, marks = tables[0]
    p = proxy(rows=rows, columns=cols) | {"key": "HD", "marks": marks}
    fam = next(f for f in m.T.load_families() if f["id"] == "NQDC-402I")
    q = m.T.make_question(fam, {"key": "HD", "name": "The Home Depot, Inc.", "short": "Home Depot"}, ("FY2025",),
                          {"key": "BROGGI", "neo": "Jordan Broggi"})
    g = {x["slot"]: x for x in m.gold_402i(q, p)}
    assert g["executive_contributions"]["status"] == "not_applicable" and "N/A" in g["executive_contributions"]["accept"]
    assert g["registrant_contributions"]["value"] == 34671
    assert all(f["concept"] != "nqdc:executive_contributions" or f["dimensions"]["executive"] != "Jordan Broggi"
               for f in m.facts(p, {"name": "The Home Depot, Inc."}))       # nothing printed to anchor
    nil = proxy(columns={f: f for f in m.AMOUNT_FIELDS if f != "registrant_contributions"}, rows=rows) | {
        "stated_nil": {"registrant_contributions": "We do not contribute to the deferred compensation plan."}}
    g = {x["slot"]: x for x in m.gold_402i(q, nil)}
    assert g["registrant_contributions"]["accept"] == ["0", "none"] and "do not contribute" in g["registrant_contributions"]["evidence"]
