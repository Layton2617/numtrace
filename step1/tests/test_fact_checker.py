"""step1.fact_checker.GoldChecker: the gold value reaches the checked slot; lenient accepts the gold's alternatives,
strict only the primary gold."""
import sys
import types
from types import SimpleNamespace

import pytest

from harness.schema import AnswerSlot, DerivationKind, FactCheckResult, Question, Verdict
from step1.fact_checker import GoldChecker

P, F = Verdict.pass_, Verdict.fail
Q = Question(id="Q1", filing_type="13f", entity="Fund", period="2024Q1", text="How many shares?", target_concepts=[],
             slots=[AnswerSlot(slot_id="group_shares", concept="13f:Shares[X]", entity="Fund", period="2024Q1",
                               unit="shares"),
                    AnswerSlot(slot_id="total_debt", concept="us-gaap:LongTermDebt", entity="Fund", period="FY2023",
                               derivation="computed", formula="a + b", inputs={"a": "x", "b": "y"})])
GOLD = [{"question_id": "Q1", "slot": "group_shares", "value": 100.0, "unit": "shares", "status": "derived",
         "accept": [105.0]},
        {"question_id": "Q1", "slot": "total_debt", "value": 50.0, "unit": "USD", "status": "reported",
         "alt_derivation": {"formula": "a + b", "inputs": []}}]


def base(claim, question):
    """Passes when the claim equals the slot's expected value, the way the stub compares a computed claim."""
    want = question.slot(claim.slot_id).expected_value
    return FactCheckResult(value=P if claim.value == want else F, binding=P, support=P)


def claim(slot, value, kind=DerivationKind.reported):
    return SimpleNamespace(slot_id=slot, value=value, derivation=SimpleNamespace(kind=kind))


def test_lenient_accepts_the_listed_value_and_strict_does_not():
    lenient, strict = GoldChecker(base, GOLD), GoldChecker(base, GOLD, "strict")
    assert lenient(claim("group_shares", 100.0), Q).value == P and strict(claim("group_shares", 100.0), Q).value == P
    r = lenient(claim("group_shares", 105.0), Q)
    assert r.value == P and "accepted alternative value 105" in r.evidence[-1]
    assert strict(claim("group_shares", 105.0), Q).value == F
    assert lenient(claim("group_shares", 110.0), Q).value == F
    assert Q.slot("group_shares").expected_value is None          # the question itself is not changed


def test_a_sum_for_a_printed_total_passes_lenient_and_is_flagged_strict():
    c = claim("total_debt", 50.0, DerivationKind.computed)
    r = GoldChecker(base, GOLD)(c, Q)
    assert (r.value, r.support) == (P, P) and "alternative derivation" in r.evidence[-1]
    s = GoldChecker(base, GOLD, "strict")(c, Q)
    assert (s.value, s.support) == (P, F) and "derivation_mismatch" in s.source_flags
    assert GoldChecker(base, GOLD, "strict")(claim("total_debt", 50.0), Q).support == P   # the printed total itself


def test_unknown_mode_is_refused_and_the_config_flag_reaches_the_factory(monkeypatch):
    with pytest.raises(ValueError):
        GoldChecker(base, GOLD, "loose")
    from harness.run_pilot import load_fact_checker
    mod = types.ModuleType("fake_checker_mod")
    mod.make = lambda mode="lenient": ("made", mode)
    monkeypatch.setitem(sys.modules, "fake_checker_mod", mod)
    assert load_fact_checker("fake_checker_mod:make", "", None, "strict") == ("made", "strict")
    assert load_fact_checker("fake_checker_mod:make", "", None) == ("made", "lenient")


NQ = Question(id="N1", filing_type="nqdc", entity="Co", period="FY2023", text="What did she defer?", target_concepts=[],
              slots=[AnswerSlot(slot_id=s, concept=f"nqdc:{s}", entity="Co", period="FY2023", scope="NEO:A")
                     for s in ("executive_contributions", "aggregate_earnings", "registrant_contributions")])
NGOLD = [{"question_id": "N1", "slot": "executive_contributions", "value": None, "unit": "USD",
          "status": "not_applicable", "accept": ["0", "none", "N/A"]},
         {"question_id": "N1", "slot": "aggregate_earnings", "value": 204695.0, "unit": "USD", "status": "reported",
          "accept": ["204695", "204,696"],
          "alt_derivation": {"formula": "sum over plans", "value": 204695.0, "inputs": []}},
         {"question_id": "N1", "slot": "registrant_contributions", "value": 45634.0, "unit": "USD", "status": "derived",
          "also_printed": [{"doc_id": "proxy-s029", "printed": "45,634"}]}]


def nbase(claim, question):
    """No anchored fact: unsupported unless the slot's gold value matches, as the stub does for unanchored slots."""
    want = question.slot(claim.slot_id).expected_value
    ok = want is not None and claim.value == want
    return FactCheckResult(value=P if ok else (Verdict.na if want is None else F), binding=P, support=F)


def test_nqdc_string_accepts_alt_derivation_and_also_printed():
    lenient, strict = GoldChecker(nbase, NGOLD), GoldChecker(nbase, NGOLD, "strict")
    # an omitted 402(i) column: no primary value, so strict keeps the existing (unanchored) verdict; "0" is accepted
    assert strict(claim("executive_contributions", 0.0), NQ).value == Verdict.na
    assert lenient(claim("executive_contributions", 0.0), NQ).value == P
    # a Total row read $1 off, accepted as a string with a thousands separator
    assert lenient(claim("aggregate_earnings", 204696.0), NQ).value == P
    assert strict(claim("aggregate_earnings", 204696.0), NQ).value == F
    # plan rows summed for a printed Total
    c = claim("aggregate_earnings", 204695.0, DerivationKind.computed)
    assert "derivation_mismatch" in strict(c, NQ).source_flags and "derivation_mismatch" not in lenient(c, NQ).source_flags
    # the same figure in another proxy table
    cited = SimpleNamespace(**vars(claim("registrant_contributions", 45634.0)), source=SimpleNamespace(doc_id="proxy-s029"))
    assert lenient(cited, NQ).support == P and strict(cited, NQ).support == F
    other = SimpleNamespace(**vars(claim("registrant_contributions", 45634.0)), source=SimpleNamespace(doc_id="x"))
    assert lenient(other, NQ).support == F


def test_not_found_by_rule_has_no_gold_value_in_either_mode():
    q = Question(id="Q9", filing_type="statement", entity="Co", period="FY2024", text="?", target_concepts=[],
                 slots=[AnswerSlot(slot_id="diluted_shares", concept="us-gaap:X", entity="Co", period="FY2024",
                                   unit="shares")])
    gold = [{"question_id": "Q9", "slot": "diluted_shares", "value": None, "unit": "shares",
             "status": "not_found_by_rule"}]

    def anchored(claim, question):     # the claim's number differs from the anchored cell it cites
        return FactCheckResult(value=F, binding=P, support=F, value_failure="12 vs printed 13")
    for mode in ("lenient", "strict"):
        c = GoldChecker(anchored, gold, mode)
        r = c(claim("diluted_shares", 12.0), q)
        assert (r.value, r.binding) == (Verdict.na, P) and r.value_failure is None
        assert c.no_gold("Q9", "diluted_shares") and not c.no_gold("Q9", "other")
    ok = GoldChecker(lambda c_, q_: FactCheckResult(value=P, binding=P, support=P), gold)(claim("diluted_shares", 13.0), q)
    assert (ok.value, ok.support) == (P, P)      # a number with anchored support keeps its verdicts


def test_a_not_disclosed_answer_to_a_slot_without_gold_is_not_attributed():
    from harness.nonanswer import diagnose_slot
    from harness.schema import SlotStatus, StageAttribution
    q = Question(id="Q9", filing_type="statement", entity="Co", period="FY2024", text="?", target_concepts=[],
                 slots=[AnswerSlot(slot_id="diluted_shares", concept="us-gaap:X", entity="Co", period="FY2024")])
    c = GoldChecker(SimpleNamespace(expected=lambda *a: []), [{"question_id": "Q9", "slot": "diluted_shares",
                                                              "value": None, "unit": "shares",
                                                              "status": "not_found_by_rule"}])
    traj = SimpleNamespace(trajectory_id="t", report="Not disclosed.", steps=[])
    d = diagnose_slot(q.slot("diluted_shares"), SlotStatus.refused, q, traj, c)
    assert d.stage == StageAttribution.none and not d.disclosure_found
