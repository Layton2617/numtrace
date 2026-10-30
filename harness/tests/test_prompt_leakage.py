"""The agent prompt must not carry the binding the study measures (harness/LEAKAGE.md)."""
import json
import re
from pathlib import Path

import pytest

from harness.agent import (DISPLAY_IDS, PROMPTS, AgentConfig, DeepResearchAgent, _q_block, real_slot, shown_slots,
                           stage_output)
from harness.claims import extract_and_align, score_slots
from harness.injection import flagged_docs
from harness.models import MockModel, ModelResponse
from harness.schema import Question, Stage
from harness.scoring import StubFactChecker, score_claims

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "pilot/harness_inputs/questions.jsonl", *sorted((ROOT / "step1/data").glob("*/questions.jsonl"))]
QUESTIONS = [Question.model_validate_json(l) for f in FILES for l in f.read_text().splitlines() if l.strip()]
# words in a gold slot id that name the concept, scope, period convention or method rather than the question
LEAKY = re.compile(r"coli|cash_surrender|filer_level|registrant|executive_contributions|aggregate_earnings|liabilit"
                   r"|_fy\b|_9m\b|nine|q[1-4]_value|top\d_q|including_current|current_portion|ocf|capex", re.I)


def _family(q: Question) -> str:
    return q.family if q.id.startswith("S1-") else q.id


def _prompts(q: Question) -> list[str]:
    fmt = {"n": 2, "docs": "", "meta": "[]", "facts": "[]", "values": ""}
    return [PROMPTS[s].format(q=_q_block(q), **fmt) for s in Stage]


def test_question_files_found():
    assert len(FILES) == 4 and len(QUESTIONS) > 400


@pytest.mark.parametrize("q", list({_family(q): q for q in QUESTIONS}.values()), ids=_family)
def test_rendered_prompt_carries_no_binding(q):
    shown = shown_slots(q)
    hidden = [s for s in q.slots if s.slot_id not in shown.values()]
    for text in _prompts(q):
        assert "us-gaap" not in text and "13f:" not in text and "nqdc:" not in text
        for s in q.slots:
            assert s.concept not in text
            if s.formula:
                assert s.formula not in text
        for s in hidden:
            assert not re.search(rf"(?<![\w-]){re.escape(s.slot_id)}(?![\w-])", text)
    assert not [d for d in shown if LEAKY.search(d)]
    assert len(shown) == len({s for s in shown.values()})


def test_every_slot_name_reviewed():
    """A new slot id must get a display id (or be confirmed neutral) before it reaches the agent."""
    neutral = {"noncurrent", "total_debt", "diluted_eps", "diluted_eps_change", "year_end_balance", "shares",
               "total_value", "number_of_positions",
               "salary_deferral_max_pct", "bonus_deferral_max_pct"}   # NQDC-LIMIT: "up to what percentage of ..."
    for q in QUESTIONS:
        for d, sid in shown_slots(q).items():
            assert sid in DISPLAY_IDS or sid in neutral or re.fullmatch(r"top\d_change", sid), sid


@pytest.mark.parametrize("qid,shown", [
    ("S1-ST-Q4-AAPL-CAPEX-FY2024", ["capital_expenditures_fourth_quarter"]),
    ("TGT-2", ["operating_cash_fourth_quarter"]),
    ("TGT-1", ["due_within_one_year", "noncurrent", "total_long_term_debt"]),
    ("CMCSA-1", ["due_within_one_year", "total_debt"]),
    ("S1-ST-EPS-AAPL-FY2023Q2", ["diluted_eps", "diluted_eps_same_quarter_prior_year", "diluted_eps_change",
                                 "weighted_average_diluted_shares"]),
    ("S1-13F-UNIT-TACONIC-ALEXANDERS-2022Q3-2022Q4", ["value_change"]),
    ("NQDC-FUND", ["obligation", "life_insurance_value"]),
    ("CMCSA-4", ["obligation", "assets_held_against_obligation"]),
    ("BRK-1", ["shares"]),
    ("BRK-4", ["holding"]),
    ("PSCM-1", ["shares", "position_value"]),
    ("13F-GROUP", ["shares", "position_value"]),
])
def test_visible_slots(qid, shown):
    q = next(q for q in QUESTIONS if qid in (q.id, q.family))
    assert list(shown_slots(q)) == shown
    assert all(real_slot(q, d) == shown_slots(q)[d] for d in shown)


class DisplayIdModel(MockModel):
    """The mock, answering with the display ids a real model sees instead of the gold slot ids."""

    def complete(self, request):
        resp = super().complete(request)
        if request.stage not in (Stage.extraction, Stage.calculation):
            return resp
        q = Question.model_validate(request.context["question"])
        back = {v: k for k, v in shown_slots(q).items()}
        out = json.loads(resp.text)
        for item in out.get("facts", []) + out.get("values", []):
            item["slot_id"] = back.get(item["slot_id"], item["slot_id"])
            for spec in (item.get("inputs") or {}).values():
                if "slot_ref" in spec:
                    spec["slot_ref"] = back.get(spec["slot_ref"], spec["slot_ref"])
        return ModelResponse(text=json.dumps(out), usage=resp.usage)


def _renamed(q: Question, names: dict[str, str]) -> Question:
    """A toy question with real gold slot ids, so its prompt uses display ids that differ from them."""
    return q.model_copy(update={"slots": [s.model_copy(update={
        "slot_id": names.get(s.slot_id, s.slot_id),
        "inputs": {v: names.get(i, i) for v, i in s.inputs.items()}}) for s in q.slots]})


@pytest.mark.parametrize("qid,names", [
    ("q-13f-gamma-nvda", {"pos_q3": "q3_value", "pos_q4": "q4_value", "pos_change": "change"}),
    ("q-nqdc-acme-liability", {"liability": "plan_liability", "funding": "coli_value"}),
    ("q-stmt-acme-ltd", {"ltd_total": "total_including_current"}),
])
def test_display_ids_map_back_to_slot_ids(corpus, questions, gold, qid, names):
    q = _renamed(questions[qid], names)
    assert set(shown_slots(q)) != set(shown_slots(q).values())
    runs = []
    for model in (MockModel(), DisplayIdModel()):
        t = DeepResearchAgent(model, corpus, AgentConfig(model="mock")).run(q)
        claims, slots = extract_and_align(t.report, q, t)
        scored = score_claims(claims, q, t, StubFactChecker(gold, flagged_docs(corpus)))
        runs.append(([f.bound_slot for f in stage_output(t, Stage.extraction)],
                     [(v.slot_id, v.value) for v in stage_output(t, Stage.calculation)],
                     t.report, [(c.claim.slot_id, c.verdicts) for c in scored], [s.status for s in slots]))
    assert runs[0] == runs[1]


def test_slot_status_only_for_asked_slots():
    """Hidden inputs and slots the question does not ask for (BRK-4's position value) never count as missing."""
    for q in QUESTIONS:
        st = {s.slot_id: s.status for s in score_slots("No figures could be determined.", [], q, "t")}
        assert list(st) == list(shown_slots(q).values())
    brk4 = next(q for q in QUESTIONS if q.id == "BRK-4")
    assert [s.slot_id for s in score_slots("", [], brk4, "t")] == ["held_at_2023-12-31"]
