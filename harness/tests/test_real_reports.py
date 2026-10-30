"""Scorer fixes from the real pilot reports. Each case keeps the report text and the logged lines of
a real trajectory (run named in the test), cut down to the documents and facts involved."""
from harness.agent import locate_span
from harness.claims import RegexClaimTagger, extract_and_align
from harness.fixtures import GoldFact
from harness.schema import (AnswerSlot, Claim, ComputedValue, Derivation, DerivationInput, DerivationKind, Doc,
                            ExtractedFact, FactCheckResult, Question, RetrievalConfig, RetrievedDoc, Stage,
                            StageAttribution, StepLog, Trajectory, Verdict)
from harness.scoring import StubFactChecker, _value_stage, score_claims
from harness.tools import safe_eval

P, F, NA = Verdict.pass_, Verdict.fail, Verdict.na


def _doc(doc_id, text):
    return Doc(doc_id=doc_id, source_label="SEC EDGAR", text=text, is_primary_filing=True)


def _fact(fid, doc, quote, printed, slot, period="FY2025", concept=None):
    span = locate_span(doc.text, doc.doc_id, quote) if doc else locate_span("", "computed", quote)
    return ExtractedFact(fact_id=fid, doc_id=span.doc_id, span=span, label=quote[:40], printed_value=printed,
                         bound_slot=slot, bound_concept=concept, period=period)


def _x(vid, f, scale, slot, period="FY2025"):
    """An identity value C = x(F) at the scale the agent chose, as harness/agent.py logs it."""
    return ComputedValue(value_id=vid, slot_id=slot, period=period, value=f.printed_value * scale, inputs=[
        DerivationInput(ref=f.fact_id, value=f.printed_value * scale, printed_value=f.printed_value, scale=scale,
                        doc_id=f.doc_id, span=f.span)])


def _calc(vid, slot, formula, period, **ins):
    """A computed value over earlier values; an identity input carries its fact's source along (harness/agent.py)."""
    dins = [DerivationInput(ref=v.value_id, var=var, value=v.value, printed_value=v.inputs[0].printed_value,
                            scale=v.inputs[0].scale, doc_id=v.inputs[0].doc_id, span=v.inputs[0].span)
            for var, v in ins.items()]
    return ComputedValue(value_id=vid, slot_id=slot, period=period, formula=formula, inputs=dins,
                         derivation=DerivationKind.computed, value=safe_eval(formula, {i.var: i.value for i in dins}))


def _traj(q, docs, facts, values, report):
    out = [(Stage.retrieval, {"docs": [RetrievedDoc(**d.model_dump(), rank=i, score=1.0).model_dump(mode="json")
                                       for i, d in enumerate(docs)]}),
           (Stage.extraction, {"facts": [f.model_dump(mode="json") for f in facts]}),
           (Stage.calculation, {"values": [v.model_dump(mode="json") for v in values]}),
           (Stage.synthesis, {"report": report})]
    return Trajectory(trajectory_id="t", question_id=q.id, model="m", corpus_snapshot_id="s",
                      retrieval_config=RetrievalConfig(), config_hash="h", report=report,
                      steps=[StepLog(step_id=i, stage=s, input={}, output=o) for i, (s, o) in enumerate(out)])


def _gold(doc, cell, printed, scale, concept, entity, period="FY2025", scope=None):
    """A fact-store entry on the first occurrence of ``cell`` in ``doc``."""
    i = doc.text.index(cell)
    return GoldFact(fact_id=f"{doc.doc_id}@{i}", doc_id=doc.doc_id, start=i, end=i + len(cell), label=cell,
                    printed_value=printed, scale=scale, value=printed * scale, concept=concept, entity=entity,
                    period=period, scope=scope)


def _score(q, traj, gold):
    claims, slots = extract_and_align(traj.report, q, traj)
    return {s.claim.number_text: s for s in score_claims(claims, q, traj, StubFactChecker(gold))}, slots


def _vbs(s):
    return s.verdicts.value, s.verdicts.binding, s.verdicts.support


# -- CMCSA-2 (DeepSeek, GPT): capex prints as an outflow "( 8,001 )" ------------------------------------------------
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
CAPEX_FY = "Capital expenditures | 2025: ( 11,750 ) | 2024: ( 12,181 ) | 2023: ( 12,242 )"
CAPEX_9M = "Capital expenditures | 2025: ( 8,001 ) | 2024: ( 8,267 )"
Q_CAPEX = Question(id="CMCSA-2", filing_type="statement", entity="Comcast", period="Q4 2025", text="?",
                   target_concepts=[], slots=[
                       AnswerSlot(slot_id="capex_fy", concept=CAPEX, entity="Comcast", period="FY2025",
                                  expected_value=11_750e6),
                       AnswerSlot(slot_id="capex_9m", concept=CAPEX, entity="Comcast", period="Q3 FY2025",
                                  expected_value=8_001e6),
                       AnswerSlot(slot_id="q4_capex", concept=CAPEX, entity="Comcast", period="Q4 FY2025",
                                  derivation="computed", formula="b - a", inputs={"a": "capex_9m", "b": "capex_fy"},
                                  expected_value=3_749e6)])


def test_q4_capex_from_outflow_cells_passes():
    k, q = _doc("10k", "Investing Activities\n" + CAPEX_FY + "\n"), _doc("10q", "Investing Activities\n" + CAPEX_9M + "\n")
    f1, f2 = _fact("F1", k, CAPEX_FY, 11750, "capex_fy"), _fact("F2", q, CAPEX_9M, 8001, "capex_9m", "Q3 FY2025")
    c1, c2 = _x("C1", f1, 1e6, "capex_fy"), _x("C2", f2, 1e6, "capex_9m", "Q3 FY2025")
    report = ("Comcast’s fiscal 2025 capital expenditures were **$11,750.00 million** [F1][C1].  \n"
              "For the first nine months of fiscal 2025, capital expenditures were **$8,001.00 million** [F2][C2].  \n\n"
              "**$11,750.00 million − $8,001.00 million = $3,749.00 million** [C3]  \n\n"
              "So Comcast spent **$3,749.00 million** on capital expenditures in Q4 2025 [C3].")
    traj = _traj(Q_CAPEX, [k, q], [f1, f2], [c1, c2, _calc("C3", "q4_capex", "b - a", "Q4 FY2025", a=c2, b=c1)], report)
    gold = [_gold(k, "( 11,750 )", -11750, 1e6, CAPEX, "Comcast"),
            _gold(q, "( 8,001 )", -8001, 1e6, CAPEX, "Comcast", "Q3 FY2025")]
    scored, _ = _score(Q_CAPEX, traj, gold)
    q4 = scored["$3,749.00 million"]
    assert q4.claim.slot_id == "q4_capex" and q4.claim.derivation.kind == DerivationKind.computed
    assert all(_vbs(s) == (P, P, P) for s in scored.values())


def test_value_stage_reads_the_cell_that_prints_the_extracted_number():
    q = _doc("10q", CAPEX_9M)
    f2 = _fact("F2", q, CAPEX_9M, 8001, "capex_9m", "Q3 FY2025")
    claim = Claim(claim_id="c", question_id="CMCSA-2", trajectory_id="t", text="", number_text="$8.10 billion",
                  value=8.1e9, rel_tol=0.005 / 8.1, derivation=Derivation(kind=DerivationKind.reported))
    ev = {}
    stage = _value_stage(claim, FactCheckResult(value=F, binding=P, support=F), [f2], [], {q.doc_id: q}, ev)
    assert stage == Stage.synthesis and "document prints" not in ev["value"]     # was "document prints -8267"


# -- TGT-4 (DeepSeek): a difference of two cited levels ------------------------------------------------------------
LIAB = ("Our total liability under these plans was $717 million and $684 million as of January 31, 2026, and "
        "February 1, 2025, respectively.")
COLI = "Company-owned life insurance investments, net of loans (b) | January 31, 2026: 600 | February 1, 2025: 540"
Q_TGT4 = Question(id="TGT-4", filing_type="nqdc", entity="Target", period="FY2025", text="?", target_concepts=[],
                  slots=[AnswerSlot(slot_id="plan_liability", concept="nqdc:PlanLiability", entity="Target",
                                    period="FY2025", expected_value=717e6, scope="company-wide",
                                    aliases=["deferred compensation obligation", "total liability under these plans"]),
                         AnswerSlot(slot_id="coli_value", concept="nqdc:COLICashSurrenderValue", entity="Target",
                                    period="FY2025", expected_value=600e6, scope="company-wide",
                                    aliases=["company-owned life insurance"])])
DIFF = "The reported plan liability exceeded this COLI value by $117.00 million"


def _tgt4(report):
    n, b = _doc("note24", LIAB), _doc("bs", COLI)
    f1, f2 = _fact("F1", n, LIAB, 717, "plan_liability"), _fact("F2", b, COLI, 600, "coli_value")
    traj = _traj(Q_TGT4, [n, b], [f1, f2], [_x("C1", f1, 1e6, "plan_liability"), _x("C2", f2, 1e6, "coli_value")],
                 report)
    gold = [_gold(n, "717", 717, 1e6, "nqdc:PlanLiability", "Target", scope="company-wide"),
            _gold(b, "600", 600, 1e6, "nqdc:COLICashSurrenderValue", "Target", scope="company-wide")]
    return _score(Q_TGT4, traj, gold)[0]


def test_difference_of_cited_levels_is_not_aligned_to_a_level_slot():
    scored = _tgt4("- **Plan liability (FY2025):** $717.00 million as of January 31, 2026 [F1][C1].  \n"
                   "- **COLI value (FY2025):** $600.00 million, reported as company-owned life insurance investments, "
                   "net of loans, as of January 31, 2026 [F2][C2].  \n  " + DIFF + " [C1][C2].")
    assert _vbs(scored["$717.00 million"]) == (P, P, P) and scored["$717.00 million"].claim.slot_id == "plan_liability"
    diff = scored["$117.00 million"]
    assert diff.claim.slot_id is None and diff.claim.derivation.kind == DerivationKind.computed
    assert _vbs(diff) == (NA, NA, NA) and diff.provenance.stage_first_visible == StageAttribution.none


def test_comparison_wording_keeps_a_difference_off_a_free_level_slot():
    for report in (DIFF + " [C1].", DIFF + "."):          # one citation that does not hold it; none at all
        diff = _tgt4(report)["$117.00 million"]
        assert diff.claim.slot_id is None and _vbs(diff) == (NA, NA, NA), report


# -- TGT-1 (DeepSeek): calculation applied thousands, the report states the total correctly -------------------------
CUR = "Current portion of long-term debt and other borrowings | January 31, 2026: 2,130 | February 1, 2025: 1,636"
NONCUR = "Long-term debt and other borrowings | January 31, 2026: 14,326 | February 1, 2025: 14,304"
CUR_C, NONCUR_C = "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligations"
Q_TGT1 = Question(id="TGT-1", filing_type="statement", entity="Target", period="FY2025", text="?", target_concepts=[],
                  slots=[AnswerSlot(slot_id="current_portion", concept=CUR_C, entity="Target", period="FY2025",
                                    expected_value=2_130e6),
                         AnswerSlot(slot_id="noncurrent", concept=NONCUR_C, entity="Target", period="FY2025",
                                    expected_value=14_326e6),
                         AnswerSlot(slot_id="total_including_current", concept=CUR_C + "IncludingCurrentMaturities",
                                    entity="Target", period="FY2025", derivation="computed", formula="a + b",
                                    inputs={"a": "current_portion", "b": "noncurrent"}, expected_value=16_456e6)])


def _tgt1(total_line):
    bs = _doc("bs", "(millions, except footnotes)\n" + CUR + "\nTotal current liabilities | January 31, 2026: 21,230\n"
              + NONCUR + "\n")
    f1, f2 = _fact("F1", bs, CUR, 2130, "current_portion"), _fact("F2", bs, NONCUR, 14326, "noncurrent")
    c1, c2 = _x("C1", f1, 1e3, "current_portion"), _x("C2", f2, 1e3, "noncurrent")      # thousands: the slip
    report = ("- Current portion of long-term debt and other borrowings: **$2.13 billion** [F1][C1]\n"
              "- Noncurrent long-term debt and other borrowings: **$14.33 billion** [F2][C2]\n" + total_line)
    traj = _traj(Q_TGT1, [bs], [f1, f2], [c1, c2, _calc("C3", "total_including_current", "a + b", "FY2025", a=c1, b=c2)],
                 report)
    gold = [_gold(bs, "2,130", 2130, 1e6, CUR_C, "Target"), _gold(bs, "14,326", 14326, 1e6, NONCUR_C, "Target")]
    return _score(Q_TGT1, traj, gold)[0]


def test_scale_slip_in_the_log_that_the_report_does_not_carry_is_not_a_failure():
    total = _tgt1("- Total long-term debt including the current portion: **$16.46 billion** [C3] = "
                  "$2.13 billion + $14.33 billion")["$16.46 billion"]
    assert _vbs(total) == (P, P, P) and total.provenance.stage_first_visible == StageAttribution.none
    assert any("x1000 (source x1e+06)" in e for e in total.fact_check.evidence)
    # the same log, but the report carries the slip: right digits, wrong unit, made in calculation (walk-through 2)
    slipped = _tgt1("- Total long-term debt including the current portion: **$16.46 million** [C3]")["$16.46 million"]
    assert _vbs(slipped) == (P, F, F) and slipped.verdicts.binding_failures == ["unit"]
    assert slipped.provenance.stage_first_visible == StageAttribution.calculation


def test_computed_claim_off_the_slot_gold_with_checked_inputs_fails_binding():
    """TGT-1 log with a wrong operation (a - b): copies and arithmetic check out, so the account is wrong."""
    bs = _doc("bs", "(millions, except footnotes)\n" + CUR + "\n" + NONCUR + "\n")
    f1 = _fact("F1", bs, CUR, 2130, "current_portion", concept=CUR_C)
    f2 = _fact("F2", bs, NONCUR, 14326, "noncurrent", concept=NONCUR_C)
    c1, c2 = _x("C1", f1, 1e6, "current_portion"), _x("C2", f2, 1e6, "noncurrent")
    report = "- Total long-term debt including the current portion: **$12.20 billion** [C3]"
    traj = _traj(Q_TGT1, [bs], [f1, f2], [c1, c2, _calc("C3", "total_including_current", "b - a", "FY2025", a=c1, b=c2)],
                 report)
    gold = [_gold(bs, "2,130", 2130, 1e6, CUR_C, "Target"), _gold(bs, "14,326", 14326, 1e6, NONCUR_C, "Target")]
    s = _score(Q_TGT1, traj, gold)[0]["$12.20 billion"]
    assert _vbs(s) == (P, F, F) and s.verdicts.binding_failures == ["concept"]
    assert s.provenance.stage_first_visible == StageAttribution.calculation


# -- CMCSA-1 (DeepSeek): copied from Note 6's billions table, stated to two decimals ---------------------------------
NOTE6 = ("December 31 (in billions) | 2025 (b) | 2024 (b)\nTotal debt | 2025 (b): 98.9 | 2024 (b): 99.1\n"
         "Less: Current portion | 2025 (b): 6.0 | 2024 (b): 4.9\n"
         "Noncurrent portion of debt | 2025 (b): $93.0 | 2024 (b): $94.2\n")
DEBT_C = "us-gaap:DebtCurrent"
Q_CMCSA1 = Question(id="CMCSA-1", filing_type="statement", entity="Comcast", period="FY2025", text="?",
                    target_concepts=[], slots=[
                        AnswerSlot(slot_id="current_portion", concept=DEBT_C, entity="Comcast", period="FY2025",
                                   expected_value=5_958e6),
                        AnswerSlot(slot_id="noncurrent", concept=NONCUR_C, entity="Comcast", period="FY2025",
                                   expected_value=92_979e6),
                        AnswerSlot(slot_id="total_debt", concept="us-gaap:DebtAndCapitalLeaseObligations",
                                   entity="Comcast", period="FY2025", derivation="computed", formula="a + b",
                                   inputs={"a": "current_portion", "b": "noncurrent"}, expected_value=98_937e6)])
CMCSA1_REPORT = ("At the end of FY2025, Comcast’s current portion of debt due within one year was **$6.00 billion** "
                 "[F1][C1]. Its noncurrent debt was **$93.00 billion** [F2][C2]. Adding those slots gives a computed "
                 "total debt of **$99.00 billion** [C3]. The filing’s reported total debt line is **$98.90 billion** "
                 "[F3], with the $0.1 billion difference versus the computed sum reflecting rounding of the components.")


def cmcsa1_case():
    """Note 6 (retrieved, a primary filing, not in the fact store) and the balance sheet (gold, not retrieved)."""
    n6 = _doc("note6", NOTE6)
    bs = _doc("bs", "Current portion of debt | 2025: 5,958 | 2024: 4,907\nNoncurrent portion of debt | 2025: 92,979\n")
    f1 = _fact("F1", n6, "Less: Current portion | 2025 (b): 6.0 | 2024 (b): 4.9", 6.0, "current_portion")
    f2 = _fact("F2", n6, "Noncurrent portion of debt | 2025 (b): $93.0 | 2024 (b): $94.2", 93.0, "noncurrent")
    f3 = _fact("F3", n6, "Total debt | 2025 (b): 98.9 | 2024 (b): 99.1", 98.9, "total_debt")
    c1, c2 = _x("C1", f1, 1e9, "current_portion"), _x("C2", f2, 1e9, "noncurrent")
    traj = _traj(Q_CMCSA1, [n6], [f1, f2, f3], [c1, c2, _calc("C3", "total_debt", "a + b", "FY2025", a=c1, b=c2)],
                 CMCSA1_REPORT)
    gold = [_gold(bs, "5,958", 5958, 1e6, DEBT_C, "Comcast"), _gold(bs, "92,979", 92979, 1e6, NONCUR_C, "Comcast")]
    return traj, gold


def test_stated_precision_and_reported_total():
    traj, gold = cmcsa1_case()
    scored, _ = _score(Q_CMCSA1, traj, gold)
    # gold at two decimals: 5,958 -> 5.96; 92,979 -> 92.98; 98,937 -> 98.94. An unanchored line of a filing is not a
    # flagged source, so retrieval is not blamed.
    for n in ("$6.00 billion", "$93.00 billion", "$98.90 billion"):
        assert _vbs(scored[n]) == (F, P, F), n
        assert scored[n].provenance.stage_first_visible == StageAttribution.synthesis, n
    total = scored["$99.00 billion"]
    assert _vbs(total) == (F, P, F) and total.claim.derivation.kind == DerivationKind.computed
    assert "derivation_mismatch: computed although F3 reports it" in total.verdicts.notes
    assert total.provenance.stage_first_visible == StageAttribution.calculation
    assert scored["$0.1 billion"].claim.slot_id is None and _vbs(scored["$0.1 billion"]) == (NA, NA, NA)


# -- BRK-2 (DeepSeek): the other-manager rows were summed inside extraction -----------------------------------------
Q_BRK2 = Question(id="BRK-2", filing_type="13f", entity="Berkshire", period="2022Q3-2022Q4", text="?",
                  target_concepts=[], slots=[
                      AnswerSlot(slot_id="q3_value", concept="13f:Value[166764100]", entity="Berkshire",
                                 period="2022Q3", expected_value=23_757_173_000, scope="filer"),
                      AnswerSlot(slot_id="q4_value", concept="13f:Value[166764100]", entity="Berkshire",
                                 period="2022Q4", expected_value=29_252_521_137, scope="filer"),
                      AnswerSlot(slot_id="change", concept="13f:ValueChange[166764100]", entity="Berkshire",
                                 period="2022Q3-2022Q4", derivation="computed", formula="b - a",
                                 inputs={"a": "q3_value", "b": "q4_value"}, expected_value=5_495_348_137)])


def test_change_of_values_summed_in_extraction_fails_support_only():
    q3 = _doc("q3", "CHEVRON CORP NEW COM (CUSIP 166764100) | Value: 9,138,809 | Shares or principal (SH): 63,609,725 | "
                    "Investment discretion: DFND | Other managers: #4 #11\n")
    q4 = _doc("q4", "CHEVRON CORP NEW COM (CUSIP 166764100) | Value: 11,417,309,540 | Shares or principal (SH): "
                    "63,609,725 | Investment discretion: DFND | Other managers: #4 #11\n")
    f1 = _fact("F1", q3, q3.text.strip(), 23_757_173_000, "q3_value", "2022Q3")
    f2 = _fact("F2", q4, q4.text.strip(), 29_252_521_137, "q4_value", "2022Q4")
    c1, c2 = _x("C1", f1, 1.0, "q3_value", "2022Q3"), _x("C2", f2, 1.0, "q4_value", "2022Q4")
    report = "- **q3_value:** $23.76 billion [C1]  \n- **q4_value:** $29.25 billion [C2]  \n- **change:** +$5.50 billion [C3]"
    traj = _traj(Q_BRK2, [q3, q4], [f1, f2], [c1, c2, _calc("C3", "change", "b - a", "2022Q3-2022Q4", a=c1, b=c2)],
                 report)
    gold = [_gold(q3, "9,138,809", 9_138_809, 1e3, "13f:Value[166764100]", "Berkshire", "2022Q3", "sub-position"),
            _gold(q4, "11,417,309,540", 11_417_309_540, 1.0, "13f:Value[166764100]", "Berkshire", "2022Q4",
                  "sub-position")]
    change = _score(Q_BRK2, traj, gold)[0]["$5.50 billion"]
    assert _vbs(change) == (P, P, F) and "not_in_quote" in change.verdicts.notes
    assert change.provenance.stage_first_visible == StageAttribution.extraction


# -- citations and repeats --------------------------------------------------------------------------------------------
def test_identity_value_repeats_its_fact():
    """BRK-3 (DeepSeek): the slot table restates the answer through [C1] = x([F1])."""
    q = Question(id="BRK-3", filing_type="13f", entity="Berkshire", period="2023Q3", text="?", target_concepts=[],
                 slots=[AnswerSlot(slot_id="total_value", concept="13f:TableValueTotal", entity="Berkshire",
                                   period="2023Q3", expected_value=314_952_628_264, scope="all filings merged")])
    d = _doc("orig", "Table entry total | 152\nTable value total | 313,257,308,189\n")
    f1 = _fact("F1", d, "Table value total | 313,257,308,189", 313_257_308_189, "total_value", "2023Q3")
    report = ("**Total portfolio value:** $313.26 billion (313,257,308,189 USD) [F1][C1], as reported in the Q3 2023 "
              "13F filing table value total (doc brk-13f-2023q3-010898-s01).\n\n| Slot | Value |\n|---|---|\n"
              "| total_value | $313.26 billion (313,257,308,189 USD) [C1] |")
    traj = _traj(q, [d], [f1], [_x("C1", f1, 1.0, "total_value", "2023Q3")], report)
    assert [c.citation for c in RegexClaimTagger().tag(report, q, traj)] == ["F1"]


def test_unlocated_extraction_note_gives_way_to_the_computed_value():
    """TGT-2 (DeepSeek): extraction logged its own arithmetic as a fact (no document); the report cites [F3] / [C3]."""
    ocf = "us-gaap:NetCashProvidedByUsedInOperatingActivities"
    q = Question(id="TGT-2", filing_type="statement", entity="Target", period="Q4 FY2025", text="?", target_concepts=[],
                 slots=[AnswerSlot(slot_id="ocf_fy", concept=ocf, entity="Target", period="FY2025",
                                   expected_value=6_562e6),
                        AnswerSlot(slot_id="ocf_9m", concept=ocf, entity="Target", period="Q3 FY2025",
                                   expected_value=3_485e6),
                        AnswerSlot(slot_id="q4_operating_cash_flow", concept=ocf, entity="Target", period="Q4 FY2025",
                                   derivation="computed", formula="b - a", inputs={"a": "ocf_9m", "b": "ocf_fy"},
                                   expected_value=3_077e6)])
    k = _doc("10k", "Cash provided by operating activities | 2025: 6,562 | 2024: 7,367 | 2023: 8,621\n")
    t = _doc("10q", "Cash provided by operating activities | November 1, 2025: 3,485 | November 2, 2024: 4,078\n")
    f1, f2 = _fact("F1", k, k.text.strip(), 6562, "ocf_fy"), _fact("F2", t, t.text.strip(), 3485, "ocf_9m", "Q3 FY2025")
    f3 = _fact("F3", None, "6,562 - 3,485 = 3,077", 3077, "q4_operating_cash_flow", "Q4 FY2025")
    c1, c2 = _x("C1", f1, 1e6, "ocf_fy"), _x("C2", f2, 1e6, "ocf_9m", "Q3 FY2025")
    report = ("- **ocf_fy**: $6,562.00 million [F1] / [C1]  \n- **ocf_9m**: $3,485.00 million [F2] / [C2]  \n"
              "- **q4_operating_cash_flow**: $3,077.00 million [F3] / [C3]  \n\nTarget generated **$3,077.00 million** "
              "in cash from operations in Q4 FY2025, computed as FY2025 operating cash flow of **$6,562.00 million** "
              "minus 9M FY2025 operating cash flow of **$3,485.00 million** [F3].")
    traj = _traj(q, [k, t], [f1, f2, f3],
                 [c1, c2, _calc("C3", "q4_operating_cash_flow", "b - a", "Q4 FY2025", a=c2, b=c1)], report)
    gold = [_gold(k, "6,562", 6562, 1e6, ocf, "Target"), _gold(t, "3,485", 3485, 1e6, ocf, "Target", "Q3 FY2025")]
    claims, _ = extract_and_align(report, q, traj)
    assert [c.number_text for c in claims] == ["$6,562.00 million", "$3,485.00 million", "$3,077.00 million"]
    q4 = score_claims(claims, q, traj, StubFactChecker(gold))[2]
    assert q4.claim.citation == "C3" and _vbs(q4) == (P, P, P)


def test_hedged_number_gets_one_unit_of_its_last_digit():
    """Opus TGT-2: 'about' allows +-1 in the last stated digit (guide 6.1)."""
    q = Question(id="TGT-2", filing_type="statement", entity="Target", period="Q4 FY2025", text="?", target_concepts=[],
                 slots=[])
    traj = _traj(q, [], [], [], "")
    c = RegexClaimTagger().tag("Subtracting the nine-month amount from the full-year amount gives about **$3.08 "
                               "billion** ($6.56 billion − $3.49 billion) [C3].", q, traj)
    assert len(c) == 1 and abs(c[0].rel_tol * c[0].value - 0.01e9) < 1


# -- mock pilot on the real corpus (pilot_mock.toml): wrong lines and single rows ------------------------------------
def test_unanchored_wrong_line_is_still_blamed_on_extraction():
    """TGT-4 (mock): a proxy compensation total cited as the plan liability while Note 24 was retrieved."""
    proxy = _doc("proxy", "Mr. Cornell | Company matching contributions: $186,186 | Life insurance: $45,720 | SPP "
                          "adjustments: $0 | Severance: $0 | Perquisites: $392,982 | Total: $624,888\n")
    note = _doc("note24", LIAB)
    f1 = _fact("F1", proxy, proxy.text.strip(), 624888, "plan_liability")
    report = "Target Corporation's deferred compensation obligation for FY2025 was $624,888 [F1]."
    traj = _traj(Q_TGT4, [proxy, note], [f1], [_x("C1", f1, 1.0, "plan_liability")], report)
    s = _score(Q_TGT4, traj, [_gold(note, "717", 717, 1e6, "nqdc:PlanLiability", "Target", scope="company-wide")])[0]
    assert _vbs(s["$624,888"]) == (F, P, F)
    assert s["$624,888"].provenance.stage_first_visible == StageAttribution.extraction


def test_computed_from_a_wrong_but_correctly_copied_line_fails_binding():
    """CMCSA-2 (mock): Q4 capex = FY capex - an operating-income line; both copies are right, the account is not."""
    proxy = _doc("proxy", "($ in millions)\nCapital expenditures | 2025: (11,750)\n")
    q10 = _doc("10q-ops", "(in millions)\nOperating income | 2025: $17,184\n")
    f1 = _fact("F1", proxy, "Capital expenditures | 2025: (11,750)", -11750, "capex_fy")
    f2 = _fact("F2", q10, "Operating income | 2025: $17,184", 17184, "capex_9m", "Q3 FY2025")
    c1, c2 = _x("C1", f1, 1e6, "capex_fy"), _x("C2", f2, 1e6, "capex_9m", "Q3 FY2025")
    report = ("From Q3 FY2025 to FY2025, Comcast Corporation's nine-month capital expenditures fell from $17.2 billion "
              "[F2] to -$11.8 billion [F1], a change of -$28.9 billion [C3].")
    traj = _traj(Q_CAPEX, [proxy, q10], [f1, f2], [c1, c2, _calc("C3", "q4_capex", "b - a", "Q4 FY2025", a=c2, b=c1)],
                 report)
    s = _score(Q_CAPEX, traj, [])[0]["-$28.9 billion"]
    assert _vbs(s) == (P, F, F) and s.verdicts.binding_failures == ["concept"]


def test_change_of_single_other_manager_rows_fails_scope_at_extraction():
    """BRK-2 (mock): the change is computed from one other-manager row per quarter (guide 4.1)."""
    q3 = _doc("q3", "CHEVRON CORP NEW COM (CUSIP 166764100) | Value: 125,999\n")
    q4 = _doc("q4", "CHEVRON CORP NEW COM (CUSIP 166764100) | Value: 157,412,730\n")
    f1 = _fact("F1", q3, q3.text.strip(), 125_999, "q3_value", "2022Q3")
    f2 = _fact("F2", q4, q4.text.strip(), 157_412_730, "q4_value", "2022Q4")
    c1, c2 = _x("C1", f1, 1e3, "q3_value", "2022Q3"), _x("C2", f2, 1.0, "q4_value", "2022Q4")
    report = ("From 2022Q3 to 2022Q4, Berkshire Hathaway Inc's Chevron stake rose from $126 million [F1] to $157 million "
              "[F2], a change of $31.4 million [C3].")
    traj = _traj(Q_BRK2, [q3, q4], [f1, f2], [c1, c2, _calc("C3", "change", "b - a", "2022Q3-2022Q4", a=c1, b=c2)],
                 report)
    gold = [_gold(q3, "125,999", 125_999, 1e3, "13f:Value[166764100]", "Berkshire", "2022Q3", "sub-position"),
            _gold(q4, "157,412,730", 157_412_730, 1.0, "13f:Value[166764100]", "Berkshire", "2022Q4", "sub-position")]
    s = _score(Q_BRK2, traj, gold)[0]["$31.4 million"]
    assert _vbs(s) == (P, F, F) and s.verdicts.binding_failures == ["scope"]
    assert s.provenance.stage_first_visible == StageAttribution.extraction
