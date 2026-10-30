"""The three proposal walk-throughs through the harness, scored by the data layer instead of the stub.

The toy corpus is the harness's; the facts behind it are built with data-layer code (13F unit rule by
filing date, the NQDC company record and 402(i) rows), then anchored to the printed lines.
"""
from datetime import date

import pytest

from data_layer.facts import Fact
from data_layer.harness_adapter import DataLayerFactChecker, anchor_table_lines
from data_layer.nqdc import CompanyNQDCRecord, FundingAsset, NQDCTableRow, table_rows_to_facts
from data_layer.thirteen_f import POSITION_SHARES, POSITION_VALUE, normalize_value, value_unit_for_filing_date
from harness.agent import AgentConfig, DeepResearchAgent
from harness.claims import extract_and_align
from harness.fixtures import WALKTHROUGH_MISTAKES, toy_corpus, toy_questions
from harness.models import build_model
from harness.schema import BindingField, Claim, Derivation, DerivationKind, Span, StageAttribution, Verdict
from harness.scoring import score_claims

P, F = Verdict.pass_, Verdict.fail
FYE = date(2025, 12, 31)
ACME, GAMMA = "0000000001", "0000000003"


def _stmt(concept, v):
    return Fact(entity=ACME, concept=concept, value=v, unit="USD", period_end=FYE, form="10-K")


def _f13(cusip, printed, filing_date, period_end):
    unit = value_unit_for_filing_date(filing_date)
    return Fact(entity=GAMMA, concept=POSITION_VALUE, value=normalize_value(printed, filing_date), unit="USD",
                period_end=period_end, dimensions={"cusip": cusip}, original_value=printed, original_unit=unit,
                form="13F-HR", filed=date.fromisoformat(filing_date))


def _shares(cusip, n, period_end):
    return Fact(entity=GAMMA, concept=POSITION_SHARES, value=n, unit="shares", period_end=period_end,
                dimensions={"cusip": cusip}, form="13F-HR")


@pytest.fixture(scope="module")
def corpus():
    return toy_corpus()


@pytest.fixture(scope="module")
def questions():
    return {q.id: q for q in toy_questions()}


@pytest.fixture(scope="module")
def checker(corpus):
    docs = {d.doc_id: d for d in corpus.docs}
    ltd = {"Long-term debt, net of current": _stmt("us-gaap:LongTermDebtNoncurrent", 4_120e6),
           "Current portion of long-term debt": _stmt("us-gaap:LongTermDebtCurrent", 750e6),
           "Total long-term debt": _stmt("us-gaap:LongTermDebt", 4_870e6)}
    rec = CompanyNQDCRecord(entity=ACME, fiscal_year_end=FYE, plan_name="Acme DCP", plan_liability=61.2e6,
                            funding_assets=[FundingAsset(vehicle="coli_cash_surrender_value", value=74.9e6,
                                                         in_rabbi_trust=True)])
    liab, coli = rec.to_facts()
    ceo = {f.concept: f for f in table_rows_to_facts([NQDCTableRow(
        executive="Jane Roe", fiscal_year_end=FYE, executive_contributions=900_000, registrant_contributions=300_000,
        aggregate_earnings=450_000, aggregate_withdrawals=0, aggregate_balance=8_400_000)], ACME)}
    q3, q4 = date(2022, 9, 30), date(2022, 12, 31)
    anchors = (
        anchor_table_lines(docs["acme-10k-fy2025-bs"], {k: v for k, v in ltd.items() if k != "Total long-term debt"},
                           entity="Acme Corp", period="FY2025")
        + anchor_table_lines(docs["acme-10k-fy2025-note9"], ltd, entity="Acme Corp", period="FY2025")
        + anchor_table_lines(docs["acme-10k-fy2025-note14"], {"Deferred compensation plan liability": liab,
                                                               "Cash surrender value": coli},
                             entity="Acme Corp", period="FY2025", scope="company-wide")
        + anchor_table_lines(docs["acme-def14a-2026-402i"], {
            "Executive contributions": ceo["nqdc:executive_contributions"],
            "Registrant contributions": ceo["nqdc:registrant_contributions"],
            "Aggregate earnings": ceo["nqdc:aggregate_earnings"],
            "Aggregate balance": ceo["nqdc:aggregate_balance"]}, entity="Acme Corp", period="FY2025", scope="NEO:CEO")
        + anchor_table_lines(docs["gamma-13f-2022q3"], {
            "Nvidia Corp, CUSIP 67066G104, value": _f13("67066G104", 1_234_567, "2022-11-14", q3),
            "Nvidia Corp, CUSIP 67066G104, shares": _shares("67066G104", 10_170_000, q3),
            "Apple Inc, CUSIP 037833100, value": _f13("037833100", 402_113, "2022-11-14", q3)},
            entity="Gamma Capital", period="2022Q3")
        + anchor_table_lines(docs["gamma-13f-2022q4"], {
            "Nvidia Corp, CUSIP 67066G104, value": _f13("67066G104", 1_198_000_000, "2023-02-10", q4),
            "Nvidia Corp, CUSIP 67066G104, shares": _shares("67066G104", 8_197_000, q4),
            "Apple Inc, CUSIP 037833100, value": _f13("037833100", 390_500_000, "2023-02-10", q4)},
            entity="Gamma Capital", period="2022Q4")
    )
    prior_ltd = Fact(entity=ACME, concept="us-gaap:LongTermDebt", value=5_210e6, unit="USD",
                     period_end=date(2024, 12, 31), form="10-K")
    return DataLayerFactChecker(anchors, facts=[a.fact for a in anchors] + [prior_ltd])


def run(corpus, question, checker, mistakes=()):
    model = build_model({"provider": "mock", "name": "mock", "mistakes": list(mistakes)})
    traj = DeepResearchAgent(model, corpus, AgentConfig(model=model.name)).run(question)
    claims, slots = extract_and_align(traj.report, question, traj)
    return traj, score_claims(claims, question, traj, checker), slots


def _by_slot(scored, slot_id):
    return next(s for s in scored if s.claim.slot_id == slot_id)


def test_anchor_scale_follows_13f_filing_date(checker):
    by_doc = {a.doc_id: a.scale for a in checker.anchors if a.fact.concept == POSITION_VALUE}
    assert by_doc == {"gamma-13f-2022q3": 1e3, "gamma-13f-2022q4": 1.0}
    stmt = {a.scale for a in checker.anchors if a.fact.concept.startswith("us-gaap")}
    assert stmt == {1e6}


def test_anchor_rejects_a_number_that_disagrees_with_its_fact(corpus):
    doc = next(d for d in corpus.docs if d.doc_id == "acme-10k-fy2025-note9")
    with pytest.raises(ValueError):
        anchor_table_lines(doc, {"Total long-term debt": _stmt("us-gaap:LongTermDebt", 4_900e6)},
                           entity="Acme Corp", period="FY2025")


@pytest.mark.parametrize("qid", ["q-stmt-acme-ltd", "q-13f-gamma-nvda", "q-nqdc-acme-liability", "q-nqdc-acme-ceo"])
def test_clean_mock_passes_everything(corpus, questions, checker, qid):
    _, scored, slots = run(corpus, questions[qid], checker)
    assert scored
    for s in scored:
        assert (s.verdicts.value, s.verdicts.binding, s.verdicts.support) == (P, P, P), s.claim.text
        assert s.provenance.stage_first_visible == StageAttribution.none
    assert all(sl.status.value == "answered" for sl in slots)


def test_walkthrough_1_neighbouring_line(corpus, questions, checker):
    q = questions["q-stmt-acme-ltd"]
    _, scored, _ = run(corpus, q, checker, WALKTHROUGH_MISTAKES[q.id])
    s = _by_slot(scored, "ltd_total")
    assert (s.verdicts.value, s.verdicts.binding, s.verdicts.support) == (P, F, F)
    assert BindingField.concept in s.verdicts.binding_failures
    assert s.provenance.gold_binding.concept == "us-gaap:LongTermDebtNoncurrent"
    assert s.provenance.stage_first_visible == StageAttribution.extraction


def test_walkthrough_2_unit_follows_filing_date(corpus, questions, checker):
    q = questions["q-13f-gamma-nvda"]
    _, scored, _ = run(corpus, q, checker, WALKTHROUGH_MISTAKES[q.id])
    q3 = _by_slot(scored, "pos_q3")
    assert (q3.verdicts.value, q3.verdicts.binding) == (P, F)
    assert q3.verdicts.binding_failures == [BindingField.unit]
    assert q3.provenance.gold_binding.scale == 1e3 and q3.fact_check.matched_scale == 1.0
    assert q3.provenance.stage_first_visible == StageAttribution.calculation
    q4 = _by_slot(scored, "pos_q4")
    assert (q4.verdicts.value, q4.verdicts.binding, q4.verdicts.support) == (P, P, P)
    ch = _by_slot(scored, "pos_change")
    assert (ch.verdicts.value, ch.verdicts.binding) == (P, F)
    assert ch.provenance.stage_first_visible == StageAttribution.calculation


def test_walkthrough_3_asset_offered_as_liability(corpus, questions, checker):
    q = questions["q-nqdc-acme-liability"]
    _, scored, _ = run(corpus, q, checker, WALKTHROUGH_MISTAKES[q.id])
    s = _by_slot(scored, "liability")
    assert (s.verdicts.value, s.verdicts.binding, s.verdicts.support) == (P, F, F)
    assert BindingField.concept in s.verdicts.binding_failures
    assert s.provenance.gold_binding.concept == "nqdc:COLICashSurrenderValue"
    assert s.provenance.stage_first_visible == StageAttribution.synthesis


def test_lookup_returns_the_binding_at_a_span(corpus, checker):
    doc = next(d for d in corpus.docs if d.doc_id == "gamma-13f-2022q3")
    i = doc.text.index("Nvidia Corp, CUSIP 67066G104, value")
    b = checker.lookup(Span(doc_id=doc.doc_id, start=i, end=i + 10, text=""))
    assert (b.concept, b.period, b.scale) == ("13f:Value[67066G104]", "2022Q3", 1e3)
    assert checker.lookup(Span(doc_id="blog-acme-debt", start=0, end=5, text="")) is None


def test_unanchored_number_found_in_the_full_store(questions, checker):
    """A blog-sourced number that is last year's total LTD: value passes, binding fails on period."""
    q = questions["q-stmt-acme-ltd"]
    claim = Claim(claim_id="c1", question_id=q.id, trajectory_id="t", text="Total long-term debt was $5.21 billion.",
                  number_text="$5.21 billion", value=5.21e9, rel_tol=0.001, entity="Acme Corp",
                  concept_as_stated="us-gaap:LongTermDebt", period="FY2025",
                  derivation=Derivation(kind=DerivationKind.reported),
                  source=Span(doc_id="blog-acme-debt", start=0, end=10, text="Acme Corp"), slot_id="ltd_total")
    r = checker(claim, q)
    assert (r.value, r.binding, r.support) == (P, F, F)
    assert r.binding_failures == [BindingField.period]
    assert "not_in_fact_store" in r.source_flags
    assert r.gold_binding.period == "2024-12-31"


def test_quote_over_several_cells_is_scored_against_the_cell_it_used():
    from data_layer.harness_adapter import Anchor
    from harness.schema import AnswerSlot, Doc, FilingType, Question
    from harness.tools import parse_table_lines

    text = "Jane Roe | Executive contributions: $900,000 | Aggregate balance: $8,400,000\n"
    doc = Doc(doc_id="p", source_label="SEC EDGAR DEF 14A", entity="Acme Corp", period="FY2025", text=text)
    anchors = []
    for ln in parse_table_lines(text):
        concept = "nqdc:executive_contributions" if "Executive" in ln.label else "nqdc:aggregate_balance"
        f = Fact(entity=ACME, concept=concept, value=ln.value, unit="USD", period_end=FYE)
        anchors.append(Anchor(doc_id="p", start=ln.end - len(ln.number_text), end=ln.end, label=ln.label,
                              printed_value=ln.value, scale=1.0, entity="Acme Corp", period="FY2025",
                              scope="NEO:Jane Roe", fact=f))
    q = Question(id="q", filing_type=FilingType.nqdc, entity="Acme Corp", period="FY2025", text="", target_concepts=[],
                 slots=[AnswerSlot(slot_id="bal", concept="nqdc:AggregateBalance", entity="Acme Corp", period="FY2025",
                                   scope="NEO:Jane Roe")])
    line = parse_table_lines(text)[1]
    claim = Claim(claim_id="c", question_id="q", trajectory_id="t", text="", number_text="$8.40 million", value=8.4e6,
                  rel_tol=0.001, entity="Acme Corp", concept_as_stated="nqdc:AggregateBalance", period="FY2025",
                  derivation=Derivation(kind=DerivationKind.reported), slot_id="bal",
                  source=Span(doc_id="p", start=line.start, end=line.end, text=line.text))
    r = DataLayerFactChecker(anchors)(claim, q)
    assert (r.value, r.binding, r.support) == (P, P, P)
    assert r.gold_binding.concept == "nqdc:AggregateBalance"


# -- real pilot reports (DeepSeek run, 2026-09-28), cut down to the lines involved ---------------------------------
from data_layer.harness_adapter import Anchor  # noqa: E402
from harness.agent import locate_span  # noqa: E402
from harness.schema import (AnswerSlot, ComputedValue, DerivationInput, Doc, ExtractedFact, FilingType,  # noqa: E402
                            Question, RetrievalConfig, RetrievedDoc, Stage, StepLog, Trajectory)

BRK, PSCM, CMCSA = "0001067983", "0001336528", "0001166691"


def _row_traj(q, docs, facts, scales, report):
    """Retrieval of ``docs``, the logged facts, one identity value C<i> = x(F<i>) per fact, then the report."""
    values = [ComputedValue(value_id=f"C{i}", slot_id=f.bound_slot, period=f.period, value=f.printed_value * s,
                            inputs=[DerivationInput(ref=f.fact_id, value=f.printed_value * s, printed_value=f.printed_value,
                                                    scale=s, doc_id=f.doc_id, span=f.span)])
              for i, (f, s) in enumerate(zip(facts, scales), start=1)]
    out = [(Stage.retrieval, {"docs": [RetrievedDoc(**d.model_dump(), rank=i, score=1.0).model_dump(mode="json")
                                       for i, d in enumerate(docs)]}),
           (Stage.extraction, {"facts": [f.model_dump(mode="json") for f in facts]}),
           (Stage.calculation, {"values": [v.model_dump(mode="json") for v in values]}),
           (Stage.synthesis, {"report": report})]
    return Trajectory(trajectory_id="t", question_id=q.id, model="deepseek", corpus_snapshot_id="s",
                      retrieval_config=RetrievalConfig(), config_hash="h", report=report,
                      steps=[StepLog(step_id=i, stage=s, input={}, output=o) for i, (s, o) in enumerate(out)])


def _logged(fid, doc, quote, printed, slot, period):
    return ExtractedFact(fact_id=fid, doc_id=doc.doc_id, span=locate_span(doc.text, doc.doc_id, quote), label=quote[:40],
                         printed_value=printed, bound_slot=slot, period=period)


def _anchor(doc, cell, fact, entity, period, scope):
    i = doc.text.index(cell)
    return Anchor(doc_id=doc.doc_id, start=i, end=i + len(cell), label=cell, printed_value=float(cell.replace(",", "")),
                  scale=1.0, entity=entity, period=period, scope=scope, fact=fact)


def _scored(q, traj, checker):
    claims, slots = extract_and_align(traj.report, q, traj)
    return {s.claim.number_text: s for s in score_claims(claims, q, traj, checker)}, {s.slot_id: s.status.value for s in slots}


def test_rows_summed_inside_extraction_are_right_but_unsupported():
    """BRK-1: the extracted 'printed' values are the filer totals, quoted with one other-manager row."""
    q4 = date(2022, 12, 31)
    row = ("APPLE INC COM (CUSIP 037833100) | Value: 86,841,985,318 | Shares or principal (SH): 669,429,166 | "
           "Investment discretion: DFND | Other managers: #4 #11")
    doc = Doc(doc_id="brk-q4", source_label="SEC EDGAR 13F-HR", text=row + "\n", is_primary_filing=True)
    cell = lambda concept, v, unit: Fact(entity=BRK, concept=concept, value=v, unit=unit, period_end=q4,  # noqa: E731
                                         dimensions={"cusip": "037833100"}, form="13F-HR")
    anchors = [_anchor(doc, "86,841,985,318", cell(POSITION_VALUE, 86_841_985_318, "USD"), "Berkshire", "2022Q4",
                       "sub-position (other managers 4 11)"),
               _anchor(doc, "669,429,166", cell(POSITION_SHARES, 669_429_166, "shares"), "Berkshire", "2022Q4",
                       "sub-position (other managers 4 11)")]
    store = [cell(POSITION_VALUE, 116_305_043_218, "USD"), cell(POSITION_SHARES, 895_136_175, "shares")]
    q = Question(id="BRK-1", filing_type=FilingType.f13, entity="Berkshire", period="2022Q4", text="", target_concepts=[],
                 slots=[AnswerSlot(slot_id="shares_total_filer_level", concept="13f:Shares[037833100]",
                                   entity="Berkshire", period="2022Q4", unit="shares", scope="filer",
                                   expected_value=895_136_175),
                        AnswerSlot(slot_id="market_value", concept="13f:Value[037833100]", entity="Berkshire",
                                   period="2022Q4", scope="filer", expected_value=116_305_043_218)])
    facts = [_logged("F1", doc, row, 895_136_175, "shares_total_filer_level", "2022Q4"),
             _logged("F2", doc, row, 116_305_043_218, "market_value", "2022Q4")]
    report = ("shares_total_filer_level: 895,136,175 shares [C1]  \n"
              "market_value: $116,305,043,218 ($116.31 billion) [C2]")
    scored, _ = _scored(q, _row_traj(q, [doc], facts, [1.0, 1.0], report), DataLayerFactChecker(anchors, store))
    assert set(scored) == {"895,136,175", "$116,305,043,218 ($116.31 billion"}
    for s in scored.values():
        assert (s.verdicts.value, s.verdicts.binding, s.verdicts.support) == (P, P, F)
        assert "not_in_quote" in s.verdicts.notes
        assert s.provenance.stage_first_visible == StageAttribution.extraction


def test_concept_written_as_bound_slot_names_the_slot():
    """PSCM-1: extraction wrote the concept ('13f:Shares[169656105]') where the slot id belongs."""
    row = ("CHIPOTLE MEXICAN GRILL INC COM (CUSIP 169656105) | Value: 1,533,465,048 | Shares or principal (SH): "
           "1,105,208 | Investment discretion: SOLE | Other managers: none")
    doc = Doc(doc_id="pscm-q4", source_label="SEC EDGAR 13F-HR", text=row + "\n", is_primary_filing=True)
    cell = lambda concept, v, unit: Fact(entity=PSCM, concept=concept, value=v, unit=unit,  # noqa: E731
                                         period_end=date(2022, 12, 31), dimensions={"cusip": "169656105"}, form="13F-HR")
    anchors = [_anchor(doc, "1,533,465,048", cell(POSITION_VALUE, 1_533_465_048, "USD"), "Pershing", "2022Q4", "filer"),
               _anchor(doc, "1,105,208", cell(POSITION_SHARES, 1_105_208, "shares"), "Pershing", "2022Q4", "filer")]
    q = Question(id="PSCM-1", filing_type=FilingType.f13, entity="Pershing", period="2022Q4",
                 text="How many Chipotle shares did Pershing Square hold, and what was the position worth?",
                 target_concepts=[],
                 slots=[AnswerSlot(slot_id="shares", concept="13f:Shares[169656105]", entity="Pershing",
                                   period="2022Q4", unit="shares", scope="filer", expected_value=1_105_208),
                        AnswerSlot(slot_id="market_value", concept="13f:Value[169656105]", entity="Pershing",
                                   period="2022Q4", scope="filer", expected_value=1_533_465_048)])
    facts = [_logged("F1", doc, row, 1_105_208, "13f:Shares[169656105]", "2022Q4"),
             _logged("F2", doc, row, 1_533_465_048, "13f:Value[169656105]", "2022Q4")]
    report = ("At 2022Q4, Pershing Square Capital Management, L.P. held **1,105,208 Chipotle shares** [F1][C1].  \n"
              "According to its 13F, the position was worth **$1,533,465,048**, or **$1.53 billion** [F2][C2].")
    scored, slots = _scored(q, _row_traj(q, [doc], facts, [1.0, 1.0], report), DataLayerFactChecker(anchors))
    assert slots == {"shares": "answered", "market_value": "answered"}
    assert {n: s.claim.slot_id for n, s in scored.items()} == {"1,105,208": "shares", "$1.53 billion": "market_value"}
    assert all((s.verdicts.value, s.verdicts.binding, s.verdicts.support) == (P, P, P) for s in scored.values())


def test_store_search_keeps_the_precision_the_claim_states():
    """CMCSA-1: '$6.00 billion' copied from Note 6's '6.0' (in billions); the store holds 5,958 million."""
    fye = date(2025, 12, 31)
    note6 = Doc(doc_id="note6", source_label="SEC EDGAR 10-K", is_primary_filing=True,
                text="December 31 (in billions) | 2025 (b) | 2024 (b)\nTotal debt | 2025 (b): 98.9 | 2024 (b): 99.1\n"
                     "Less: Current portion | 2025 (b): 6.0 | 2024 (b): 4.9\n"
                     "Noncurrent portion of debt | 2025 (b): $93.0 | 2024 (b): $94.2\n")
    bs = Doc(doc_id="bs", source_label="SEC EDGAR 10-K", is_primary_filing=True,
             text="Current portion of debt | 2025: 5,958 | 2024: 4,907\n")
    current = Fact(entity=CMCSA, concept="us-gaap:DebtCurrent", value=5_958e6, unit="USD", period_end=fye, form="10-K")
    i = bs.text.index("5,958")
    anchor = Anchor(doc_id="bs", start=i, end=i + 5, label="Current portion of debt, 2025", printed_value=5958,
                    scale=1e6, entity="Comcast", period="FY2025", fact=current)
    q = Question(id="CMCSA-1", filing_type=FilingType.statement, entity="Comcast", period="FY2025", text="",
                 target_concepts=[], slots=[AnswerSlot(slot_id="current_portion", concept="us-gaap:DebtCurrent",
                                                       entity="Comcast", period="FY2025", expected_value=5_958e6)])
    facts = [_logged("F1", note6, "Less: Current portion | 2025 (b): 6.0 | 2024 (b): 4.9", 6.0, "current_portion",
                     "FY2025")]
    report = ("At the end of FY2025, Comcast’s current portion of debt due within one year was **$6.00 billion** "
              "[F1][C1].")
    checker = DataLayerFactChecker([anchor], [current])
    s = _scored(q, _row_traj(q, [note6], facts, [1e9], report), checker)[0]["$6.00 billion"]
    assert (s.verdicts.value, s.verdicts.binding, s.verdicts.support) == (F, P, F)     # 5,958 -> 5.96 at two decimals
    assert s.provenance.stage_first_visible == StageAttribution.synthesis
    s1 = _scored(q, _row_traj(q, [note6], facts, [1e9], report.replace("$6.00 billion", "$6.0 billion")), checker)[0]
    assert s1["$6.0 billion"].verdicts.value == P
