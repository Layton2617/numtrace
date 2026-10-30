"""Stage attribution for unanswered slots (harness/nonanswer.py)."""
from harness.agent import stage_output
from harness.nonanswer import diagnose_nonanswers, diagnose_slot, value_in_report
from harness.schema import SlotScore, SlotStatus, Stage, StageAttribution, StageOverrides
from harness.scoring import StubFactChecker
from harness.injection import flagged_docs

Q = "q-13f-gamma-nvda"


def _diag(traj, slots, q, corpus, gold):
    return {d.slot_id: d for d in diagnose_nonanswers(slots, q, traj, StubFactChecker(gold, flagged_docs(corpus)))}


def _diag_hidden(slot_id, status, traj, q, corpus, gold):
    """An input slot the question does not ask for gets no status; its diagnosis is still reachable directly."""
    return diagnose_slot(q.slot(slot_id), status, q, traj, StubFactChecker(gold, flagged_docs(corpus)))


def test_answered_slots_are_not_diagnosed(run, corpus, questions, gold):
    q = questions[Q]
    traj, _, slots = run(corpus, q, gold)
    assert _diag(traj, slots, q, corpus, gold) == {}


def test_refusal_after_extraction_is_synthesis_and_disclosure_found(run, corpus, questions, gold):
    q = questions[Q]
    mk = [{"kind": "refuse", "slot": "pos_q3"}, {"kind": "formula_only", "slot": "pos_change"},
          {"kind": "drop", "slot": "pos_q4"}]
    traj, _, slots = run(corpus, q, gold, mk)
    d = _diag(traj, slots, q, corpus, gold)
    assert set(d) == {"pos_change"}
    q3 = _diag_hidden("pos_q3", SlotStatus.refused, traj, q, corpus, gold)
    assert q3.stage == StageAttribution.synthesis and q3.disclosure_found and q3.refused_after_retrieval
    assert q3.retrieved_gold_doc_ids == ["gamma-13f-2022q3"]
    q4 = _diag_hidden("pos_q4", SlotStatus.missing, traj, q, corpus, gold)
    assert q4.stage == StageAttribution.synthesis and not q4.disclosure_found
    assert d["pos_change"].status == SlotStatus.formula_only and d["pos_change"].stage == StageAttribution.synthesis


def test_gold_doc_never_retrieved_is_retrieval(run, corpus, questions, gold):
    q = questions[Q]
    clean, _, _ = run(corpus, q, gold)
    docs = [d for d in stage_output(clean, Stage.retrieval) if d.doc_id != "gamma-13f-2022q3"]
    traj, _, slots = run(corpus, q, gold, overrides=StageOverrides(retrieval=docs))
    d = _diag(traj, slots, q, corpus, gold)
    q3 = _diag_hidden("pos_q3", SlotStatus.missing, traj, q, corpus, gold)
    assert q3.stage == StageAttribution.retrieval and q3.retrieved_gold_doc_ids == []
    # the computed change inherits the upstream stage of its missing input
    assert d["pos_change"].stage == StageAttribution.retrieval
    assert d["pos_change"].input_slots["pos_q3"] == "retrieval"


def test_retrieved_but_not_extracted_is_extraction(run, corpus, questions, gold):
    q = questions[Q]
    clean, _, _ = run(corpus, q, gold)
    facts = [f for f in stage_output(clean, Stage.extraction) if f.bound_slot != "pos_q3"]
    traj, _, slots = run(corpus, q, gold, overrides=StageOverrides(extraction=facts))
    d = _diag(traj, slots, q, corpus, gold)
    assert d["pos_change"].input_slots["pos_q3"] == "extraction"
    q3 = _diag_hidden("pos_q3", SlotStatus.missing, traj, q, corpus, gold)
    assert q3.stage == StageAttribution.extraction and q3.retrieved_gold_doc_ids == ["gamma-13f-2022q3"]


def test_value_in_report_means_scorer_miss_not_agent_failure(run, corpus, questions, gold):
    q = questions[Q]
    traj, _, slots = run(corpus, q, gold)
    fake = [SlotScore(question_id=q.id, trajectory_id=traj.trajectory_id, slot_id=s.slot_id,
                      status=SlotStatus.missing) for s in slots]
    d = _diag(traj, fake, q, corpus, gold)
    assert all(x.in_report and x.stage == StageAttribution.unknown for x in d.values() if x.slot_id != "pos_change")


def test_value_in_report_rounding_and_small_values():
    assert value_in_report("worth $1.53 billion", 1_533_465_048)
    assert value_in_report("held 1,105,208 shares", 1_105_208)
    assert not value_in_report("worth $1.6 billion", 1_533_465_048)
    assert not value_in_report("filed on 6 March", 6, cues=["positions"])
    assert value_in_report("number_of_positions: 6 [F1]", 6, cues=["positions"])
    assert value_in_report("the holdings count is 6 [F1]", 6, cues=["[F1]"])
