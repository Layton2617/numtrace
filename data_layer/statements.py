"""10-K / 10-Q fact store: companyfacts JSON -> facts, the concept binding table, and
derivations (marked derived, never presented as reported).

The companyfacts format (SEC XBRL API, /api/xbrl/companyfacts/CIK##########.json):
  {"cik": 320193, "entityName": "...",
   "facts": {"us-gaap": {"<Concept>": {"label", "description",
             "units": {"USD": [{"start"?, "end", "val", "accn", "fy", "fp", "form",
                                "filed", "frame"?}, ...]}}}}}
The same fact is repeated once per filing that reports it (e.g. a balance in the 10-K and
again as the comparative in later 10-Qs); each repetition is kept with its accession.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel

from .facts import Fact



def _d(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def cik10(cik: Union[int, str]) -> str:
    return str(int(cik)).zfill(10)


class StatementFact(Fact):
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None
    frame: Optional[str] = None


def load_companyfacts(src: Union[str, Path, dict],
                      taxonomies: tuple[str, ...] = ("us-gaap", "dei")) -> list[StatementFact]:
    """Flatten a companyfacts JSON (path or parsed dict) into one row per reported fact."""
    data = src if isinstance(src, dict) else json.loads(Path(src).read_text())
    entity = cik10(data["cik"])
    out = []
    for tax in taxonomies:
        for concept, body in data.get("facts", {}).get(tax, {}).items():
            for unit, items in body.get("units", {}).items():
                for it in items:
                    out.append(StatementFact(
                        entity=entity, concept=f"{tax}:{concept}", value=it["val"], unit=unit,
                        period_start=_d(it.get("start")), period_end=_d(it["end"]),
                        accession=it.get("accn"), form=it.get("form"), filed=_d(it.get("filed")),
                        fiscal_year=it.get("fy"), fiscal_period=it.get("fp"), frame=it.get("frame"),
                        label=body.get("label"),
                    ))
    return out


def facts_to_frame(facts: list[Fact]):
    """The fact store as a pandas DataFrame (concept, unit, value, start, end, fp, form,
    filed, accession, ...)."""
    import pandas as pd
    return pd.DataFrame([f.model_dump() for f in facts])



class ConceptBinding(BaseModel):
    metric: str
    description: str
    concepts: list[str]
    unit: str
    period_type: Literal["instant", "duration"]
    additive: bool
    derivation: Optional[str] = None
    not_to_confuse_with: list[str] = []
    sum_of: list[str] = []
    derived_concept: Optional[str] = None


CONCEPT_BINDINGS: dict[str, ConceptBinding] = {b.metric: b for b in [
    ConceptBinding(
        metric="total_long_term_debt",
        description="Total long-term debt including the current portion (carrying amount)",
        concepts=["us-gaap:LongTermDebt"], unit="USD", period_type="instant", additive=False,
        derivation="LongTermDebtNoncurrent + LongTermDebtCurrent",
        not_to_confuse_with=["us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebtCurrent",
                             "us-gaap:DebtInstrumentFaceAmount"]),
    ConceptBinding(
        metric="long_term_debt_noncurrent",
        description="Long-term debt, net of current portion (balance-sheet noncurrent line)",
        concepts=["us-gaap:LongTermDebtNoncurrent"], unit="USD", period_type="instant",
        additive=False, not_to_confuse_with=["us-gaap:LongTermDebt"]),
    ConceptBinding(
        metric="long_term_debt_current",
        description="Current portion of long-term debt",
        concepts=["us-gaap:LongTermDebtCurrent"], unit="USD", period_type="instant",
        additive=False, not_to_confuse_with=["us-gaap:LongTermDebt"]),
    ConceptBinding(
        metric="cash_and_cash_equivalents",
        description="Cash and cash equivalents (balance sheet)",
        concepts=["us-gaap:CashAndCashEquivalentsAtCarryingValue"], unit="USD",
        period_type="instant", additive=False,
        not_to_confuse_with=["us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    ConceptBinding(
        metric="eps_diluted",
        description="Diluted earnings per share",
        concepts=["us-gaap:EarningsPerShareDiluted"], unit="USD/shares", period_type="duration",
        additive=False, derivation=None,
        not_to_confuse_with=["us-gaap:EarningsPerShareBasic"]),
    ConceptBinding(
        metric="net_income",
        description="Net income (loss) attributable to the parent",
        concepts=["us-gaap:NetIncomeLoss"], unit="USD", period_type="duration", additive=True,
        derivation="discrete Q4 = FY - 9M YTD",
        not_to_confuse_with=["us-gaap:ProfitLoss"]),
    ConceptBinding(
        metric="deferred_comp_liability_total",
        description="Deferred compensation liability, current and noncurrent",
        concepts=["us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent"], unit="USD",
        period_type="instant", additive=False,
        derivation="DeferredCompensationLiabilityCurrent + DeferredCompensationLiabilityClassifiedNoncurrent",
        not_to_confuse_with=["us-gaap:DeferredCompensationLiabilityClassifiedNoncurrent",
                             "us-gaap:CashSurrenderValueOfLifeInsurance"]),
    ConceptBinding(
        metric="deferred_comp_liability_noncurrent",
        description="Deferred compensation liability, noncurrent",
        concepts=["us-gaap:DeferredCompensationLiabilityClassifiedNoncurrent"], unit="USD",
        period_type="instant", additive=False,
        not_to_confuse_with=["us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent"]),
    ConceptBinding(
        metric="coli_cash_surrender_value",
        description="Cash surrender value of corporate-owned life insurance (an asset)",
        concepts=["us-gaap:CashSurrenderValueOfLifeInsurance"], unit="USD",
        period_type="instant", additive=False,
        not_to_confuse_with=["us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent"]),
]}

_SUM_DERIVATIONS = {
    "total_long_term_debt": ["us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebtCurrent"],
    "deferred_comp_liability_total": ["us-gaap:DeferredCompensationLiabilityCurrent",
                                      "us-gaap:DeferredCompensationLiabilityClassifiedNoncurrent"],
}


TARGET = "0000027419"
_TGT_NONCURRENT = "us-gaap:LongTermDebtAndCapitalLeaseObligations"
_TGT_CURRENT = "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent"

ENTITY_BINDINGS: dict[tuple[str, str], ConceptBinding] = {
    (TARGET, "total_long_term_debt"): CONCEPT_BINDINGS["total_long_term_debt"].model_copy(update=dict(
        description="Total long-term debt and other borrowings including the current portion. Target reports no "
                    "total; it tags the Note 17 subtotal 'Total notes and debentures' (before swap adjustments and "
                    "finance leases) as us-gaap:LongTermDebt, which is not this metric",
        concepts=[], sum_of=[_TGT_NONCURRENT, _TGT_CURRENT],
        derived_concept="us-gaap:LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        derivation="LongTermDebtAndCapitalLeaseObligations + LongTermDebtAndCapitalLeaseObligationsCurrent",
        not_to_confuse_with=["us-gaap:LongTermDebt", _TGT_NONCURRENT, _TGT_CURRENT])),
    (TARGET, "long_term_debt_noncurrent"): CONCEPT_BINDINGS["long_term_debt_noncurrent"].model_copy(update=dict(
        description="Long-term debt and other borrowings, noncurrent (balance-sheet line)",
        concepts=[_TGT_NONCURRENT], not_to_confuse_with=["us-gaap:LongTermDebt"])),
    (TARGET, "long_term_debt_current"): CONCEPT_BINDINGS["long_term_debt_current"].model_copy(update=dict(
        description="Current portion of long-term debt and other borrowings",
        concepts=[_TGT_CURRENT], not_to_confuse_with=["us-gaap:LongTermDebt"])),
}


def binding_for_entity(entity: Optional[str], metric: str) -> ConceptBinding:
    """The entity's own binding of a metric where one is defined, else the default."""
    return ENTITY_BINDINGS.get((entity or "", metric), CONCEPT_BINDINGS[metric])


def binding_for(metric_or_concept: str) -> Optional[ConceptBinding]:
    """Look up a binding by metric key or by one of its concepts."""
    if metric_or_concept in CONCEPT_BINDINGS:
        return CONCEPT_BINDINGS[metric_or_concept]
    for b in CONCEPT_BINDINGS.values():
        if metric_or_concept in b.concepts:
            return b
    return None



def _duration_days(f: Fact) -> Optional[int]:
    return (f.period_end - f.period_start).days if f.period_start else None


def select(facts: list[Fact], entity: str, concept: str, period_end: date,
           period_start: Optional[date] = None, policy: Literal["first", "latest"] = "first"
           ) -> Optional[Fact]:
    """One fact for (entity, concept, period). The same fact repeats across filings;
    policy "first" takes the first-filed (as originally reported), "latest" the most
    recently filed (as restated/re-presented)."""
    cands = [f for f in facts if f.entity == entity and f.concept == concept
             and f.period_end == period_end
             and (period_start is None or f.period_start == period_start)]
    if not cands:
        return None
    cands.sort(key=lambda f: (f.filed or date.min, f.accession or ""))
    return cands[0] if policy == "first" else cands[-1]


class MetricValue(BaseModel):
    status: Literal["reported", "derived", "not_available"]
    fact: Optional[Fact] = None
    inputs: list[Fact] = []
    note: str = ""


def get_metric(facts: list[Fact], entity: str, metric: str, period_end: date,
               period_start: Optional[date] = None, policy: Literal["first", "latest"] = "first"
               ) -> MetricValue:
    """Reported value of a pilot metric, else a sum derivation marked derived."""
    b = binding_for_entity(entity, metric)
    for c in b.concepts:
        f = select(facts, entity, c, period_end, period_start, policy)
        if f is not None:
            return MetricValue(status="reported", fact=f)
    parts = b.sum_of or _SUM_DERIVATIONS.get(metric)
    if parts:
        inputs = [select(facts, entity, c, period_end, period_start, policy) for c in parts]
        if all(inputs):
            return MetricValue(status="derived", inputs=inputs, fact=Fact(
                entity=entity, concept=b.derived_concept or b.concepts[0], value=sum(i.value for i in inputs),
                unit=b.unit, period_end=period_end, period_start=period_start,
                derived=True, derivation=b.derivation,
                accession=",".join(sorted({i.accession for i in inputs if i.accession}))))
    return MetricValue(status="not_available", note=f"{metric}: no reported or derivable value")


def derive_q4(facts: list[Fact], entity: str, metric: str, fy_end: date,
              policy: Literal["first", "latest"] = "first") -> MetricValue:
    """Discrete fourth quarter of an additive duration metric: FY minus 9-month YTD.

    Returns a reported value if a ~3-month fact ending at fy_end exists; otherwise a
    derived fact; not_available for non-additive metrics such as EPS."""
    b = CONCEPT_BINDINGS[metric]
    if b.period_type != "duration":
        return MetricValue(status="not_available", note=f"{metric} is an instant; no Q4 flow")
    for c in b.concepts:
        q4 = [f for f in facts if f.entity == entity and f.concept == c and f.period_end == fy_end
              and _duration_days(f) is not None and 80 <= _duration_days(f) <= 100]
        if q4:
            q4.sort(key=lambda f: (f.filed or date.min))
            return MetricValue(status="reported", fact=q4[0 if policy == "first" else -1])
    if not b.additive:
        return MetricValue(status="not_available",
                           note=f"{metric} is not additive; Q4 cannot be derived as FY - 9M")
    for c in b.concepts:
        fys = [f for f in facts if f.entity == entity and f.concept == c and f.period_end == fy_end
               and _duration_days(f) and 350 <= _duration_days(f) <= 380]
        if not fys:
            continue
        fy = sorted(fys, key=lambda f: f.filed or date.min)[0 if policy == "first" else -1]
        nine = [f for f in facts if f.entity == entity and f.concept == c
                and f.period_start == fy.period_start and _duration_days(f)
                and 260 <= _duration_days(f) <= 290]
        if not nine:
            continue
        ytd = sorted(nine, key=lambda f: f.filed or date.min)[0 if policy == "first" else -1]
        return MetricValue(status="derived", inputs=[fy, ytd], fact=Fact(
            entity=entity, concept=c, value=fy.value - ytd.value, unit=fy.unit,
            period_start=ytd.period_end + timedelta(days=1), period_end=fy_end,
            derived=True, derivation="discrete Q4 = FY - 9M YTD",
            accession=f"{fy.accession},{ytd.accession}"))
    return MetricValue(status="not_available", note=f"{metric}: FY or 9M YTD fact missing")
