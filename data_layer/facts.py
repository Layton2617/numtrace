"""The common fact record shared by all three fact stores (statements, 13F, NQDC).

A Fact is one number the standard lets us check a claim against. It carries the value in
a canonical unit (USD, USD/shares, shares) and, where the filing reported it differently,
the original value and unit, so that a claim that copied the raw number can still be
matched and its unit binding judged.
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class Fact(BaseModel):
    entity: str
    concept: str
    value: float
    unit: str
    period_end: date
    period_start: Optional[date] = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    original_value: Optional[float] = None
    original_unit: Optional[str] = None
    form: Optional[str] = None
    accession: Optional[str] = None
    filed: Optional[date] = None
    derived: bool = False
    derivation: Optional[str] = None
    label: Optional[str] = None

    def raw_readings(self) -> list[tuple[float, str]]:
        """(value, unit) pairs under which this fact could be quoted: canonical and original."""
        out = [(self.value, self.unit)]
        if self.original_value is not None and self.original_unit and self.original_unit != self.unit:
            out.append((self.original_value, self.original_unit))
        return out
