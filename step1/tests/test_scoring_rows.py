"""A computed 13F claim over row-level inputs, for a slot whose gold sums rows that carry no anchored fact
(step1.fact_checker.GoldChecker._gold_rows). Fixture: S1-13F-UNIT-AMP-JPMORGAN-46625H100-2022Q3-2022Q4 from the k=5
calibration run: "rose by about $602.80 million" = (h+...+n) - (a+...+g), 7 rows per quarter, the 2022Q3 filing in
thousands. Before the fix it scored value pass, binding pass, support fail."""
import copy
import json
from pathlib import Path

from data_layer.harness_adapter import DataLayerFactChecker
from harness.claims import extract_and_align
from harness.schema import Question, Trajectory, Verdict
from harness.scoring import score_claims
from step1.fact_checker import GoldChecker

P, F = Verdict.pass_, Verdict.fail
FIX = json.loads((Path(__file__).parent / "fixtures" / "amp_jpmorgan_unit_2022q3_2022q4.json").read_text())


def _change(traj: dict):
    q = Question.model_validate(FIX["question"])
    t = Trajectory.model_validate(traj)
    claims, _ = extract_and_align(t.report, q, t)
    scored = score_claims(claims, q, t, GoldChecker(DataLayerFactChecker([], []), FIX["gold"]))
    return next(s for s in scored if s.claim.slot_id == "change")


def _inputs(traj: dict) -> list[dict]:
    step = next(s for s in traj["steps"] if s["stage"] == "calculation" and "values" in s["output"])
    return next(v for v in step["output"]["values"] if v["slot_id"] == "change")


def test_row_inputs_support_the_change():
    s = _change(FIX["trajectory"])
    assert s.claim.value == 602_800_000 and len(s.claim.derivation.inputs) == 14
    v = s.verdicts
    assert (v.value, v.binding, v.support, v.binding_failures) == (P, P, P, [])
    assert "14 13F rows" in s.fact_check.evidence[-1]


def _mutated(edit) -> Verdict:
    traj = copy.deepcopy(FIX["trajectory"])
    edit(_inputs(traj))
    return _change(traj).verdicts


def test_input_not_printed_in_its_span_fails():
    def edit(v):
        v["inputs"][0]["printed_value"] = 1841036.0          # the quote prints 1,841,035
    assert _mutated(edit).support == F


def test_input_from_the_other_quarter_filing_fails():
    def edit(v):
        v["inputs"][0]["span"]["doc_id"] = "amp-13f-2022q4-0000950123-23-002429-s047"
    assert _mutated(edit).support == F


def test_input_of_another_security_fails():
    def edit(v):
        v["inputs"][6]["span"]["text"] = v["inputs"][6]["span"]["text"].replace("46625H100", "46625H10X")
    assert _mutated(edit).support == F


def test_arithmetic_that_does_not_give_the_claim_fails():
    def edit(v):
        v["formula"] = "(a + b + c + d + e + f + g) - (h + i + j + k + l + m + n)"
    assert _mutated(edit).support == F
