import json

import pytest

from step1.reimpl import compare as C
from step1.reimpl.sample_selector import allocate

SAMPLE = {"batches": {
    "statements": {"questions": [{"id": "Q-LTD", "family": "ST-LTD"}]},
    "13f": {"questions": [{"id": "Q-POS", "family": "13F-POS"}]},
    "nqdc": {"questions": [{"id": "Q-402I", "family": "NQDC-402I"}]}}}
GOLD = {
    "statements": [
        {"question_id": "Q-LTD", "slot": "current_portion", "concept": "us-gaap:LongTermDebtCurrent", "period": "FY2024",
         "value": 1_000_000_000.0, "unit": "USD", "status": "reported", "scale": 1e6},
        {"question_id": "Q-LTD", "slot": "total_including_current", "concept": "us-gaap:LongTermDebt", "period": "FY2024",
         "value": 3_000_000_000.0, "unit": "USD", "status": "derived"}],
    "13f": [
        {"question_id": "Q-POS", "slot": "market_value", "concept": "13f:Value[X]", "period": "2022Q3", "value": 5_000.0,
         "unit": "USD", "status": "reported", "scope": "filer", "rows": [{"rule_unit": "USD_thousands"}]},
        {"question_id": "Q-POS", "slot": "as_of_date", "concept": "13f:PeriodOfReport", "period": "2022Q3",
         "value": "2022-09-30", "unit": "date", "status": "reported"}],
    "nqdc": [
        {"question_id": "Q-402I", "slot": "executive_contributions", "concept": "nqdc:ExecutiveContributions",
         "period": "FY2023", "value": None, "unit": "USD", "status": "not_reported", "scope": "NEO:A. B",
         "accept": ["0", "none"]}]}


def row(g, **kw):
    return {"question_id": g["question_id"], "slot": g["slot"], "concept": g["concept"], "period": g["period"],
            "period_end": g.get("period_end"), "unit": g["unit"], "scope": g.get("scope"), "status": g["status"],
            "value": g["value"], "text": g.get("gold_text"), "accept": g.get("accept"),
            "alt_value": C.alt_value(g), "excluded": None, **kw}


@pytest.fixture
def data(tmp_path):
    for b, rows in GOLD.items():
        (tmp_path / b).mkdir()
        (tmp_path / b / "gold.jsonl").write_text("".join(json.dumps(g) + "\n" for g in rows))
    return tmp_path


def test_identical_answers_agree_everywhere(data):
    rows = [row(g) for gs in GOLD.values() for g in gs]
    res = C.compare(SAMPLE, rows, data)
    assert res["diffs"] == [] and res["extra"] == []
    a, n = res["counts"][("*", "*")]["all_fields"]
    assert (a, n) == (5, 5)
    assert res["counts"][("*", "*")]["question_exists"] == [3, 3]


def test_disagreements_are_listed_with_both_values_and_the_printed_unit(data):
    st, f13, nq = GOLD["statements"], GOLD["13f"], GOLD["nqdc"]
    rows = [row(st[0], value=1_000_400_000.0),                   # within one printed unit ($1M)
            row(st[1], concept="us-gaap:DebtAndCapitalLeaseObligations"),
            row(f13[0], value=5.0),                               # thousands not converted: off by more than $1,000
            row(f13[1]),
            row(nq[0], status="reported", value=5.0, excluded="no table")]  # 5 is not on the accept list
    res = C.compare(SAMPLE, rows, data)
    by = {(d["question_id"], d["slot"], d["field"]): d for d in res["diffs"]}
    assert by[("Q-LTD", "current_portion", "value")]["within_one_unit"] is True
    assert by[("Q-LTD", "total_including_current", "concept")]["theirs"] == "us-gaap:DebtAndCapitalLeaseObligations"
    pos = by[("Q-POS", "market_value", "value")]
    assert (pos["ours"], pos["theirs"], pos["within_one_unit"], pos["unit_step"]) == (5000.0, 5.0, False, 1000.0)
    assert by[("Q-402I", "executive_contributions", "status")]["ours"] == "not_reported"
    assert by[("Q-402I", None, "excluded")]["theirs"] == ["no table"]
    c = res["counts"]
    assert c[("statements", "ST-LTD")]["value_exact"] == [1, 2]
    assert c[("statements", "ST-LTD")]["value_unit"] == [2, 2]  # the derived total takes the reported row's $1M step
    assert c[("13f", "*")]["value_unit"] == [1, 2]
    assert c[("nqdc", "NQDC-402I")]["question_exists"] == [0, 1]
    assert "| statements | ST-LTD |" in C.report(res)


def test_missing_rows_count_against_every_field(data):
    res = C.compare(SAMPLE, [row(GOLD["statements"][0])], data)
    assert res["counts"][("13f", "13F-POS")]["status"] == [0, 2]
    assert res["counts"][("13f", "13F-POS")]["question_exists"] == [0, 1]


ALT_SAMPLE = {"batches": {
    "statements": {"questions": [{"id": "Q-DEBT", "family": "ST-DEBT"}, {"id": "Q-EPS", "family": "ST-EPS"}]},
    "13f": {"questions": [{"id": "Q-GRP", "family": "13F-GROUP"}]},
    "nqdc": {"questions": [{"id": "Q-FUND", "family": "NQDC-FUND"}, {"id": "Q-402I", "family": "NQDC-402I"}]}}}
ALT_GOLD = {
    "statements": [
        {"question_id": "Q-DEBT", "slot": "total_debt", "concept": "us-gaap:DebtAndCapitalLeaseObligations",
         "period": "FY2022", "period_end": "2022-12-31", "value": 57.0e9, "unit": "USD", "status": "reported",
         "scale": 1e6, "alt_derivation": {"formula": "a + b", "inputs": [{"value": 5.0e9}, {"value": 52.0e9}]}},
        {"question_id": "Q-EPS", "slot": "diluted_shares",
         "concept": "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "period": "Q3 FY2022",
         "period_end": "2022-09-30", "value": None, "unit": "shares", "status": "not_reported",
         "accept": [None, 4185000000.0]}],
    "13f": [
        {"question_id": "Q-GRP", "slot": "group_shares", "concept": "13f:Shares[X]", "period": "2025Q2", "value": 100,
         "unit": "shares", "status": "derived", "scope": "group:PRU", "accept": [108]},
        {"question_id": "Q-GRP", "slot": "members_counted", "concept": "text", "period": "2025Q2", "value": None,
         "unit": "text", "status": "text", "gold_text": "A: 100 shares", "accept": ["A: 100 shares; B: 8 shares"]}],
    "nqdc": [
        {"question_id": "Q-FUND", "slot": "plan_liability", "concept": "nqdc:PlanLiability", "period": "FY2024",
         "value": None, "unit": "USD", "status": "not_found_by_rule", "scope": "company-wide"},
        {"question_id": "Q-402I", "slot": "aggregate_earnings", "concept": "nqdc:AggregateEarnings",
         "period": "FY2023", "value": 204695.0, "unit": "USD", "status": "reported", "scope": "NEO:A. B",
         "accept": ["204695", "204696"],
         "alt_derivation": {"formula": "sum over plans", "value": 204696.0,
                            "inputs": [{"value": 67289.0}, {"value": 137407.0}, {"value": None}]}}]}


@pytest.fixture
def alt_data(tmp_path):
    for b, rows in ALT_GOLD.items():
        (tmp_path / b).mkdir()
        (tmp_path / b / "gold.jsonl").write_text("".join(json.dumps(g) + "\n" for g in rows))
    return tmp_path


@pytest.mark.parametrize("mode", C.MODES)
def test_our_own_gold_agrees_in_both_modes(alt_data, mode):
    rows = [row(g) for gs in ALT_GOLD.values() for g in gs]
    res = C.compare(ALT_SAMPLE, rows, alt_data, mode)
    assert res["diffs"] == []
    assert res["counts"][("*", "*")]["all_fields"] == [6, 6]


def test_accept_lists_compare_numbers_strings_and_null_alike():
    assert C.norm_accept([None, 4185000000.0]) == C.norm_accept(["4185000000", "none"]) == ["4185000000", "none"]
    assert C.norm_accept(["0", "none"]) == C.norm_accept([0, None])
    assert C.norm_accept(None) is None and C.norm_accept([]) is None


def test_lenient_takes_accepted_and_alternative_readings_strict_does_not(alt_data):
    st, f13, nq = ALT_GOLD["statements"], ALT_GOLD["13f"], ALT_GOLD["nqdc"]
    rows = [row(st[0], status="derived"),                        # the sum behind the printed total, same number
            row(st[1], status="reported", value=4185000000.0),   # the basic count on the accept list
            row(f13[0], value=108),                              # the PGIM-inclusive reading
            row(f13[1], text="A: 100 shares; B: 8 shares"),
            row(nq[0], status="not_reported"),                   # not_found_by_rule is its own status
            row(nq[1], value=204696.0)]                          # the plan rows' sum
    len_ = C.compare(ALT_SAMPLE, rows, alt_data, "lenient")
    strict = C.compare(ALT_SAMPLE, rows, alt_data, "strict")
    fields = lambda res: {(d["question_id"], d["slot"], d["field"]) for d in res["diffs"]}
    assert fields(len_) == {("Q-DEBT", "total_debt", "status"), ("Q-FUND", "plan_liability", "status")}
    assert fields(strict) == {("Q-DEBT", "total_debt", "status"), ("Q-EPS", "diluted_shares", "value"),
                              ("Q-EPS", "diluted_shares", "status"), ("Q-GRP", "group_shares", "value"),
                              ("Q-GRP", "members_counted", "value"), ("Q-FUND", "plan_liability", "status"),
                              ("Q-402I", "aggregate_earnings", "value")}
    with pytest.raises(ValueError):
        C.compare(ALT_SAMPLE, rows, alt_data, "loose")


def test_alt_value_and_period_end_are_compared_fields(alt_data):
    st = ALT_GOLD["statements"]
    rows = [row(st[0], alt_value=None, period_end=None), row(st[1])]
    res = C.compare(ALT_SAMPLE, rows, alt_data, "strict")
    by = {(d["slot"], d["field"]): d for d in res["diffs"]}
    assert by[("total_debt", "alt_value")]["ours"] == 57.0e9
    assert by[("total_debt", "period_end")]["ours"] == "2022-12-31"


def test_wilson_interval():
    lo, hi = C.wilson(8, 10)
    assert round(lo, 4) == 0.4902 and round(hi, 4) == 0.9433
    assert C.wilson(10, 10)[1] == 1.0


def test_allocation_is_even_and_capped():
    assert allocate({"ST-EPS": 60, "ST-Q4": 39, "ST-LTD": 12, "ST-DEBT": 9}, 30) == \
        {"ST-EPS": 8, "ST-Q4": 8, "ST-LTD": 7, "ST-DEBT": 7}
    assert allocate({"A": 2, "B": 50}, 10) == {"A": 2, "B": 8}
