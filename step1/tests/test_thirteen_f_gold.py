"""13F gold, offline. Berkshire's cached EDGAR files trimmed to a few KB (step1/tests/fixtures: six filings in the
submissions JSON, 6-row information tables), Bull Street from data_layer's real fixtures, and small synthetic filings
built here for the amendment merge and the question selection."""
import csv
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from data_layer.thirteen_f import HoldingsStore, load_full_submission, parse_filing, position
from harness.schema import Question
from step1 import thirteen_f_gold as G

FX = Path(__file__).parent / "fixtures"
REAL = Path(__file__).resolve().parents[2] / "data_layer" / "tests" / "fixtures" / "13f" / "real"
BRK = {"key": "BRK", "cik": "1067983", "name": "Berkshire Hathaway Inc", "short": "Berkshire Hathaway",
       "kind": "insurer", "main": "Y", "boundary": "Y"}
ATVI = "00507V109"


@pytest.fixture
def raw(monkeypatch, tmp_path):
    """data/raw/ replaced by a tree with the trimmed Berkshire fixtures."""
    sub = tmp_path / "data.sec.gov" / "submissions"
    sub.mkdir(parents=True)
    shutil.copy(FX / "CIK0001067983.json", sub)
    for d in FX.glob("0000950123-*"):
        shutil.copytree(d, tmp_path / "www.sec.gov" / "Archives" / "edgar" / "data" / "1067983" / d.name.replace("-", ""))
    monkeypatch.setattr(G, "RAW", tmp_path)
    return tmp_path


def filing(acc, filed, period, rows, amendment=None, cik="0009000001"):
    """A synthetic 13F: rows are (cusip, value as filed, shares, putCall, other managers[, class])."""
    amend = (f"<isAmendment>true</isAmendment><amendmentNo>1</amendmentNo><amendmentInfo><amendmentType>{amendment}"
             f"</amendmentType></amendmentInfo>" if amendment else "<isAmendment>false</isAmendment>")
    m, d, y = period[5:7], period[8:], period[:4]
    primary = (f'<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler"><headerData><submissionType>'
               f'{"13F-HR/A" if amendment else "13F-HR"}</submissionType><filerInfo><filer><credentials><cik>{cik}</cik>'
               f'</credentials></filer><periodOfReport>{m}-{d}-{y}</periodOfReport></filerInfo></headerData><formData>'
               f'<coverPage><reportCalendarOrQuarter>{m}-{d}-{y}</reportCalendarOrQuarter>{amend}<filingManager><name>'
               f'Test Manager</name></filingManager><reportType>13F HOLDINGS REPORT</reportType></coverPage><summaryPage>'
               f'<tableEntryTotal>{len(rows)}</tableEntryTotal><tableValueTotal>{sum(r[1] for r in rows)}'
               f'</tableValueTotal></summaryPage></formData></edgarSubmission>')
    info = ('<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">' + "".join(
        f"<infoTable><nameOfIssuer>ISSUER {c}</nameOfIssuer><titleOfClass>{cl[0] if cl else 'COM'}</titleOfClass>"
        f"<cusip>{c}</cusip><value>{v}</value><shrsOrPrnAmt><sshPrnamt>{s}</sshPrnamt><sshPrnamtType>SH"
        f"</sshPrnamtType></shrsOrPrnAmt>" + (f"<putCall>{pc}</putCall>" if pc else "")
        + "<investmentDiscretion>SOLE</investmentDiscretion>" + (f"<otherManager>{om}</otherManager>" if om else "")
        + f"<votingAuthority><Sole>{s}</Sole><Shared>0</Shared><None>0</None></votingAuthority></infoTable>"
        for c, v, s, pc, om, *cl in rows) + "</informationTable>")
    fl = parse_filing(primary, info, accession=acc, filing_date=filed)
    return {"acc": acc, "form": fl.meta.form_type, "filed": filed}, fl.meta, fl


def manager(key, quarters, boundary="", cik="9000001"):
    """A manager dict as load_manager returns it, from {quarter: [filing(...), ...]}."""
    m = {"key": key, "cik": cik, "cik10": cik.zfill(10), "name": f"{key} Capital LP", "short": f"{key} Capital",
         "kind": "hedge", "main": "Y", "boundary": boundary,
         "quarters": {q: G.build_quarter(q, items) for q, items in quarters.items()}}
    m["issuer_keys"] = G.issuer_keys(m)
    return m


def test_filing_list_keeps_13f_in_the_window_filed_by_the_freeze_date(raw, monkeypatch):
    fs, missing = G.filing_list(1067983)
    assert [(f["form"], f["q"], f["filed"]) for f in fs] == [
        ("13F-HR", "2022Q3", "2022-11-14"), ("13F-HR", "2022Q4", "2023-02-14"),
        ("13F-HR/A", "2023Q3", "2023-11-16"), ("13F-HR/A", "2023Q3", "2024-05-15")]
    assert missing == [] and fs[0]["doc"] == "primary_doc.xml"   # the older page ends in 2017
    monkeypatch.setattr(G, "FREEZE", date(2024, 1, 1))
    assert G.filing_list(1067983)[0][-1]["acc"] == "0000950123-23-011029"


def test_boundary_quarters_keep_both_units_and_match_data_layer_positions(raw):
    m = G.load_manager(BRK)
    q3, q4 = m["quarters"]["2022Q3"], m["quarters"]["2022Q4"]
    assert (q3["filings"][0]["rule_unit"], q3["filings"][0]["evidence_unit"]) == ("USD_thousands", "USD_thousands")
    assert (q4["filings"][0]["rule_unit"], q4["filings"][0]["evidence_unit"]) == ("USD", "USD")
    assert not q3["filings"][0]["conflict"] and not q4["filings"][0]["conflict"]
    atvi = q3["positions"][ATVI]
    assert (len(atvi["rows"]), atvi["shares"]) == (3, 25645116 + 1144672 + 33352078)
    assert atvi["value_usd"] == (1906458 + 85095 + 2479393) * 1000 and {r.unit for r in atvi["rows"]} == {"USD_thousands"}
    store = HoldingsStore.from_filings(G.load_filing(1067983, f)[1] for f in G.filing_list(1067983)[0][:2])
    for q, end in (("2022Q3", date(2022, 9, 30)), ("2022Q4", date(2022, 12, 31))):
        for c, p in m["quarters"][q]["positions"].items():
            ref = position(store, "0001067983", c, end)
            assert (p["value_usd"], p["shares"]) == (ref.value_usd, ref.shares)
    assert G.crosses(atvi, q4["positions"][ATVI])
    # 2023Q3: restatement and new-holdings covers are cached, their information tables are not
    q23 = m["quarters"]["2023Q3"]
    assert not q23["complete"] and len(q23["problems"]) == 2 and "information table not in the cache" in q23["problems"][0]
    assert {c["amendment_type"] for c in q23["filings"]} == set() and len(q23["covers"]) == 2


def test_restatement_replaces_new_holdings_adds_and_a_short_restatement_is_contested():
    orig = filing("A-1", "2023-11-14", "2023-09-30", [("AAA", 5000, 100, None, ""), ("BBB", 2000, 100, None, "1"),
                                                      ("BBB", 2000, 100, None, "2"), ("AAA", 900, 100, "Call", "")])
    rs = filing("A-2", "2023-11-16", "2023-09-30", [("AAA", 5000, 100, None, ""), ("BBB", 3000, 150, None, "1"),
                                                    ("BBB", 3000, 150, None, "2")], "RESTATEMENT")
    nh = filing("A-3", "2024-05-15", "2023-09-30", [("CCC", 2500, 50, None, "1")], "NEW HOLDINGS")
    rec = G.build_quarter("2023Q3", [nh, orig, rs])
    assert [s["action"] for s in rec["history"]] == ["original", "restatement_replaced", "new_holdings_added"]
    ps = rec["positions"]
    assert (ps["BBB"]["value_usd"], ps["BBB"]["shares"], len(ps["BBB"]["rows"])) == (6000, 300, 2)
    assert (ps["CCC"]["rows"][0].acc, ps["AAA"]["option_rows"]) == ("A-3", 0)   # the restatement has no call row
    assert rec["complete"] and not rec["contested"] and [p["cusip"] for p in G.ranked(rec)] == ["BBB", "AAA", "CCC"]
    short = filing("A-4", "2023-12-01", "2023-09-30", [("AAA", 5000, 100, None, "")], "RESTATEMENT")
    rec = G.build_quarter("2023Q3", [orig, short])
    assert rec["contested"] and "replaces 4 rows with 1" in rec["problems"][0]
    assert not G.usable(rec, rec["positions"]["AAA"])
    untyped = filing("A-5", "2023-12-01", "2023-09-30", [("AAA", 5000, 100, None, "")])
    untyped[2].meta.is_amendment = True
    assert not G.build_quarter("2023Q3", [orig, untyped])["complete"]
    # NEW HOLDINGS that re-reports the table (same CUSIPs and share counts) is counted twice: contested until restated
    table = [(f"C{i:02d}", 1000 + i, 10, None, "") for i in range(6)]
    again = filing("B-2", "2024-01-10", "2023-09-30", table, "NEW HOLDINGS")
    first = filing("B-1", "2023-11-14", "2023-09-30", table)
    rec = G.build_quarter("2023Q3", [first, again])
    assert rec["contested"] and "re-reports 6 of its 6 rows" in rec["problems"][0] and rec["positions"]["C00"]["shares"] == 20
    fixed = filing("B-3", "2024-02-01", "2023-09-30", table, "RESTATEMENT")
    assert not G.build_quarter("2023Q3", [first, again, fixed])["contested"]


def test_unit_rule_breaker_is_flagged_and_kept_in_the_rule_unit():
    fl = load_full_submission(REAL / "0001790837-23-000002.txt")   # Bull Street: filed 2023-02-28, still thousands
    u = G.filing_units(fl)
    assert (u["rule_unit"], u["evidence_unit"], u["conflict"]) == ("USD", "USD_thousands", True)
    rec = G.build_quarter("2022Q4", [({"acc": fl.meta.accession, "form": "13F-HR", "filed": "2023-02-28"}, fl.meta, fl)])
    p = G.ranked(rec)[0]
    assert p["conflict"] and p["value_usd"] == sum(r.value for r in p["rows"])
    assert p["value_if_evidence"] == 1000 * p["value_usd"] and not G.usable(rec, p)


def test_warrants_give_no_unit_evidence():
    stock = [(f"S{i}", 2000, 100, None, "") for i in range(2)]   # $20 a share in dollars
    warrants = [(f"W{i}", 10, 100, None, "", "*W EXP 12/31/2027") for i in range(3)]   # $0.10: thousands if read alone
    u = G.filing_units(filing("W-1", "2023-05-15", "2023-03-31", stock + warrants)[2])
    assert (u["evidence_unit"], u["rows_usd"], u["rows_thousands"], u["conflict"]) == ("USD", 2, 0, False)


def test_questions_and_gold_for_the_three_families_and_the_boundary_pair(monkeypatch):
    monkeypatch.setattr(G, "PER_MANAGER", 1)
    quarters = {"2022Q3": [filing("X-1", "2022-11-14", "2022-09-30", [("AAA", 50, 1000, None, ""),
                                                                      ("BBB", 20, 500, None, "1"), ("BBB", 20, 500, None, "2")])],
                "2022Q4": [filing("X-2", "2023-02-14", "2022-12-31", [("AAA", 60000, 1000, None, ""), ("CCC", 9000, 100, None, ""),
                                                                      ("BBB", 30000, 600, None, "1"), ("BBB", 25000, 500, None, "2")])],
                "2023Q1": [filing("X-3", "2023-05-15", "2023-03-31", [("AAA", 70000, 1000, None, ""),
                                                                      ("BBB", 20000, 500, None, "1"), ("BBB", 20000, 500, None, "2"),
                                                                      ("CCC", 10000, 100, None, ""), ("CCC", 99, 10, "Put", "")])]}
    m = manager("XCAP", quarters, boundary="Y")
    # a member filer re-reports one shared BBB row (counted once) and holds BBB on its own row too
    member = manager("XMEM", {"2023Q1": [filing("M-1", "2023-05-15", "2023-03-31", [("BBB", 20000, 500, None, "1"),
                                                                                      ("BBB", 5000, 100, None, "")],
                                                cik="0009000002")]}, cik="9000002")
    picks = G.select([m], {"XCAP": [member]})
    got = {fam: (it[0]["id"], it[2], it[3].get("cusip")) for fam, it in picks}
    assert got == {"largest_qoq": ("13F-TOP", ("2022Q4", "2023Q1"), None),   # 2022Q3->Q4 crosses: boundary only
                   "cofiling_group": ("13F-GROUP", ("2023Q1",), "BBB"),
                   "named_issuer": ("13F-POS", got["named_issuer"][1], got["named_issuer"][2]),
                   "boundary": ("13F-UNIT", ("2022Q3", "2022Q4"), got["boundary"][2])}
    gold = {}
    for fam, it in picks:
        q = G.T.make_question(*it)
        assert Question.model_validate_json(q.model_dump_json()) == q and q.family == it[0]["id"]
        rows = {(r.acc, c, r.shares, r.value_usd) for c, p in member["quarters"]["2023Q1"]["positions"].items()
                for r in p["rows"]}
        ok = {"2023Q1": {"0009000002": {"member": True, "rows": "column 7", "direction": "named", "evidence": [],
                                         "tagged": rows}}}
        for g in G.gold_records(m, fam, it, q, [member] if fam == "cofiling_group" else (), ok):
            gold[(fam, g["slot"])] = g
            assert g["question_id"] == q.id and g["proposal_family"] == fam
    top = next(it for fam, it in picks if fam == "largest_qoq")[3]
    assert [top[f"cusip{n}"] for n in (1, 2, 3)] == ["AAA", "BBB", "CCC"]
    assert gold[("largest_qoq", "top2_change")]["value"] == 40000 - 55000 and gold[("largest_qoq", "top1_q1_value")]["value"] == 70000
    assert gold[("largest_qoq", "top_positions")]["gold_text"].startswith("ISSUER AAA COM")
    grp = gold[("cofiling_group", "group_shares")]
    assert (grp["value"], gold[("cofiling_group", "group_value")]["value"]) == (1100, 45000)
    assert len(grp["dropped_as_duplicates"]) == 1 and {m["cik"] for m in grp["members"]} == {"0009000001", "0009000002"}
    # neither cover names the other, so by the membership rule only the filer's own rows count
    it = next(it for fam, it in picks if fam == "cofiling_group")
    ms = G.membership(m, [member])
    assert not ms["2023Q1"]["0009000002"]["member"]
    q = G.T.make_question(*it)
    assert q.text.startswith("Counting XCAP Capital's own 13F reports") and "XMEM" not in q.text   # members never named
    alone = {g["slot"]: g for g in G.gold_records(m, "cofiling_group", it, q, [member], ms)}
    assert (alone["group_shares"]["value"], alone["group_value"]["value"]) == (1000, 40000)
    b = gold[("boundary", "q3_value")]
    assert b["original_unit"] == "USD_thousands" and b["value"] == b["original_value"] * 1000
    assert gold[("boundary", "q4_value")]["original_unit"] == "USD"
    assert gold[("boundary", "change")]["value"] == gold[("boundary", "q4_value")]["value"] - b["value"]


def test_boundary_skips_filer_unit_errors_and_pairs_that_do_not_cross():
    rule_breaker = manager("BRKR", {"2022Q3": [filing("Y-1", "2022-11-14", "2022-09-30", [("AAA", 5, 100, None, "")])],
                                    "2022Q4": [filing("Y-2", "2023-02-14", "2022-12-31", [("AAA", 6, 100, None, "")])]},
                           boundary="Y")   # 2022Q4 filed in dollars-era but still in thousands: $0.06 a share
    late = manager("LATE", {"2022Q3": [filing("Z-1", "2023-03-23", "2022-09-30", [("AAA", 50000, 1000, None, "")])],
                            "2022Q4": [filing("Z-2", "2023-02-15", "2022-12-31", [("AAA", 60000, 1000, None, "")])]},
                   boundary="Y")
    for m in (rule_breaker, late):
        m["main"] = ""
        assert G.select([m], {}) == []


def test_cofiling_groups_from_cover_pages(raw, tmp_path):
    m = G.load_manager(BRK)
    m["quarters"]["2022Q4"]["covers"][0]["reported_by"].append(   # a self-reference (Temasek's 2025Q1 13F-NT)
        {"name": "Berkshire Hathaway Inc", "cik": "0001067983", "form13f_file_number": "28-4545"})
    g, = G.cofiling_groups([m], tmp_path / "none.csv")
    assert g["members"] == "0001067983" and "New England Asset Management Inc (28-5194)" in g["note"]
    assert g["report_types"] == "13F COMBINATION REPORT: 2; 13F HOLDINGS REPORT: 2"
    assert "Buffett Warren E [28-" in g["other_included_managers"]
    m["quarters"]["2022Q4"]["covers"][0]["reported_by"].append({"name": "Other", "cik": "0000000042",
                                                                 "form13f_file_number": None})
    assert G.cofiling_groups([m], tmp_path / "none.csv")[0]["members"] == "0001067983;0000000042"


def test_facts_carry_both_units_and_mark_summed_positions(raw):
    m = G.load_manager(BRK)
    fs = {(f.concept, f.dimensions.get("cusip")): f for f in G.quarter_facts(m, "2022Q3", {ATVI}, totals=True)}
    tv = fs[("13f:TableValueTotal", None)]
    assert (tv.value, tv.original_value, tv.original_unit) == (296096640 * 1000, 296096640, "USD_thousands")
    assert fs[("13f:TableEntryTotal", None)].value == 179
    pv = fs[("13f:PositionValue", ATVI)]
    assert pv.derived and pv.derivation == "sum of 3 rows" and pv.label == f"ACTIVISION BLIZZARD INC COM (CUSIP {ATVI})"
    assert pv.value == pv.original_value * 1000 and pv.accession == "0000950123-22-012275"


def test_main_writes_questions_gold_facts_and_readme(raw, tmp_path, monkeypatch):
    sel = tmp_path / "selected.csv"
    with sel.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(BRK))
        w.writeheader()
        w.writerow(BRK)
    out = tmp_path / "out"
    monkeypatch.setattr(G, "SELECTED", sel)
    monkeypatch.setattr(G, "OUT", out)
    monkeypatch.setattr(G, "GROUPS", tmp_path / "groups.csv")
    G.main([])
    qs = [Question.model_validate_json(l) for l in (out / "questions.jsonl").read_text().splitlines()]
    gold = [json.loads(l) for l in (out / "gold.jsonl").read_text().splitlines()]
    assert {g["question_id"] for g in gold} == {q.id for q in qs} and "S1-13F-UNIT-BRK-ACTIVISION-2022Q3-2022Q4" in {q.id for q in qs}
    assert all(g["value"] is not None for g in gold)
    assert (out / "facts.jsonl").read_text() and "## Question families" in (out / "README.md").read_text()
    assert next(csv.DictReader((tmp_path / "groups.csv").open()))["group_id"] == "BRK"


def _om(name, cik=None, fn=None, seq=None):
    return {"name": name, "cik": cik, "form13f_file_number": fn, "sequence_number": seq}


def _mgr(cik, name, q, covers, rows=()):
    rs = [G.Row(acc, "2024-05-01", v, "USD", float(v), sh, "SH", others, "DFND", "USD") for acc, v, sh, others in rows]
    return {"key": name, "cik": str(int(cik)), "cik10": cik, "name": name, "short": name,
            "quarters": {q: {"covers": covers, "positions": {"X": {"rows": rs, "conflict": False}} if rs else {}}}}


def test_group_membership_needs_the_filers_direction_and_the_members_confirmation(raw):
    q = "2024Q1"
    cov = lambda acc, rep=(), inc=(): {"accession": acc, "reported_by": list(rep), "other_included_managers": list(inc)}
    parent = _mgr("0000000001", "Parent", q, [cov("p", rep=[_om("Sub A", "0000000002"), _om("Adviser", fn="028-3"),
                                                          _om("Sub D", "0000000004")])])
    a = _mgr("0000000002", "Sub A", q, [cov("a", inc=[_om("PARENT, INC.", fn="028-1", seq=1)])], [("a", 10, 1, "1")])
    adviser = _mgr("0000000003", "Adviser", q, [cov("b", inc=[_om("Someone", fn="028-9", seq=1)])],
                   [("b", 20, 2, "1"), ("b", 30, 3, "")])
    rev = _mgr("0000000005", "Holdco", q, [cov("r", rep=[_om("Parent", "0000000001")])], [("r", 40, 4, "")])
    shared = _mgr("0000000004", "Sub D", q, [cov("d", inc=[_om("Parent", "0000000001", seq=7)])],
                  [("d", 50, 5, "7"), ("d", 60, 6, "")])
    # the parent's own file number is only known through a cover pairing it with the CIK
    parent["quarters"][q]["covers"][0]["other_included_managers"].append(_om("Parent, Inc.", "0000000001", "028-1"))
    parent["quarters"][q]["covers"][0]["reported_by"].append(_om("Sub E", "0000000006"))
    parent["quarters"][q]["covers"][0]["other_included_managers"].append(_om("Parent Trust Co", fn="028-77"))
    tagged = _mgr("0000000006", "Sub E", q, [cov("e", inc=[_om("Parent Trust Co", fn="28-77", seq=3)])],
                  [("e", 70, 7, "3"), ("e", 80, 8, "")])
    ms = G.membership(parent, [a, adviser, rev, shared, tagged])[q]
    assert ms["0000000006"]["member"] and ms["0000000006"]["tagged"] == {("e", "X", 7, 70.0)}   # Column 7 rows only
    # named by the parent, silent this quarter, but a 13F-NT in another quarter names the parent: an accepted
    # alternative reading (all its rows), not a member; one that never names the parent is neither
    parent["quarters"][q]["covers"][0]["reported_by"].append(_om("Sub F", "0000000007"))
    alt = _mgr("0000000007", "Sub F", q, [cov("f")], [("f", 90, 9, "")])
    alt["quarters"]["2023Q4"] = {"covers": [cov("n", rep=[_om("Parent", "0000000001")])], "positions": {}}
    ms = G.membership(parent, [a, adviser, rev, shared, tagged, alt])[q]
    assert not ms["0000000007"]["member"] and ms["0000000007"]["alternative"]
    assert not ms["0000000003"].get("alternative")
    assert ms["0000000002"]["member"] and ms["0000000002"]["tagged"] == {("a", "X", 1, 10.0)}  # name + file number
    assert not ms["0000000003"]["member"]        # named by the parent, but never names it nor tags a row with it
    assert not ms["0000000005"]["member"] and "reverse" in ms["0000000005"]["evidence"][0]
    # a confirming member's row without a Column 7 link to the group does not count (60 of Sub D's rows)
    assert ms["0000000004"]["member"] and ms["0000000004"]["tagged"] == {("d", "X", 5, 50.0)}
    assert not ms["0000000004"]["unresolved"]
    # Column 7 numbers its own filing does not list: the position is ambiguous and not asked
    odd = _mgr("0000000008", "Sub G", q, [cov("g", inc=[_om("Parent", "0000000001", seq=1)])],
               [("g", 11, 1, "1"), ("g", 12, 2, "1,2")])
    parent["quarters"][q]["covers"][0]["reported_by"].append(_om("Sub G", "0000000008"))
    ms2 = G.membership(parent, [odd])
    assert ms2[q]["0000000008"]["unresolved"] == {"X"} and G.ambiguous(ms2, q, "X") and not G.ambiguous(ms2, q, "Y")


def test_an_ambiguous_group_pick_is_replaced_by_the_managers_next_item_only():
    fam, ent = {"id": "13F-GROUP"}, {"key": "M"}
    item = lambda q, c: (fam, ent, (q,), {"cusip": c, "key": c})
    other = ({"id": "13F-GROUP"}, {"key": "N"}, ("2024Q1",), {"cusip": "Z", "key": "Z"})
    tiers = [[item("2024Q1", "A"), item("2024Q2", "B"), other], [item("2024Q3", "C")]]
    ms = {"M": {"2024Q1": {"x": {"member": True, "unresolved": {"A"}}}}}
    out = G.replace_ambiguous([item("2024Q1", "A"), other], tiers, ms)
    assert out[1] is other and out[0][2][0] != "2024Q1" and out[0][3]["cusip"] in ("B", "C")
    assert G.replace_ambiguous([other], tiers, ms) == [other]
