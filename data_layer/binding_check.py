"""Concept check against the fact store (tier 2 monitor).

Given a claim {entity, concept_as_stated, period, unit, value}, find every fact the
claimed number is a correct copy (or rounding) of, and judge:
  value_ok    the number equals some fact in the store under some reading
              (canonical unit, original unit, or a x1,000 / x1,000,000 scale slip);
  binding_ok  that fact is bound to the question's target concept, period, unit, entity
              and dimensions; None when no fact matches (binding not assessable);
  verdict     "correct" | "right_number_wrong_account" | "wrong_value" | "no_ground_truth".
"Right number, wrong account" = value_ok and not binding_ok.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Literal, Optional

from pydantic import BaseModel, Field

from .facts import Fact
from .statements import CONCEPT_BINDINGS, binding_for_entity
from .thirteen_f import POSITION_VALUE

PHRASES: dict[str, str] = {
    "total long-term debt": "total_long_term_debt",
    "total long-term debt including current portion": "total_long_term_debt",
    "long-term debt": "total_long_term_debt",
    "long-term debt, net of current portion": "long_term_debt_noncurrent",
    "long-term debt, noncurrent": "long_term_debt_noncurrent",
    "current portion of long-term debt": "long_term_debt_current",
    "cash and cash equivalents": "cash_and_cash_equivalents",
    "diluted eps": "eps_diluted",
    "diluted earnings per share": "eps_diluted",
    "net income": "net_income",
    "deferred compensation obligation": "deferred_comp_liability_total",
    "deferred compensation liability": "deferred_comp_liability_total",
    "nqdc plan liability": "deferred_comp_liability_total",
    "plan liability": "deferred_comp_liability_total",
    "cash surrender value of life insurance": "coli_cash_surrender_value",
    "13f position value": POSITION_VALUE,
    "position value": POSITION_VALUE,
    "market value of position": POSITION_VALUE,
}


def resolve_concepts(stated: str, entity: Optional[str] = None) -> list[str]:
    """Metric key, concept name ("us-gaap:X", "13f:Y") or known phrase -> concept list, using the
    entity's own binding where one is defined."""
    key = PHRASES.get(stated.strip().lower(), stated.strip())
    if key in CONCEPT_BINDINGS:
        b = binding_for_entity(entity, key)
        return list(b.concepts) + ([b.derived_concept] if b.derived_concept else [])
    if ":" in key:
        return [key]
    raise KeyError(f"unknown concept or phrase: {stated!r}")


class Claim(BaseModel):
    entity: str
    concept_as_stated: str
    period_end: date
    period_start: Optional[date] = None
    unit: str = "USD"
    value: float
    dimensions: dict[str, str] = Field(default_factory=dict)
    target: Optional[str] = None


def half_unit_of_last_digit(x: float, min_sig: int = 2) -> float:
    """Rounding tolerance implied by how the claim is written: 4.12e9 -> 5e6, 6.13 -> 0.005.
    Claims with one significant digit are treated as two (1e9 -> +-5e7)."""
    if x == 0:
        return 0.0
    d = Decimal(repr(float(x))).normalize()
    t = d.as_tuple()
    exp = t.exponent - max(0, min_sig - len(t.digits))
    return float(Decimal(5) * Decimal(10) ** (exp - 1))


def values_match(claimed: float, actual: float, rel_tol: float = 0.0) -> bool:
    """True when `actual`, rounded to the claim's precision, equals the claim (or the
    difference is within rel_tol of the actual value)."""
    if actual == 0 or claimed == 0:
        return actual == claimed
    tol = max(half_unit_of_last_digit(claimed), rel_tol * abs(actual))
    return abs(claimed - actual) <= tol * (1 + 1e-9)


SCALES = [(1e3, "x1,000 scale"), (1e6, "x1,000,000 scale")]


class Match(BaseModel):
    fact: Fact
    reading_value: float
    reading_unit: str
    reading_rank: int
    failed: list[str]


class BindingResult(BaseModel):
    value_ok: bool
    binding_ok: Optional[bool]
    verdict: Literal["correct", "right_number_wrong_account", "wrong_value", "no_ground_truth"]
    failed_dimensions: list[str] = Field(default_factory=list)
    target_concepts: list[str]
    expected_fact: Optional[Fact] = None
    nearest_fact: Optional[Fact] = None
    nearest_reading: Optional[tuple[float, str]] = None
    matches: list[Match] = Field(default_factory=list)
    note: str = ""


def _period_ok(f: Fact, c: Claim) -> bool:
    return f.period_end == c.period_end and (c.period_start is None or f.period_start == c.period_start)


def _dims_ok(f: Fact, c: Claim) -> bool:
    return all(f.dimensions.get(k) == v for k, v in c.dimensions.items())


def _readings(f: Fact, claim_unit: str) -> list[tuple[float, str, bool, int]]:
    """(value, unit-as-read, unit_ok, rank) for each way a fact could be quoted."""
    out = []
    for rank, (v, u) in enumerate(f.raw_readings()):
        out.append((v, u, u == claim_unit, rank))
    if f.unit == claim_unit == "USD":
        out += [(f.value / s, label, False, 2) for s, label in SCALES]
    return out


def check_claim(claim: Claim, facts: Iterable[Fact], rel_tol: float = 0.0) -> BindingResult:
    facts = list(facts)
    targets = resolve_concepts(claim.target or claim.concept_as_stated, claim.entity)

    def is_expected(f: Fact) -> bool:
        return (f.entity == claim.entity and f.concept in targets
                and _period_ok(f, claim) and _dims_ok(f, claim))

    expected = [f for f in facts if is_expected(f)]
    expected_fact = sorted(expected, key=lambda f: (f.derived, f.filed or date.min))[0] if expected else None

    matches: list[Match] = []
    for f in facts:
        if claim.dimensions and not _dims_ok(f, claim) and f.entity != claim.entity:
            continue
        for v, u, unit_ok, rank in _readings(f, claim.unit):
            if values_match(claim.value, v, rel_tol):
                failed = []
                if f.entity != claim.entity:
                    failed.append("entity")
                if f.concept not in targets:
                    failed.append("concept")
                if not _period_ok(f, claim):
                    failed.append("period")
                if not unit_ok:
                    failed.append("unit")
                if not _dims_ok(f, claim):
                    failed.append("dimensions")
                matches.append(Match(fact=f, reading_value=v, reading_unit=u,
                                     reading_rank=rank, failed=failed))
                break
    matches.sort(key=lambda m: (len(m.failed), m.reading_rank, m.fact.derived,
                                m.fact.filed or date.min))

    if matches:
        best = matches[0]
        ok = not best.failed
        return BindingResult(
            value_ok=True, binding_ok=ok,
            verdict="correct" if ok else "right_number_wrong_account",
            failed_dimensions=best.failed, target_concepts=targets,
            expected_fact=expected_fact, nearest_fact=best.fact,
            nearest_reading=(best.reading_value, best.reading_unit), matches=matches,
            note="" if ok else f"value appears in the filing under a different {', '.join(best.failed)}",
        )
    if expected_fact is None:
        return BindingResult(value_ok=False, binding_ok=None, verdict="no_ground_truth",
                             target_concepts=targets, note="no fact for the target concept/period")
    return BindingResult(value_ok=False, binding_ok=None, verdict="wrong_value",
                         target_concepts=targets, expected_fact=expected_fact,
                         nearest_fact=expected_fact,
                         nearest_reading=(expected_fact.value, expected_fact.unit),
                         note=f"claimed {claim.value:,.4g} vs expected {expected_fact.value:,.4g}")
