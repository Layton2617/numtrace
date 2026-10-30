"""Scope failures: a component taken for the whole is attributed to the first stage that treats it as the
slot's answer."""
from harness.schema import (AnswerSlot, Binding, BindingField, Claim, ComputedValue, DerivationInput, Derivation,
                            DerivationKind, ExtractedFact, FactCheckResult, Question, Span, Stage, Verdict)
from harness.scoring import _binding_stage

SLOT = AnswerSlot(slot_id="market_value", concept="13f:Value[037833100]", entity="Berkshire", period="2022Q4",
                  scope="filer")
Q = Question(id="BRK-1", filing_type="13f", entity="Berkshire", period="2022Q4", text="?", target_concepts=[],
             slots=[SLOT])
SPAN = Span(doc_id="d", start=0, end=5, text="12345")
CLAIM = Claim(claim_id="c1", question_id="BRK-1", trajectory_id="t", text="...", number_text="12,345", value=12345,
              concept_as_stated=SLOT.concept, derivation=Derivation(kind=DerivationKind.reported), source=SPAN,
              slot_id="market_value")
FC = FactCheckResult(value=Verdict.pass_, binding=Verdict.fail,
                     support=Verdict.fail, binding_failures=[BindingField.scope],
                     gold_binding=Binding(concept=SLOT.concept, period="2022Q4", scope="sub-position (other managers 4)"))


def _fact(bound_slot):
    return ExtractedFact(fact_id="f1", doc_id="d", span=SPAN, label="APPLE INC COM, Value", printed_value=12345,
                         bound_slot=bound_slot, bound_concept=SLOT.concept, period="2022Q4")


def test_row_bound_to_filer_slot_is_extraction():
    ev = {}
    assert _binding_stage(CLAIM, FC, [_fact("market_value")], [], Q, ev) == Stage.extraction
    assert "as the whole of slot market_value" in ev["binding"]


def test_wrong_merge_is_calculation():
    v = ComputedValue(value_id="v1", slot_id="market_value", concept=SLOT.concept, value=24690, formula="a + b",
                      inputs=[DerivationInput(ref="f1", var="a", value=12345), DerivationInput(ref="f2", var="b", value=12345)])
    assert _binding_stage(CLAIM, FC, [_fact("market_value")], [v], Q, {}) == Stage.calculation


def test_unbound_component_is_synthesis():
    assert _binding_stage(CLAIM, FC, [_fact(None)], [], Q, {}) == Stage.synthesis
