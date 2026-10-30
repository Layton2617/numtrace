import json

from harness.claims import LLMClaimTagger, RegexClaimTagger, align_claims, extract_and_align
from harness.schema import SlotStatus
from harness.tools import find_money, scale_invariant_match


def test_find_money():
    ms = find_money("rose from $1.23 million to $1.20 billion, a change of -$36.6 million and $25,000")
    assert [m.value for m in ms] == [1.23e6, 1.20e9, -36.6e6, 25000]
    assert abs(ms[0].rel_tol - 0.005e6 / 1.23e6) < 1e-12


def test_scale_invariant_match():
    assert scale_invariant_match(4.12e9, 4120, 0.005 / 4.12) == 1e6
    assert scale_invariant_match(1.23e6, 1_234_567, 0.005 / 1.23) == 1.0
    assert scale_invariant_match(7.48e6, 0, 0.001) is None
    assert scale_invariant_match(3.2e9, 3, 0.01) is None


def test_claims_resolve_sources_and_derivations(run, corpus, questions, gold):
    q = questions["q-13f-gamma-nvda"]
    traj, scored, slots = run(corpus, q, gold)
    claims = [s.claim for s in scored]
    assert [c.slot_id for c in claims] == ["pos_q3", "pos_q4", "pos_change"]
    assert claims[0].source.doc_id == "gamma-13f-2022q3" and claims[0].period == "2022Q3"
    assert claims[2].derivation.kind.value == "computed" and len(claims[2].derivation.inputs) == 2
    assert claims[2].derivation.inputs[0].span is not None
    assert all(s.status == SlotStatus.answered for s in slots)


def test_slot_statuses_refused_formula_only_missing(run, corpus, questions, gold):
    q = questions["q-13f-gamma-nvda"]
    mk = [{"kind": "refuse", "slot": "pos_q3"}, {"kind": "formula_only", "slot": "pos_change"},
          {"kind": "drop", "slot": "pos_q4"}]
    traj, scored, slots = run(corpus, q, gold, mk)
    st = {s.slot_id: s.status for s in slots}
    # the two quarter values are inputs the question does not ask for: no status, never missing
    assert st == {"pos_change": SlotStatus.formula_only}
    assert scored == []


def test_alignment_uses_period_to_break_ties(run, corpus, questions, gold):
    q = questions["q-13f-gamma-nvda"]
    traj, scored, _ = run(corpus, q, gold)
    claims = [s.claim.model_copy(update={"slot_id": None}) for s in scored][::-1]
    aligned = align_claims(claims, q)
    assert {c.period: c.slot_id for c in aligned if c.slot_id != "pos_change"} == {"2022Q3": "pos_q3",
                                                                                  "2022Q4": "pos_q4"}


def test_llm_tagger_hook_matches_regex_tagger(run, corpus, questions, gold):
    q = questions["q-nqdc-acme-liability"]
    traj, _, _ = run(corpus, q, gold)
    regex = RegexClaimTagger().tag(traj.report, q, traj)

    def fake_llm(prompt: str) -> str:
        return json.dumps({"claims": [{"number_text": c.number_text, "concept_phrase": c.concept_phrase,
                                       "citation": c.citation} for c in regex]})

    llm = LLMClaimTagger(fake_llm).tag(traj.report, q, traj)
    assert [(c.value, c.concept_as_stated, c.source) for c in llm] == \
           [(c.value, c.concept_as_stated, c.source) for c in regex]
    claims, _ = extract_and_align(traj.report, q, traj, tagger=LLMClaimTagger(fake_llm))
    assert [c.slot_id for c in claims] == ["liability", "funding"]


# -- real-report formatting (Opus pilot, 2026-09-28) -----------------------------------------------------------
from harness.claims import _mentions, split_sentences
from harness.tools import find_counts, find_money, scale_invariant_match


def test_lines_and_table_rows_are_separate_units():
    report = "# Debt\n| Current portion | $2.13 billion | [F1] |\nTotal was $16.46 billion [C3]. **$5.96 billion** [F2] is due."
    parts = [s for _, s in split_sentences(report)]
    assert parts == ["# Debt", "| Current portion | $2.13 billion | [F1] |", "Total was $16.46 billion [C3].",
                     "**$5.96 billion** [F2] is due."]


def test_parenthetical_restatement_is_one_mention_at_the_finer_precision():
    ms = _mentions("current portion $2.13 billion ($2,130 million) [F1]", counts=False)
    assert len(ms) == 1 and ms[0].value == 2.13e9 and ms[0].rel_tol < 1e-3


def test_binary_minus_is_not_a_sign():
    assert [m.value for m in find_money("$6.56 billion − $3.49 billion")] == [6.56e9, 3.49e9]
    assert find_money("a loss of −$5 million")[0].value == -5e6


def test_counts_with_markdown():
    assert [m.value for m in find_counts("a table entry total of **6** positions and 895,136,175 shares")] == [6, 895136175]
    assert find_counts("the same 11 rows") == []


def test_outflow_sign_convention_matches():
    assert scale_invariant_match(11.75e9, -11750, rel_tol=0.005 / 11.75) == 1e6


def test_count_after_its_label():
    """DeepSeek PSCM-4 wrote the slot name as the label: 'number_of_positions: 6 [F1][C1]'."""
    assert [m.value for m in find_counts("number_of_positions: 6 [F1][C1]")] == [6]
    assert [m.value for m in find_counts("- **Shares held:** 1,105,208 shares [F1]")] == [1105208]   # one mention
    assert find_counts("positions as of 2022Q4") == [] and find_counts("Number of positions: 2022Q4") == []
