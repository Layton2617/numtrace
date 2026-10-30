"""Data-layer and gold-side scorer fixes from the audit of the Opus Step 1 run (cells, concept aliases, 13F rows).
Quotes are real ones from harness/runs/step1/opus-cli/*/claims.jsonl."""
from datetime import date

from data_layer.facts import Fact
from data_layer.harness_adapter import Anchor, DataLayerFactChecker
from harness.schema import (AnswerSlot, BindingField, Claim, Derivation, DerivationInput, DerivationKind,
                            FactCheckResult, Question, Span, Verdict)
from step1.fact_checker import GoldChecker

P, F = Verdict.pass_, Verdict.fail
CVX = "Chevron Corporation"
ROW = "Capital expenditures | ( 16,448 ) | ( 15,829 ) | ( 11,974 )"


def _anchor(doc, start, printed, period, concept, entity="x", scope=None, pend=date(2024, 12, 31), pstart=None):
    f = Fact(entity="0000093410", concept=concept, value=printed * 1e6, unit="USD", period_end=pend,
             period_start=pstart)
    return Anchor(doc_id=doc, start=start, end=start + 10, label="row", printed_value=printed, scale=1e6,
                  entity=entity, period=period, scope=scope, fact=f)


def test_cell_with_the_number_in_either_sign_then_period_then_concept():
    capex = "us-gaap:PaymentsToAcquireProductiveAssets"
    chk = DataLayerFactChecker([_anchor("d", 1256, -16448, "FY2024", capex, CVX),
                                _anchor("d", 1269, -15829, "FY2023", capex, CVX, pend=date(2023, 12, 31)),
                                _anchor("d", 1282, -11974, "FY2022", capex, CVX, pend=date(2022, 12, 31))])
    span = Span(doc_id="d", start=1233, end=1292, text=ROW)
    assert chk._narrow(span, 16448.0).start == 1256              # "( 16,448 )" is anchored as -16448
    # two cells print the same number (Apple diluted EPS 1.52 in both quarters): the agent's period decides
    same = DataLayerFactChecker([_anchor("e", 10, 1.52, "Q2 FY2023", "eps", "Apple Inc.", pend=date(2023, 4, 1)),
                                 _anchor("e", 30, 1.52, "Q2 FY2022", "eps", "Apple Inc.", pend=date(2022, 3, 26))])
    e = Span(doc_id="e", start=0, end=50, text="Diluted | 2023: $1.52 | 2022: $1.52")
    assert same._narrow(e, 1.52, "Q2 FY2022 (three months ended March 26, 2022)").start == 30
    # ... or the claim's concept (registrant = executive contributions, 60,046 both)
    nq = DataLayerFactChecker([_anchor("n", 10, 60046, "FY2023", "nqdc:executive_contributions"),
                               _anchor("n", 30, 60046, "FY2023", "nqdc:registrant_contributions")])
    n = Span(doc_id="n", start=0, end=50, text="total | Executive: $60,046 | Registrant: $60,046")
    assert nq._narrow(n, 60046.0, "FY2023", "nqdc:RegistrantContributions").start == 30
    # a zero printed as a dash no anchor covers is none of the row's other cells
    assert nq._narrow(n, 0.0).start == n.end


def test_line_also_reported_under_the_slot_concept():
    basic = _anchor("x", 753, 4.68, "Q3 FY2022", "us-gaap:EarningsPerShareBasic", pend=date(2022, 9, 30),
                    pstart=date(2022, 7, 1))
    diluted = basic.fact.model_copy(update={"concept": "us-gaap:EarningsPerShareDiluted"})
    chk = DataLayerFactChecker([basic], facts=[basic.fact, diluted])
    g = basic.gold()
    assert chk.also_reported(g, "us-gaap:EarningsPerShareDiluted")
    assert not chk.also_reported(g, "us-gaap:IncomeLossFromContinuingOperationsPerDilutedShare")
    assert not DataLayerFactChecker([basic]).also_reported(g, "us-gaap:EarningsPerShareDiluted")


def _claim(value, text="", source=None, inputs=(), formula=None, slot="s"):
    kind = DerivationKind.computed if formula else DerivationKind.reported
    return Claim(claim_id="c", question_id="q", trajectory_id="t", text=text, number_text=f"{value:,}", value=value,
                 rel_tol=0.5 / value if value else 0.0, slot_id=slot, source=source,
                 derivation=Derivation(kind=kind, formula=formula, inputs=list(inputs)))


def _res(fails, value=P):
    return FactCheckResult(value=value, binding=F if fails else P, support=F, binding_failures=fails)


def test_zero_cell_and_group_single_row():
    gc = GoldChecker(None, [])
    g0 = {"value": 0.0}
    assert gc._zero_cell(_claim(0.0), _res([BindingField.concept]), g0, [])
    assert gc._zero_cell(_claim(0.0), _res([BindingField.concept]), {"value": None}, [0.0])
    assert not gc._zero_cell(_claim(0.0), _res([BindingField.concept]), {"value": 5.0}, [])
    assert not gc._zero_cell(_claim(7.0), _res([BindingField.concept]), g0, [])
    text = ("VISA INC COM CL A (CUSIP 92826C839) | Value: 1,122,065,552 | Shares or principal (SH): 3,270,470")
    src = Span(doc_id="pscm-13f-2026q2-0001172661-26-003790-s001", start=0, end=len(text), text=text)
    g = {"value": 3270470, "cusip": "92826C839", "rows": [{"accession": "0001172661-26-003790"}]}
    fails = [BindingField.entity, BindingField.scope]
    assert gc._group_row(_claim(3270470.0, source=src), _res(fails), g)
    assert not gc._group_row(_claim(3270470.0, source=src), _res(fails), {**g, "rows": g["rows"] * 2})
    assert not gc._group_row(_claim(8939.0, source=src), _res(fails), g)


def test_filer_total_rows_on_the_slot_itself():
    q = Question(id="q", filing_type="13f", entity="Ameriprise", period="2022Q3", text="?", target_concepts=[],
                 slots=[AnswerSlot(slot_id="shares", concept="13f:Shares[91324P102]", entity="Ameriprise",
                                   period="2022Q3", unit="shares")])
    rows = [("UNITEDHEALTH GROUP INC COM (CUSIP 91324P102) | Value: 1,799,429 | Shares or principal (SH): 3,562,945",
             3562945), ("UNITEDHEALTH GROUP INC COM (CUSIP 91324P102) | Value: 34,123 | Shares or principal (SH): 67,565",
                        67565)]
    ins = [DerivationInput(ref=f"F{i}", var=v, value=n, printed_value=n,
                           span=Span(doc_id="amp-13f-2022q3-0000950123-22-011781-s081", start=0, end=len(t), text=t))
           for i, (v, (t, n)) in enumerate(zip("ab", rows))]
    g = {"value": 3630510, "unit": "shares", "cusip": "91324P102", "period": "2022Q3",
         "rows": [{"accession": "0000950123-22-011781", "value": 1799429, "shares": 3562945},
                  {"accession": "0000950123-22-011781", "value": 34123, "shares": 67565}]}
    gc = GoldChecker(None, [])
    c = _claim(3630510.0, "Ameriprise held 3,630,510 shares", inputs=ins, formula="a + b", slot="shares")
    assert gc._gold_rows(c, q, g)
    assert gc._gold_rows(c.model_copy(update={"value": 3630511.0}), q, g) is None
