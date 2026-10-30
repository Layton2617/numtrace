"""Periods in the binding check are compared as dates (harness.scoring.same_period), and a computed claim's inputs
are matched to the slot's inputs by binding, not by the agent's variable names. Fixture: the Elliott 13F-TOP report
of the Opus trial (step1/tests/fixtures/elliott_top3_2023q1_2023q2.json), whose 9 claims all failed binding on
"2023-03-31" vs "2023Q1" before the fix."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from data_layer.harness_adapter import Anchor, DataLayerFactChecker
from harness.claims import align_claims, extract_and_align, slot_of
from harness.periods import calendar, period_of, same_period
from harness.schema import AnswerSlot, Claim, Derivation, Question, Trajectory, Verdict
from harness.scoring import score_claims
from step1.fact_checker import GoldChecker

P, F = Verdict.pass_, Verdict.fail
FIX = json.loads((Path(__file__).parent / "fixtures" / "elliott_top3_2023q1_2023q2.json").read_text())
AAPL = "Apple Inc."      # companies.json: FY2023 ends 2023-09-30; FY2024 runs 2023-10-01..2024-09-28, Q3 ends 2024-06-29


def test_labels_read_as_dates():
    cal = calendar(AAPL)
    assert period_of("2023Q1") == period_of("Q1 2023") == (date(2023, 1, 1), date(2023, 3, 31), True)
    assert period_of("Q3 FY2024", cal) == (date(2024, 3, 31), date(2024, 6, 29), True)
    assert period_of("Q4 FY2024", cal) == (date(2024, 6, 30), date(2024, 9, 28), True)
    assert period_of("9M FY2024", cal) == (date(2023, 10, 1), date(2024, 6, 29), True)
    assert period_of("FY2024", cal) == (date(2023, 10, 1), date(2024, 9, 28), True)
    assert period_of("FY2024") is None and period_of("FY2025 (52 weeks ended 2026-01-31)", cal) is None
    assert calendar("Elliott Investment Management L.P.") is None


@pytest.mark.parametrize("claimed, gold, start, end, ok", [
    ("2023-03-31", "2023Q1", None, date(2023, 3, 31), True),          # 13F report date vs quarter label
    ("2023Q1", "2023Q1", None, date(2023, 3, 31), True),
    ("2023-06-30", "2023Q1", None, date(2023, 3, 31), False),
    ("2023Q2", "2023Q1", None, date(2023, 3, 31), False),
    ("2023-03-31", "2023Q1", None, None, True),                       # gold label without its dates
    ("2024-09-28", "FY2024", None, date(2024, 9, 28), True),          # NQDC: FY label at the fiscal year-end
    ("2024-09-30", "FY2024", None, date(2024, 9, 28), False),
    ("FY2024", "2023-10-01..2024-09-28", date(2023, 10, 1), date(2024, 9, 28), True),
    ("2024-09-28", "FY2024", date(2023, 10, 1), date(2024, 9, 28), False),   # an instant is not the year
    ("Q4 FY2024", "FY2024", date(2023, 10, 1), date(2024, 9, 28), False),    # a quarter is not the year
    ("9M FY2024", "9M FY2024", date(2023, 10, 1), date(2024, 6, 29), True),
    ("Q3 FY2024", "9M FY2024", date(2023, 10, 1), date(2024, 6, 29), False),
    ("2023-10-01..2024-06-29", "9M FY2024", date(2023, 10, 1), date(2024, 6, 29), True),
    ("Q3 FY2024", "Q3 FY2024", None, date(2024, 6, 29), True),          # balance at the quarter's end
])
def test_same_period(claimed, gold, start, end, ok):
    assert same_period(claimed, gold, AAPL, start, end) is ok


# Alignment uses the same definition: a statements claim dated at a fiscal year-end aligns to that FY's slot.
CASH = "us-gaap:CashAndCashEquivalentsAtCarryingValue"
SQ = Question(id="S", filing_type="statement", entity=AAPL, period="FY2023-FY2024", text="Cash?", target_concepts=[],
              slots=[AnswerSlot(slot_id=f"cash_{y}", concept=CASH, entity=AAPL, period=f"FY{y}") for y in (2024, 2023)])


def _claim(i, period):
    return Claim(claim_id=f"t:c{i}", question_id="S", trajectory_id="t", text="", number_text="$1", value=1.0,
                 rel_tol=0.0, entity=AAPL, concept_as_stated=CASH, period=period,
                 derivation=Derivation(kind="reported"))


def test_fiscal_year_end_date_aligns_to_its_fy_slot():
    assert [c.slot_id for c in align_claims([_claim(1, "2023-09-30")], SQ)] == ["cash_2023"]
    assert [c.slot_id for c in align_claims([_claim(1, "2024-09-28")], SQ)] == ["cash_2024"]
    assert slot_of(CASH, SQ, "2023-09-30") == "cash_2023" and slot_of(CASH, SQ, "2024-09-28") == "cash_2024"


def test_wrong_year_date_does_not_align_to_the_fy_slot():
    # 2023-09-30 is FY2023's year-end; before the fix no slot matched the string and it fell to the first, FY2024
    assert align_claims([_claim(1, "2023-09-30")], SQ)[0].slot_id != "cash_2024"
    assert slot_of(CASH, SQ, "2022-09-24") is None and slot_of(CASH, SQ, "2024-09-30") is None


def _score(traj: dict):
    q = Question.model_validate(FIX["question"])
    t = Trajectory.model_validate(traj)
    checker = GoldChecker(DataLayerFactChecker([Anchor.model_validate(a) for a in FIX["anchors"]]), FIX["gold"])
    claims, slots = extract_and_align(t.report, q, t)
    return {s.claim.claim_id.split(":")[1]: s for s in score_claims(claims, q, t, checker)}, slots


def test_elliott_claims_pass():
    scored, slots = _score(FIX["trajectory"])
    assert len(scored) == 9
    for c, s in scored.items():
        v = s.verdicts
        assert (v.value, v.binding, v.support, v.binding_failures) == (P, P, P, []), c
    assert {c: s.claim.slot_id for c, s in scored.items() if s.claim.slot_id} == \
        {"c3": "top1_change", "c6": "top2_change", "c9": "top3_change"}
    assert {s.slot_id: s.status.value for s in slots} == {f"top{i}_change": "answered" for i in (1, 2, 3)}


def _steps(traj, stage):
    return [s for s in traj["steps"] if s["stage"] == stage]


def test_elliott_wrong_period_still_fails():
    traj = copy.deepcopy(FIX["trajectory"])
    f2 = next(f for f in _steps(traj, "extraction")[-1]["output"]["facts"] if f["fact_id"] == "F2")
    assert f2["period"] == "2023-03-31"
    f2["period"] = "2023-06-30"                       # the March 31 row bound to June 30
    v = _score(traj)[0]["c1"].verdicts
    assert (v.value, v.binding, v.support, v.binding_failures) == (P, F, F, ["period"])


def test_elliott_change_from_one_quarter_twice_fails():
    traj = copy.deepcopy(FIX["trajectory"])
    values = next(s for s in _steps(traj, "calculation") if "values" in s["output"])["output"]["values"]
    c1 = next(v for v in values if v["value_id"] == "C1")
    c1["inputs"][1] = copy.deepcopy(c1["inputs"][0]) | {"var": "b"}    # both inputs are the June 30 row
    v = _score(traj)[0]["c3"].verdicts
    assert v.binding == F and "period" in [b.value for b in v.binding_failures] and v.support == F
