"""Step 1 statements: calendars, concept choice, restatements and gold on a trimmed companyfacts file (offline)."""
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from data_layer.statements import load_companyfacts
from harness.schema import Question
from step1 import statements as S
from step1 import templates as T

K23, K24 = "0000000001-24-000001", "0000000001-25-000001"
Q3_23, Q1_24, Q3_24, Q1_25 = "0000000001-23-000003", "0000000001-24-000002", "0000000001-24-000003", "0000000001-25-000002"
FILED = {K23: ("10-K", 2023, "FY", "2024-02-15"), K24: ("10-K", 2024, "FY", "2025-02-15"),
         Q3_23: ("10-Q", 2023, "Q3", "2023-11-01"), Q1_24: ("10-Q", 2024, "Q1", "2024-05-01"),
         Q3_24: ("10-Q", 2024, "Q3", "2024-11-01"), Q1_25: ("10-Q", 2025, "Q1", "2025-05-01")}
M = 1e6
FY23, FY24, FY22 = ("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31"), ("2022-01-01", "2022-12-31")
# (concept, unit, (start, end) or end, value, accession)
ROWS = [
    ("NetCashProvidedByUsedInOperatingActivities", "USD", FY23, 1000 * M, K23),
    ("NetCashProvidedByUsedInOperatingActivities", "USD", FY22, 900 * M, K23),
    ("NetCashProvidedByUsedInOperatingActivities", "USD", ("2023-01-01", "2023-09-30"), 700 * M, Q3_23),
    ("NetCashProvidedByUsedInOperatingActivities", "USD", FY23, 1100 * M, K24),     # restated ...
    ("NetCashProvidedByUsedInOperatingActivities", "USD", ("2023-01-01", "2023-09-30"), 770 * M, Q3_24),  # ... both
    ("NetCashProvidedByUsedInOperatingActivities", "USD", FY24, 1200 * M, K24),
    ("NetCashProvidedByUsedInOperatingActivities", "USD", ("2024-01-01", "2024-09-30"), 800 * M, Q3_24),
    ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", FY23, 300 * M, K23),
    ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", ("2023-01-01", "2023-09-30"), 200 * M, Q3_23),
    ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", FY23, 280 * M, K24),       # restated, nine months not
    ("PaymentsToAcquirePropertyPlantAndEquipment", "USD", ("2023-01-01", "2023-09-30"), 200 * M, Q3_24),
    ("LongTermDebtCurrent", "USD", "2023-12-31", 100 * M, K23),
    ("LongTermDebtNoncurrent", "USD", "2023-12-31", 900 * M, K23),
    ("LongTermDebt", "USD", "2023-12-31", 1000 * M, K23),
    ("LongTermDebtCurrent", "USD", "2023-12-31", 120 * M, K24),                       # restated comparative
    ("LongTermDebtNoncurrent", "USD", "2023-12-31", 900 * M, K24),
    ("LongTermDebtCurrent", "USD", "2024-12-31", 50 * M, K24),
    ("LongTermDebtNoncurrent", "USD", "2024-12-31", 800 * M, K24),
    ("LongTermDebt", "USD", "2024-12-31", 870 * M, K24),                              # not current + noncurrent
    ("EarningsPerShareDiluted", "USD/shares", ("2023-07-01", "2023-09-30"), 1.50, Q3_23),
    ("EarningsPerShareDiluted", "USD/shares", ("2022-07-01", "2022-09-30"), 1.20, Q3_23),
    ("EarningsPerShareDiluted", "USD/shares", ("2023-01-01", "2023-09-30"), 4.00, Q3_23),
    ("EarningsPerShareBasic", "USD/shares", ("2023-07-01", "2023-09-30"), 1.55, Q3_23),
    ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", ("2023-07-01", "2023-09-30"), 100 * M, Q3_23),
    ("EarningsPerShareDiluted", "USD/shares", ("2024-07-01", "2024-09-30"), 2.00, Q3_24),
    ("EarningsPerShareDiluted", "USD/shares", ("2023-07-01", "2023-09-30"), 0.75, Q3_24),  # 2:1 split
    ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", ("2024-07-01", "2024-09-30"), 200 * M, Q3_24),
    ("EarningsPerShareDiluted", "USD/shares", ("2024-01-01", "2024-03-31"), 1.00, Q1_24),
    ("EarningsPerShareDiluted", "USD/shares", ("2023-01-01", "2023-03-31"), 0.40, Q1_24),
    ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", ("2024-01-01", "2024-03-31"), 200 * M, Q1_24),
    ("EarningsPerShareDiluted", "USD/shares", ("2025-01-01", "2025-03-31"), 1.10, Q1_25),
    ("EarningsPerShareDiluted", "USD/shares", ("2024-01-01", "2024-03-31"), 0.90, Q1_25),  # Q1 2024 re-presented
    ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares", ("2025-01-01", "2025-03-31"), 201 * M, Q1_25),
]


def companyfacts(rows=ROWS, cik=1):
    gaap = {}
    for concept, unit, period, val, acc in rows:
        form, fy, fp, filed = FILED[acc]
        start, end = period if isinstance(period, tuple) else (None, period)
        item = {"end": end, "val": val, "accn": acc, "fy": fy, "fp": fp, "form": form, "filed": filed,
                **({"start": start} if start else {})}
        gaap.setdefault(concept, {"label": concept, "units": {}})["units"].setdefault(unit, []).append(item)
    return {"cik": cik, "entityName": "ACME", "facts": {"us-gaap": gaap}}


@pytest.fixture(scope="module")
def acme():
    return S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts()))


def cand(c, fam, point, metric=None):
    return next(r for f, p, m, r in c["candidates"] if (f, p, m) == (fam, point, metric))


def test_calendar_labels_follow_the_companys_own_fiscal_year_naming(acme):
    cal = acme["calendar"]
    assert list(cal["years"]) == ["FY2023", "FY2024"] and cal["years"]["FY2024"]["filings"][0][1] == K24
    assert {q: v["end"].isoformat() for q, v in cal["quarters"].items()} == {
        "FY2023Q3": "2023-09-30", "FY2024Q1": "2024-03-31", "FY2024Q3": "2024-09-30", "FY2025Q1": "2025-03-31"}

    def year(fy_field, filings=1):  # a January year-end, as NVIDIA (FY = end year) or Home Depot (FY = start year)
        rows = [{"start": "2023-01-30", "end": "2024-01-28", "val": 1.0, "accn": f"a-{i}", "fy": fy_field, "fp": "FY",
                 "form": "10-K", "filed": "2024-03-01"} for i in range(filings)]
        rows.append({"start": "2023-01-30", "end": "2024-01-28", "val": 1.0, "accn": "wrong", "fy": 2019, "fp": "FY",
                     "form": "10-K", "filed": "2024-03-01"})   # a wrong fiscal-year field (FCX) is outvoted
        data = {"cik": 2, "facts": {"us-gaap": {"Revenues": {"label": "R", "units": {"USD": rows}}}}}
        return list(S.calendar(load_companyfacts(data))["years"])
    assert year(2024, 2) == ["FY2024"] and year(2023, 2) == ["FY2023"]


def test_debt_restated_comparative_is_gold_and_the_original_is_kept(acme):
    r = cand(acme, "ST-LTD", "FY2023")
    cur, non, tot = r["slots"]
    assert (cur["value"], cur["source"]["accession"], cur["original"]["value"]) == (120 * M, K24, 100 * M)
    assert non["source"]["accession"] == K23 and "original" not in non     # unchanged: sourced to the 10-K of the year
    assert (tot["status"], tot["value"], tot["concept"]) == ("derived", 1020 * M, "us-gaap:LongTermDebt")
    assert [i["slot"] for i in tot["inputs"]] == ["current_portion", "noncurrent"] and r["concepts"] == {}
    # a tagged total that is not current + noncurrent is a neighbouring line, and the slot is left to a human
    assert cand(acme, "ST-LTD", "FY2024")["status"].endswith(": manual")
    assert cand(acme, "ST-DEBT", "FY2023")["status"] == "not_found_by_rule: no debt-due-within-one-year line tagged"


def test_q4_uses_one_vintage_and_skips_mixed_restatements(acme):
    r = cand(acme, "ST-Q4", "FY2023", "ocf")
    fy, nine, q4 = r["slots"]
    assert (fy["value"], nine["value"], q4["value"]) == (1100 * M, 770 * M, 330 * M)
    assert (fy["original"]["value"], nine["original"]["value"]) == (1000 * M, 700 * M)
    assert (nine["period"], q4["period"], q4["formula"]) == ("9M FY2023", "Q4 FY2023", "b - a")
    assert (nine["period_start"], nine["period_end"]) == (date(2023, 1, 1), date(2023, 9, 30))
    assert (q4["period_start"], q4["period_end"]) == (date(2023, 10, 1), date(2023, 12, 31))
    assert "no single basis" in cand(acme, "ST-Q4", "FY2023", "capex")["status"]
    assert cand(acme, "ST-Q4", "FY2024", "ocf")["slots"][2]["value"] == 400 * M   # no later vintage: as first filed


def test_eps_quarter_keeps_the_split_adjusted_comparative_and_skips_requarters(acme):
    r = cand(acme, "ST-EPS", "FY2024Q3")
    eps, prior, change, shares = r["slots"]
    assert (eps["period"], prior["period"], change["period"]) == ("Q3 FY2024", "Q3 FY2023", "Q3 FY2023-Q3 FY2024")
    assert (prior["value"], prior["source"]["accession"], prior["original"]["value"]) == (0.75, Q3_24, 1.50)
    assert change["value"] == pytest.approx(1.25) and (shares["value"], shares["printed"]) == (200 * M, "200")
    # a quarter a later filing re-presents (split, restatement) has no single basis
    assert "re-presented later" in cand(acme, "ST-EPS", "FY2023Q3")["status"]
    assert "re-presented later" in cand(acme, "ST-EPS", "FY2024Q1")["status"]
    # the share slot is dropped only for a company that never tags a diluted count
    bare = [r for r in ROWS if "Shares" not in r[0]]
    c = S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts(bare)))
    r = cand(c, "ST-EPS", "FY2024Q3")
    assert r["drop"] == ["diluted_shares"] and r["slots"][3]["status"] == "not_found_by_rule"
    some = [r for r in ROWS if not ("Shares" in r[0] and r[4] == Q1_25)]
    c = S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts(some)))
    assert cand(c, "ST-EPS", "FY2025Q1")["status"] == "not_found_by_rule: diluted shares not tagged this quarter"


def test_period_labels_separate_quarter_and_year_to_date(acme):
    lab = {(f.concept[8:], f.period_start, f.accession): S.period_label(f, acme["calendar"]) for f in acme["book"].facts}
    assert lab[("EarningsPerShareDiluted", date(2023, 7, 1), Q3_23)] == "Q3 FY2023"
    assert lab[("EarningsPerShareDiluted", date(2023, 1, 1), Q3_23)] == "9M FY2023"
    assert lab[("NetCashProvidedByUsedInOperatingActivities", date(2023, 1, 1), Q3_23)] == "9M FY2023"  # not "Q3"
    assert lab[("EarningsPerShareDiluted", date(2022, 7, 1), Q3_23)] == "2022-07-01..2022-09-30"


def test_build_four_questions_per_company_with_gold_for_every_slot(acme):
    res = S.build([acme])
    qs = res["questions"]
    assert Counter(q.family for q in qs) == {"ST-LTD": 1, "ST-Q4": 1, "ST-EPS": 2}
    assert {q.id for q in qs} == {"S1-ST-LTD-ACME-FY2023", "S1-ST-Q4-ACME-OCF-FY2024", "S1-ST-EPS-ACME-FY2024Q3",
                                  "S1-ST-EPS-ACME-FY2025Q1"}
    gold = {(g["question_id"], g["slot"]): g for g in res["gold"]}
    for q in qs:
        assert Question.model_validate_json(q.model_dump_json()) == q
        for s in q.slots:
            g = gold[(q.id, s.slot_id)]
            assert (g["concept"], g["period"], g["entity"]) == (s.concept, s.period, q.entity) and g["value"] is not None
    eps = next(q for q in qs if q.id.endswith("FY2024Q3"))
    assert eps.text.startswith("What were Acme's diluted earnings per share for the third quarter of fiscal 2024") \
        and "same quarter of fiscal 2023" in eps.text
    facts = res["facts"]
    assert {f["accession"] for f in facts} == set(FILED)   # sources and the originals of restated values
    assert all(f["entity_name"] == "Acme Corp." for f in facts)


def test_templates_take_fiscal_quarters_and_per_period_concepts():
    fams = {f["id"]: f for f in T.load_families()}
    ent = {"key": "KO", "kind": "company", "name": "The Coca-Cola Company", "short": "Coca-Cola",
           "fiscal_years": {"FY2023": "2023-12-31", "FY2024": "2024-12-31"},
           "concepts_by_period": {"FY2024": {"us-gaap:LongTermDebtCurrent": "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent"}}}
    q23, q24 = (T.make_question(fams["ST-LTD"], ent, (fy,), {}) for fy in ("FY2023", "FY2024"))
    assert q23.slots[0].concept == "us-gaap:LongTermDebtCurrent"
    assert q24.slots[0].concept == "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent"
    # without "fiscal_quarters", Q1-Q3 are placed from the year-ends (Target: FY2025 ends 31 January 2026)
    tgt = {"fiscal_years": {"FY2024": "2025-02-01", "FY2025": "2026-01-31"}}
    assert T.fiscal_quarters(tgt, T.quarters("2025Q2:2025Q4")) == ["FY2025Q1", "FY2025Q2", "FY2025Q3"]


OUT = S.OUT


@pytest.mark.skipif(not (OUT / "questions.jsonl").exists(), reason="step1/data/statements not built")
def test_built_set_has_120_questions_4_per_company_and_gold_for_every_slot():
    qs = [Question.model_validate_json(l) for l in (OUT / "questions.jsonl").read_text().splitlines()]
    gold = {(g["question_id"], g["slot"]): g for g in map(json.loads, (OUT / "gold.jsonl").read_text().splitlines())}
    assert len(qs) == 120 and set(Counter(q.entity for q in qs).values()) == {4}
    assert Counter(q.family == "ST-EPS" for q in qs) == {True: 60, False: 60}
    assert all(gold[(q.id, s.slot_id)]["value"] is not None for q in qs for s in q.slots)


def test_debt_rules_from_tags_alone():
    def debt(rows):
        c = S.company("X", "X Inc.", "X", [1], facts=load_companyfacts(companyfacts(rows)))
        return cand(c, "ST-DEBT", "FY2023"), cand(c, "ST-LTD", "FY2023")
    base = [r for r in ROWS if r[0] not in ("LongTermDebtCurrent", "LongTermDebt") or r[2] != "2023-12-31"]
    sb = ("ShortTermBorrowings", "USD", "2023-12-31", 100 * M, K23)
    # Netflix: no DebtCurrent, no current-portion tag, and a debt total = ShortTermBorrowings + noncurrent
    d, _ = debt(base + [sb, ("NotesPayable", "USD", "2023-12-31", 1000 * M, K23)])
    assert d["status"] == "ok" and d["concepts"]["us-gaap:DebtCurrent"] == "us-gaap:ShortTermBorrowings"
    # Caterpillar: the same two lines but no total that ties them
    assert debt(base + [sb])[0]["status"] == "not_found_by_rule: no debt-due-within-one-year line tagged"
    # Chevron: the noncurrent line equals a total "including current maturities", so the split is not current + noncurrent
    cvx = [("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "USD", "2023-12-31", 900 * M, K23)]
    d, l = debt(ROWS + cvx)
    assert "split inconsistent" in l["status"]


def test_capex_netted_later_is_a_presentation_change_and_asks_the_net_measure():
    # first filed gross 300 / 200 with proceeds 20 / 10 beside them; the next vintage tags 280 / 190 with the same concept
    capex = "PaymentsToAcquirePropertyPlantAndEquipment"
    rows = [r for r in ROWS if r[0] != capex] + [
        (capex, "USD", FY23, 300 * M, K23), ("ProceedsFromSaleOfPropertyPlantAndEquipment", "USD", FY23, 20 * M, K23),
        (capex, "USD", ("2023-01-01", "2023-09-30"), 200 * M, Q3_23),
        ("ProceedsFromSaleOfPropertyPlantAndEquipment", "USD", ("2023-01-01", "2023-09-30"), 10 * M, Q3_23),
        (capex, "USD", FY23, 280 * M, K24), (capex, "USD", ("2023-01-01", "2023-09-30"), 190 * M, Q3_24)]
    c = S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts(rows)))
    r = cand(c, "ST-Q4", "FY2023", "capex")
    fy, nine, q4 = r["slots"]
    assert r["netted"] and q4["value"] == 90 * M
    assert fy["notes"].startswith("not a restatement") and fy["presentation_change"]["proceeds_value"] == 20 * M
    assert nine["original"]["basis"].startswith("gross")
    assert S.capex_basis(r, {})["basis"] == "net of proceeds"
    # a gross line becomes net only by the rule's evidence: a row label saying so
    g = cand(S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts())), "ST-Q4", "FY2024", "ocf")
    key = lambda s: (s["source"]["accession"], s["concept"], str(s["period_start"]), str(s["period_end"]))
    assert S.capex_basis(g, {})["basis"] == "gross"
    assert S.capex_basis(g, {key(g["slots"][0]): ["Purchases of property, net of sales, 2024"]})["basis"] == "net of proceeds"


def test_missing_diluted_count_keeps_the_basic_count_as_evidence_only():
    bare = [r for r in ROWS if "Shares" not in r[0]] + [
        ("WeightedAverageNumberOfSharesOutstandingBasic", "shares", ("2024-07-01", "2024-09-30"), 199 * M, Q3_24)]
    c = S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts(bare)))
    sh = cand(c, "ST-EPS", "FY2024Q3")["slots"][3]
    assert (sh["status"], sh["value"], sh["basic_count"]["value"], sh["source"]["accession"]) == \
        ("not_reported", None, 199 * M, Q3_24)
    assert sh["accept"] == [None, 199 * M] and str(sh["period_end"]) == "2024-09-30"


def test_a_tagged_total_is_the_gold_and_the_sum_an_accepted_path():
    rows = [r for r in ROWS if not (r[2] == "2023-12-31" and r[4] == K24)]   # FY2023 debt as first filed only
    c = S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts(rows)))
    cur, non, tot = cand(c, "ST-LTD", "FY2023")["slots"]
    assert (tot["status"], tot["value"], tot["concept"], tot["source"]["accession"]) == \
        ("reported", 1000 * M, "us-gaap:LongTermDebt", K23)
    assert tot["alt_derivation"]["formula"] == "a + b" and [i["slot"] for i in tot["alt_derivation"]["inputs"]] == \
        ["current_portion", "noncurrent"]
    # a sum with no tagged total stays derived and takes the balance date of its inputs
    # (the default fixture restates the current portion in the FY2024 10-K, which does not re-tag the total)
    d = cand(S.company("ACME", "Acme Corp.", "Acme", [1], facts=load_companyfacts(companyfacts())), "ST-LTD", "FY2023")
    assert d["slots"][2]["status"] == "derived" and str(d["slots"][2]["period_end"]) == "2023-12-31"
    q4 = cand(c, "ST-Q4", "FY2024", "ocf")["slots"][2]      # inputs over different periods keep their own dates
    assert (str(q4["period_start"]), str(q4["period_end"])) == ("2024-10-01", "2024-12-31")


def test_equality_is_at_the_coarser_reported_precision():
    # Netflix FY2025: 998,865 + 13,463,971 thousand (decimals -3) against NotesPayable 14,463 million (-6)
    assert S.agree(998_865e3 + 13_463_971e3, -3, 14_463e6, -6)
    assert not S.agree(998_865e3 + 13_463_971e3, -3, 14_463e6, -3)          # at thousands they differ
    assert S.agree(4_752.5e6, -5, 4_753e6, -6)                                # half rounds up, not to even
    assert not S.agree(7_084.4e6, -5, 7_585.7e6, -5) and S.agree(1.0, S.EXACT, 1.0, S.EXACT)
