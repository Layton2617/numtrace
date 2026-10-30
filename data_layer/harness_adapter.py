"""The data layer as the harness's fact checker (harness.scoring.FactChecker) and tier-2 lookup.

The harness judges a claim by the number printed at its cited span; the data layer holds facts by
(entity, concept, period). An ``Anchor`` joins the two: fact F is printed at doc[start:end] as P,
and P x scale = F.value. For 13F the scale follows the filing date (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03) unless the fact carries
an as-filed unit that says otherwise.

Span-level verdicts reuse the harness's reference logic (``StubFactChecker``) over the anchors.
When the cited source is not anchored and the number does not match the slot's gold fact, the whole
data-layer store is searched with ``check_claim``, so a number taken from a table we did not anchor
can still be identified as the right number under the wrong account.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from harness.fixtures import GoldFact
from harness.periods import same_period
from harness.schema import (Binding, BindingField, Claim, Doc, FactCheckResult, Question, Span,
                            Verdict)
from harness.scoring import NOT_IN_QUOTE, StubFactChecker
from harness.tools import detect_unit_text, parse_table_lines, scale_from_unit_text, scale_invariant_match
from pydantic import BaseModel

from .binding_check import Claim as DLClaim, check_claim
from .facts import Fact
from .thirteen_f import POSITION_SHARES, POSITION_VALUE, USD_THOUSANDS, value_unit_for_filing_date

P, F = Verdict.pass_, Verdict.fail

NQDC_CONCEPTS = {
    "us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent": "nqdc:PlanLiability",
    "us-gaap:CashSurrenderValueOfLifeInsurance": "nqdc:COLICashSurrenderValue",
    "nqdc:FundingAsset:coli_cash_surrender_value": "nqdc:COLICashSurrenderValue",
    "nqdc:executive_contributions": "nqdc:ExecutiveContributions",
    "nqdc:registrant_contributions": "nqdc:RegistrantContributions",
    "nqdc:aggregate_earnings": "nqdc:AggregateEarnings",
    "nqdc:aggregate_withdrawals": "nqdc:AggregateWithdrawals",
    "nqdc:aggregate_balance": "nqdc:AggregateBalance",
}
_DIM_FIELD = {"entity": BindingField.entity, "concept": BindingField.concept, "period": BindingField.period,
              "unit": BindingField.unit}


def harness_concept(f: Fact) -> str:
    if f.concept == POSITION_VALUE:
        return f"13f:Value[{f.dimensions['cusip']}]"
    if f.concept == POSITION_SHARES:
        return f"13f:Shares[{f.dimensions['cusip']}]"
    return NQDC_CONCEPTS.get(f.concept, f.concept)


def _is_13f(f: Fact) -> bool:
    return f.concept.startswith("13f:") or (f.form or "").upper().startswith("13F")


def scale_for(fact: Fact, doc: Doc) -> float:
    """Printed -> base multiplier of a fact as printed in ``doc``."""
    if fact.unit == "shares":
        return 1.0
    if _is_13f(fact):
        unit = fact.original_unit or value_unit_for_filing_date(doc.filing_date)
        return 1e3 if unit == USD_THOUSANDS else 1.0
    return scale_from_unit_text(detect_unit_text(doc.text))


class Anchor(BaseModel):
    """One data-layer fact at one span of a snapshot doc, with the harness's names for its binding."""

    doc_id: str
    start: int
    end: int
    label: str
    printed_value: float
    scale: float
    entity: str
    period: str
    scope: Optional[str] = None
    fact: Fact

    @property
    def concept(self) -> str:
        return harness_concept(self.fact)

    def gold(self) -> GoldFact:
        return GoldFact(fact_id=f"{self.doc_id}@{self.start}", doc_id=self.doc_id, start=self.start, end=self.end,
                        label=self.label, printed_value=self.printed_value, scale=self.scale,
                        value=self.printed_value * self.scale, concept=self.concept, entity=self.entity,
                        period=self.period, unit="shares" if self.fact.unit == "shares" else "USD",
                        scope=self.scope, period_start=self.fact.period_start, period_end=self.fact.period_end)


def anchor_table_lines(doc: Doc, facts: dict[str, Fact], *, entity: str, period: str,
                       scope: Optional[str] = None) -> list[Anchor]:
    """Anchor facts to the doc's ``Label ..... 1,234`` lines; ``facts`` maps a label prefix to its fact.
    Raises when a printed number disagrees with its fact, so a wrong anchor cannot enter the gold."""
    out = []
    for ln in parse_table_lines(doc.text):
        fact = next((f for k, f in facts.items() if ln.label.startswith(k)), None)
        if fact is None:
            continue
        s = scale_for(fact, doc)
        if abs(ln.value * s - fact.value) > 0.5 * s:
            raise ValueError(f"{doc.doc_id}: '{ln.label}' prints {ln.value:g} x{s:g}, fact says {fact.value:g}")
        out.append(Anchor(doc_id=doc.doc_id, start=ln.start, end=ln.end, label=ln.label, printed_value=ln.value,
                          scale=s, entity=entity, period=period, scope=scope, fact=fact))
    return out


class DataLayerFactChecker:
    """``fact_check(claim, question) -> FactCheckResult`` plus ``lookup(span) -> Binding | None``.

    ``facts`` is the full data-layer store searched for unanchored numbers (defaults to the anchored facts).
    """

    def __init__(self, anchors: Iterable[Anchor], facts: Optional[Iterable[Fact]] = None,
                 flagged_docs: Optional[dict[str, str]] = None, primary_docs: Optional[dict[str, str]] = None) -> None:
        self.anchors = list(anchors)
        self.facts = list(facts) if facts is not None else [a.fact for a in self.anchors]
        self._at = {(a.doc_id, a.start): a for a in self.anchors}
        self._values: dict[tuple, set[float]] = {}
        for f in self.facts:
            if not f.dimensions:
                self._values.setdefault((f.entity, harness_concept(f), f.period_start, f.period_end), set()).add(f.value)
        self._stub = StubFactChecker([a.gold() for a in self.anchors], flagged_docs, primary_docs, self.also_reported)

    def also_reported(self, g: GoldFact, concept: Optional[str]) -> bool:
        """The anchored line's value is also reported under ``concept`` for the same entity and period (the filer
        tags one printed line with two elements: EarningsPerShareBasic and EarningsPerShareDiluted, DebtCurrent and
        LongTermDebtCurrent)."""
        a = self._at.get((g.doc_id, g.start))
        if a is None or concept is None or a.fact.dimensions:
            return False
        f = a.fact
        return f.value in self._values.get((f.entity, concept, f.period_start, f.period_end), ())

    def __call__(self, claim: Claim, question: Question) -> FactCheckResult:
        return self.fact_check(claim, question)

    def fact_check(self, claim: Claim, question: Question) -> FactCheckResult:
        res = self._stub(self._narrowed(claim), question)
        # a zero matches any zero-valued fact of the filer: the store cannot tell which account it is
        if res.value == F and {"not_in_fact_store", NOT_IN_QUOTE} & set(res.source_flags) and claim.slot_id and \
                claim.value != 0:
            res = self._search_store(claim, question.slot(claim.slot_id), res) or res
        if res.gold_value is None and claim.slot_id:
            slot = question.slot(claim.slot_id)
            target = self._slot_anchor(slot.entity, slot.concept, slot.period)
            res.gold_value = target.fact.value if target else None
        return res

    def _narrow(self, span: Optional[Span], printed: Optional[float], period: Optional[str] = None,
                concept: Optional[str] = None) -> Optional[Span]:
        """A quote that covers several table cells ('Row | A: 1 | B: 2') is resolved to the cell holding the
        printed number the claim used (either sign: "( 16,448 )" is anchored as -16448), among several such cells to
        the one of the claim's concept and the period the agent bound it to, then to the cell the quote ends on."""
        if span is None or span.start < 0:
            return span
        hits = [a for a in self.anchors if a.doc_id == span.doc_id and a.start < span.end and span.start < a.end]
        if len(hits) < 2:
            return span
        pick = [a for a in hits if printed is not None and abs(a.printed_value) == abs(printed)]
        if len({(a.start, a.end) for a in pick}) > 1 and concept:
            pick = [a for a in pick if a.concept == concept] or pick
        if len({(a.start, a.end) for a in pick}) > 1 and period:
            pick = [a for a in pick if same_period(period, a.period, a.entity, a.fact.period_start,
                                                   a.fact.period_end)] or pick
        if not pick and printed == 0:
            # a zero printed as a dash no anchor covers: it is none of the row's other cells
            return Span(doc_id=span.doc_id, start=span.end, end=span.end, text=span.text)
        pick = pick or [a for a in hits if a.end == span.end]
        if not pick:
            return span
        a = pick[0]
        return Span(doc_id=a.doc_id, start=a.start, end=a.end, text=span.text)

    def _narrowed(self, claim: Claim) -> Claim:
        ins = claim.derivation.inputs
        printed = ins[0].printed_value if len(ins) == 1 else None
        new_inputs = [i.model_copy(update={"span": self._narrow(i.span, i.printed_value, i.period)}) for i in ins]
        return claim.model_copy(update={"source": self._narrow(claim.source, printed, claim.period,
                                                               claim.concept_as_stated),
                                        "derivation": claim.derivation.model_copy(update={"inputs": new_inputs})})

    def lookup(self, span: Span) -> Optional[Binding]:
        g = self._stub.at_span(span)
        return None if g is None else Binding(entity=g.entity, concept=g.concept, period=g.period, unit=g.unit,
                                              scale=g.scale, scope=g.scope)

    def _slot_anchor(self, entity: str, concept: str, period: str) -> Optional[Anchor]:
        return next((a for a in self.anchors if a.entity == entity and a.concept == concept and a.period == period
                     and a.doc_id not in self._stub.flagged_docs), None)

    def _search_store(self, claim: Claim, slot, res: FactCheckResult) -> Optional[FactCheckResult]:
        target = self._slot_anchor(slot.entity, slot.concept, slot.period)
        if target is None:
            return None
        t = target.fact
        r = check_claim(DLClaim(entity=t.entity, concept_as_stated=t.concept, period_end=t.period_end,
                                period_start=t.period_start, unit=t.unit, value=claim.value,
                                dimensions=t.dimensions), [f for f in self.facts if f.entity == t.entity])
        # check_claim reads the precision off the float ("$6.00 billion" as 6e9, +-5e7); the claim states its own
        if not r.value_ok or \
                scale_invariant_match(claim.value, r.nearest_reading[0], claim.rel_tol, scales=(1.0,)) is None:
            return None
        nf = r.nearest_fact
        at = next((a for a in self.anchors if a.fact == nf), None)
        fails = {_DIM_FIELD.get(d, BindingField.concept if _is_13f(nf) else BindingField.scope)
                 for d in r.failed_dimensions}
        if slot.scope and at is not None and at.scope != slot.scope:
            fails.add(BindingField.scope)        # one member filer's row for a group slot, another officer's figure
        fails = sorted(fails, key=lambda b: b.value)
        gold = Binding(entity=at.entity if at else nf.entity, concept=harness_concept(nf),
                       period=at.period if at else nf.period_end.isoformat(), unit=nf.unit,
                       scale=at.scale if at else None, scope=at.scope if at else None)
        return res.model_copy(update={
            "value": P, "binding": F if fails else P, "support": F, "binding_failures": fails,
            "gold_binding": gold, "value_failure": None,
            "evidence": res.evidence + [f"data-layer store: {r.verdict}, matches {nf.concept} "
                                        f"{nf.period_end} {r.nearest_reading}"]})


def load_anchors(path: str | Path) -> list[Anchor]:
    return [Anchor.model_validate_json(l) for l in Path(path).read_text().splitlines() if l.strip()]
