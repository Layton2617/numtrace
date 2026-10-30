"""Nonqualified deferred compensation (NQDC) ground truth.

Two populations that plain English conflates are kept apart:
  * NQDCTableRow: the Regulation S-K Item 402(i) proxy table, per named executive officer
    (NEO) and plan (17 CFR 229.402(i)(2) columns (b)-(f)).
  * CompanyNQDCRecord: the company-wide plan liability from the financial-statement note,
    the assets that informally fund it (cash, funds, corporate-owned life insurance, held
    directly or in a rabbi trust), and dated plan-document versions.
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

from .facts import Fact


AMOUNT_FIELDS = ["executive_contributions", "registrant_contributions", "aggregate_earnings",
                 "aggregate_withdrawals", "aggregate_balance"]


class NQDCTableRow(BaseModel):
    """One NEO x plan row of the 402(i) table, in dollars. None = cell blank; 0 = dash."""
    executive: str
    plan: Optional[str] = None
    fiscal_year_end: Optional[date] = None
    executive_contributions: Optional[float] = None
    registrant_contributions: Optional[float] = None
    aggregate_earnings: Optional[float] = None
    aggregate_withdrawals: Optional[float] = None
    aggregate_balance: Optional[float] = None
    source: Optional[str] = None


COLUMN_RULES: list[tuple[str, str]] = [
    (r"executive\s+contributions?|employee\s+contributions?|contributions?\s+by\s+(the\s+)?executive", "executive_contributions"),
    (r"registrant\s+contributions?|company\s+contributions?|employer\s+contributions?|contributions?\s+by\s+(the\s+)?company", "registrant_contributions"),
    (r"earnings|gains?\s*/?\s*\(?loss", "aggregate_earnings"),
    (r"withdrawals?|distributions?", "aggregate_withdrawals"),
    (r"balance", "aggregate_balance"),
    (r"^\s*name\b|named\s+executive|executive\s+officer", "executive"),
    (r"^\s*plan\b|plan\s+name", "plan"),
]


def normalize_column(name: str) -> Optional[str]:
    """Map a proxy-table header ("Aggregate Balance at Last FYE ($)(4)") to a field."""
    text = re.sub(r"\s+", " ", str(name).replace("¦", " ")).lower()
    text = re.sub(r"\(\d+\)|\(\$\)|\$", " ", text)
    for pattern, field in COLUMN_RULES:
        if re.search(pattern, text):
            return field
    return None


DASHES = {"—", "–", "-", "--", "—", "N/A", "n/a"}


def parse_amount(cell) -> Optional[float]:
    """'$1,234' -> 1234; '(1,234)' -> -1234; dash -> 0; blank -> None."""
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return None
    s = str(cell).replace("$", "").replace(" ", " ").strip()
    s = re.sub(r"\(\d\)$", "", s).strip()
    if not s:
        return None
    if s in DASHES:
        return 0.0
    neg = s.startswith("(")
    s = s.strip("()").replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


SEP = " ¦ "


def _mark_breaks(html: str) -> str:
    html = re.sub(r"^\s*<\?xml[^>]*\?>", "", html)
    return re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>", SEP, html)


def _parts(cell) -> list[str]:
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return []
    text = re.sub(r"[\s\u00a0]+", " ", str(cell))
    return [p.strip() for p in text.split("¦") if p.strip() and p.strip() != "$"]


def parse_nqdc_table(html: str, fiscal_year_end: Optional[date] = None,
                     source: Optional[str] = None, table_index: Optional[int] = None
                     ) -> list[NQDCTableRow]:
    """Parse a 402(i) table from proxy HTML with pandas.read_html.

    Handles header rows inside the body, headers repeated across colspans, "$" cells in
    their own columns, and several plans stacked in one row (name cell = executive then
    plan names; amount cells = one value per plan, separated by line breaks)."""
    if table_index is not None:
        tables = [pd.read_html(io.StringIO(_mark_breaks(html)), flavor="lxml")[table_index]]
    else:
        tables = _candidate_tables(html)
    for raw in tables:
        rows = _parse_one(raw, fiscal_year_end, source)
        if rows:
            return rows
    return []


def _candidate_tables(html: str):
    """Tables whose text mentions contributions and a balance, parsed one at a time. On whole
    multi-MB inline-XBRL proxies lxml recovers only part of the document, so the table is cut
    out first."""
    for m in re.finditer(r"(?is)<table\b.*?</table\s*>", html):
        text = re.sub(r"<[^>]+>", " ", m.group(0))
        if re.search(r"(?i)contribution", text) and re.search(r"(?i)balance", text):
            try:
                yield from pd.read_html(io.StringIO(_mark_breaks(m.group(0))), flavor="lxml")
            except ValueError:
                continue


def _header_row(df: pd.DataFrame) -> Optional[int]:
    for i in range(min(len(df), 10)):
        fields = {normalize_column(c) for c in df.iloc[i].tolist() if isinstance(c, str)}
        if {"aggregate_balance", "executive_contributions"} <= fields:
            return i
    return None


def _parse_one(raw: pd.DataFrame, fye, source) -> list[NQDCTableRow]:
    df = raw.copy()
    headers = [" ".join(str(x) for x in c) if isinstance(c, tuple) else str(c) for c in df.columns]
    if not {"aggregate_balance", "executive_contributions"} <= {normalize_column(h) for h in headers}:
        h = _header_row(df)
        if h is None:
            return []
        headers = [str(x) if isinstance(x, str) else "" for x in df.iloc[h].tolist()]
        df = df.iloc[h + 1:]
    fields = [normalize_column(h) for h in headers]
    if "executive" not in fields:
        fields[0] = "executive"

    out = []
    for _, r in df.iterrows():
        cells: dict[str, list[str]] = {}
        for field, cell in zip(fields, r.tolist()):
            if field and field not in cells and _parts(cell):
                cells[field] = _parts(cell)
        name_parts = cells.get("executive", [])
        if not name_parts or not any(f in cells for f in AMOUNT_FIELDS):
            continue
        executive, plans = name_parts[0], name_parts[1:] or cells.get("plan", [None])
        if re.match(r"(?i)total", executive):
            continue
        for k, plan in enumerate(plans):
            amounts = {}
            for f in AMOUNT_FIELDS:
                vals = cells.get(f, [])
                amounts[f] = parse_amount(vals[k]) if k < len(vals) else None
            out.append(NQDCTableRow(executive=executive, plan=plan, fiscal_year_end=fye,
                                    source=source, **amounts))
    return out


def totals_by_executive(rows: list[NQDCTableRow]) -> dict[str, dict[str, float]]:
    """Sum each amount column over an executive's plans (blank cells count as 0)."""
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        t = out.setdefault(r.executive, {f: 0.0 for f in AMOUNT_FIELDS})
        for f in AMOUNT_FIELDS:
            t[f] += getattr(r, f) or 0.0
    return out


class RollForwardCheck(BaseModel):
    executive: str
    plan: Optional[str]
    status: Literal["ok", "mismatch", "not_checkable"]
    expected_balance: Optional[float] = None
    reported_balance: Optional[float] = None
    difference: Optional[float] = None
    note: str = ""


def check_roll_forward(current: NQDCTableRow, prior: Optional[NQDCTableRow],
                       abs_tol: float = 1000.0, rel_tol: float = 0.01) -> RollForwardCheck:
    """balance(t) ~= balance(t-1) + executive + registrant contributions + earnings
    - withdrawals. Differences within tolerance are "ok"; larger ones are flagged, not
    fixed (registrant credits made after FYE, forfeitures, plan transfers and a changed
    NEO/plan population all break the identity legitimately)."""
    base = dict(executive=current.executive, plan=current.plan,
                reported_balance=current.aggregate_balance)
    if prior is None or prior.aggregate_balance is None or current.aggregate_balance is None:
        return RollForwardCheck(status="not_checkable", note="prior-year balance unavailable", **base)
    expected = (prior.aggregate_balance + (current.executive_contributions or 0)
                + (current.registrant_contributions or 0) + (current.aggregate_earnings or 0)
                - abs(current.aggregate_withdrawals or 0))
    diff = current.aggregate_balance - expected
    ok = abs(diff) <= max(abs_tol, rel_tol * abs(current.aggregate_balance))
    return RollForwardCheck(status="ok" if ok else "mismatch", expected_balance=expected,
                            difference=diff, **base)


def check_table_roll_forward(current: list[NQDCTableRow], prior: list[NQDCTableRow],
                             **tol) -> list[RollForwardCheck]:
    """Match rows by (executive, plan) across two proxy years and check each."""
    prior_by = {(p.executive.lower(), (p.plan or "").lower()): p for p in prior}
    return [check_roll_forward(c, prior_by.get((c.executive.lower(), (c.plan or "").lower())), **tol)
            for c in current]



FundingVehicle = Literal["unfunded", "cash", "mutual_funds", "coli_cash_surrender_value",
                         "company_stock", "other"]


class FundingAsset(BaseModel):
    vehicle: FundingVehicle
    value: Optional[float] = None
    concept: Optional[str] = None
    in_rabbi_trust: bool = False
    source: Optional[str] = None


class PlanDocumentVersion(BaseModel):
    title: str
    effective_date: date
    exhibit: Optional[str] = None
    accession: Optional[str] = None
    filed: Optional[date] = None
    summary_of_terms: str = ""


class CompanyNQDCRecord(BaseModel):
    entity: str
    fiscal_year_end: date
    plan_name: str
    plan_type: Optional[Literal["elective_deferral", "supplemental_401k", "excess_benefit",
                                "SERP_account", "other"]] = None
    plan_liability: Optional[float] = None
    plan_liability_concept: str = "us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent"
    plan_liability_source: Optional[str] = None
    funding_assets: list[FundingAsset] = Field(default_factory=list)
    plan_documents: list[PlanDocumentVersion] = Field(default_factory=list)

    def document_in_force(self, as_of: date) -> Optional[PlanDocumentVersion]:
        """Latest plan-document version effective on or before as_of."""
        docs = [d for d in self.plan_documents if d.effective_date <= as_of]
        return max(docs, key=lambda d: d.effective_date) if docs else None

    def to_facts(self) -> list[Fact]:
        """Liability and funding assets as facts for the binding check."""
        out = []
        if self.plan_liability is not None:
            out.append(Fact(entity=self.entity, concept=self.plan_liability_concept,
                            value=self.plan_liability, unit="USD", period_end=self.fiscal_year_end,
                            label=f"{self.plan_name} liability", accession=self.plan_liability_source))
        for a in self.funding_assets:
            if a.value is not None:
                out.append(Fact(entity=self.entity, concept=a.concept or f"nqdc:FundingAsset:{a.vehicle}",
                                value=a.value, unit="USD", period_end=self.fiscal_year_end,
                                label=f"funding asset: {a.vehicle}" + (" (rabbi trust)" if a.in_rabbi_trust else ""),
                                accession=a.source))
        return out


def table_rows_to_facts(rows: list[NQDCTableRow], entity: str) -> list[Fact]:
    """402(i) cells as facts (concept nqdc:<field>, dimensions executive/plan)."""
    out = []
    for r in rows:
        if r.fiscal_year_end is None:
            continue
        for f in AMOUNT_FIELDS:
            v = getattr(r, f)
            if v is not None:
                out.append(Fact(entity=entity, concept=f"nqdc:{f}", value=v, unit="USD",
                                period_end=r.fiscal_year_end,
                                dimensions={"executive": r.executive, "plan": r.plan or ""},
                                accession=r.source))
    return out
