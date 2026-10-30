"""Form 13F ground truth: as-filed holdings with the unit rule, amendment merge,
co-filing groups and position helpers.

Rules implemented here (sources in data_layer/README.md):
  * UNIT RULE (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03): the information-table <value> is in THOUSANDS of
    dollars for filings made before 3 January 2023 and in whole DOLLARS for filings made
    on or after 3 January 2023. The unit follows the FILING DATE, not the report period.
  * AMENDMENTS (Form 13F Special Instruction 3, FAQ 58b/58c): a RESTATEMENT amendment replaces the
    holdings of the period; a NEW HOLDINGS amendment adds entries to them.
  * OTHER INCLUDED MANAGERS (Form 13F Special Instructions 7 and 11.b.vii, FAQ 37/48): the summary page lists the other
    managers on whose behalf the report is filed (sequence numbers used in the
    information-table column "Other Manager"); a 13F Notice / Combination Report lists on
    its cover page the other managers reporting for this manager.
  * CO-FILING GROUPS and DE-DUPLICATION: defined by us (not by the SEC) through an
    explicit input table, see CoFilingGroup and group_rows().
"""
from __future__ import annotations

import base64
import re
import statistics
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Literal, Optional, Union

from lxml import etree
from pydantic import BaseModel, Field

from .facts import Fact


UNIT_CHANGE_DATE = date(2023, 1, 3)
USD = "USD"
USD_THOUSANDS = "USD_thousands"
UNIT_BASIS_RULE = "Form 13F FAQ 62: unit follows filing date"

Unit = Literal["USD", "USD_thousands"]


def _to_date(d: Union[date, datetime, str]) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = d.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {d!r}")


def value_unit_for_filing_date(filing_date: Union[date, str]) -> Unit:
    """Unit of the information-table <value> column (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03)."""
    return USD if _to_date(filing_date) >= UNIT_CHANGE_DATE else USD_THOUSANDS


def to_dollars(value: float, unit: Unit) -> float:
    return value * 1000 if unit == USD_THOUSANDS else value


def normalize_value(value: float, filing_date: Union[date, str]) -> float:
    """Information-table value -> whole dollars, using the FILING date (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03)."""
    return to_dollars(value, value_unit_for_filing_date(filing_date))


class UnitOverride(BaseModel):
    """A caller-verified exception to the unit rule for one filing (e.g. a filer who
    reported dollars before 2023). Recorded, never inferred silently."""
    accession: str
    unit: Unit
    reason: str



class OtherManager(BaseModel):
    name: str
    cik: Optional[str] = None
    form13f_file_number: Optional[str] = None
    sequence_number: Optional[int] = None


class FilingMeta(BaseModel):
    accession: str
    form_type: str
    filing_date: date
    period_of_report: date
    filer_cik: str
    filer_name: str
    report_type: str
    is_amendment: bool = False
    amendment_no: Optional[int] = None
    amendment_type: Optional[Literal["RESTATEMENT", "NEW HOLDINGS"]] = None
    schema_version: Optional[str] = None
    other_included_managers: list[OtherManager] = Field(default_factory=list)
    other_managers_reporting: list[OtherManager] = Field(default_factory=list)
    table_entry_total: Optional[int] = None
    table_value_total: Optional[int] = None
    value_unit: Unit = USD
    unit_basis: str = UNIT_BASIS_RULE


class HoldingRow(BaseModel):
    name_of_issuer: str
    title_of_class: str
    cusip: str
    figi: Optional[str] = None
    original_value: int
    original_unit: Unit
    value_usd: float
    ssh_prnamt: int
    ssh_prnamt_type: Literal["SH", "PRN"]
    put_call: Optional[Literal["Put", "Call"]] = None
    investment_discretion: Literal["SOLE", "DFND", "OTR"]
    other_managers: list[int] = Field(default_factory=list)
    voting_sole: int = 0
    voting_shared: int = 0
    voting_none: int = 0
    accession: str
    filer_cik: str
    filing_date: date
    row_index: int


class Filing13F(BaseModel):
    meta: FilingMeta
    rows: list[HoldingRow] = Field(default_factory=list)



def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, name):
    if el is None:
        return None
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _path(el, *names):
    for n in names:
        el = _child(el, n)
    return el


def _children(el) -> list:
    return list(el) if el is not None else []


def _text(el, *names) -> Optional[str]:
    el = _path(el, *names) if names else el
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _xml_root(xml: Union[str, bytes]):
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    return etree.fromstring(xml.strip(), parser=etree.XMLParser(remove_blank_text=True))


def _parse_other_manager(el, seq=None) -> OtherManager:
    return OtherManager(name=_text(el, "name") or "", cik=_text(el, "cik"),
                        form13f_file_number=_text(el, "form13FFileNumber"), sequence_number=seq)


def parse_primary_doc(xml: Union[str, bytes], accession: str, filing_date: Union[date, str],
                      unit_override: Optional[UnitOverride] = None) -> FilingMeta:
    """Parse the 13F primary document (cover page, summary page)."""
    root = _xml_root(xml)
    header, form = _child(root, "headerData"), _child(root, "formData")
    cover, summary = _child(form, "coverPage"), _child(form, "summaryPage")
    filing_date = _to_date(filing_date)

    included = []
    for om2 in _children(_child(summary, "otherManagers2Info")):
        seq = _text(om2, "sequenceNumber")
        included.append(_parse_other_manager(_child(om2, "otherManager"), int(seq) if seq else None))
    reporting = [_parse_other_manager(om) for om in _children(_child(cover, "otherManagersInfo"))]

    amend_no = _text(cover, "amendmentNo")
    unit = value_unit_for_filing_date(filing_date)
    basis = UNIT_BASIS_RULE
    if unit_override is not None:
        unit, basis = unit_override.unit, f"override: {unit_override.reason}"
    entry_total, value_total = _text(summary, "tableEntryTotal"), _text(summary, "tableValueTotal")
    return FilingMeta(
        accession=accession,
        form_type=_text(header, "submissionType"),
        filing_date=filing_date,
        period_of_report=_to_date(_text(cover, "reportCalendarOrQuarter")
                                  or _text(header, "filerInfo", "periodOfReport")),
        filer_cik=_text(header, "filerInfo", "filer", "credentials", "cik"),
        filer_name=_text(cover, "filingManager", "name") or "",
        report_type=_text(cover, "reportType"),
        is_amendment=(_text(cover, "isAmendment") or "false").lower() in ("true", "y"),
        amendment_no=int(amend_no) if amend_no else None,
        amendment_type=_text(cover, "amendmentInfo", "amendmentType"),
        schema_version=_text(root, "schemaVersion"),
        other_included_managers=included,
        other_managers_reporting=reporting,
        table_entry_total=int(entry_total) if entry_total else None,
        table_value_total=int(value_total) if value_total else None,
        value_unit=unit,
        unit_basis=basis,
    )


def _embedded_infotable(primary_xml: Union[str, bytes]) -> Optional[bytes]:
    """SEC sample files carry the information table base64-encoded under <documents>."""
    root = _xml_root(primary_xml)
    for doc in _children(_child(root, "documents")):
        if (_text(doc, "conformedDocumentType") or "").upper() == "INFORMATION TABLE":
            return base64.b64decode(_text(doc, "contents"))
    return None


def parse_information_table(xml: Union[str, bytes], meta: FilingMeta) -> list[HoldingRow]:
    """Parse the information table; values are normalised with the filing's unit."""
    rows = []
    for i, it in enumerate(e for e in _xml_root(xml) if _local(e.tag) == "infoTable"):
        raw = int(_text(it, "value"))
        others = _text(it, "otherManager")
        rows.append(HoldingRow(
            name_of_issuer=_text(it, "nameOfIssuer"),
            title_of_class=_text(it, "titleOfClass"),
            cusip=_text(it, "cusip").upper(),
            figi=_text(it, "figi"),
            original_value=raw,
            original_unit=meta.value_unit,
            value_usd=to_dollars(raw, meta.value_unit),
            ssh_prnamt=int(_text(it, "shrsOrPrnAmt", "sshPrnamt")),
            ssh_prnamt_type=_text(it, "shrsOrPrnAmt", "sshPrnamtType"),
            put_call=_text(it, "putCall"),
            investment_discretion=_text(it, "investmentDiscretion"),
            other_managers=[int(x) for x in re.findall(r"\d+", others)] if others else [],
            voting_sole=int(_text(it, "votingAuthority", "Sole") or 0),
            voting_shared=int(_text(it, "votingAuthority", "Shared") or 0),
            voting_none=int(_text(it, "votingAuthority", "None") or 0),
            accession=meta.accession, filer_cik=meta.filer_cik,
            filing_date=meta.filing_date, row_index=i,
        ))
    return rows


def parse_filing(primary_xml, infotable_xml=None, *, accession: str,
                 filing_date: Union[date, str],
                 unit_overrides: Optional[dict[str, UnitOverride]] = None) -> Filing13F:
    """Parse one 13F filing from its primary document and (optional) information table.

    Accession number and filing date are not in the XML; they come from the EDGAR index
    or the SGML header (see parse_full_submission)."""
    override = (unit_overrides or {}).get(accession)
    meta = parse_primary_doc(primary_xml, accession, filing_date, override)
    if infotable_xml is None:
        infotable_xml = _embedded_infotable(primary_xml)
    rows = parse_information_table(infotable_xml, meta) if infotable_xml is not None else []
    return Filing13F(meta=meta, rows=rows)


def parse_sgml_header(text: str) -> dict:
    """Header fields of an EDGAR full-submission .txt file."""
    fields = {}
    for key in ("ACCESSION NUMBER", "CONFORMED SUBMISSION TYPE", "CONFORMED PERIOD OF REPORT",
                "FILED AS OF DATE"):
        m = re.search(rf"^{key}:\s*(\S+)", text, re.M)
        fields[key] = m.group(1) if m else None
    return fields


def parse_full_submission(text: str,
                          unit_overrides: Optional[dict[str, UnitOverride]] = None) -> Filing13F:
    """Parse an EDGAR full-submission .txt (SGML header + XML documents).

    The filing date is the header's FILED AS OF DATE (the official filing date)."""
    h = parse_sgml_header(text)
    primary = infotable = None
    for block in re.findall(r"<XML>\s*(.*?)</XML>", text, re.S):
        tag = _local(_xml_root(block).tag)
        if tag == "edgarSubmission":
            primary = block
        elif tag == "informationTable":
            infotable = block
    if primary is None:
        raise ValueError("no 13F primary document found")
    return parse_filing(primary, infotable, accession=h["ACCESSION NUMBER"],
                        filing_date=h["FILED AS OF DATE"], unit_overrides=unit_overrides)


def load_full_submission(path: Union[str, Path], **kw) -> Filing13F:
    return parse_full_submission(Path(path).read_text(encoding="utf-8", errors="replace"), **kw)


def validate_xml(xml: Union[str, bytes], xsd_dir: Union[str, Path]) -> list[str]:
    """Validate a primary document or information table against the official EDGAR 13F
    XSDs in xsd_dir. Returns a list of error messages (empty = valid)."""
    root = _xml_root(xml)
    xsd = "eis_13F_Filer.xsd" if _local(root.tag) == "edgarSubmission" else "eis_13FDocument.xsd"
    schema = etree.XMLSchema(etree.parse(str(Path(xsd_dir) / xsd)))
    return [] if schema.validate(root) else [str(e) for e in schema.error_log]



class UnitDiagnostics(BaseModel):
    rule_unit: Unit
    priceable_rows: int
    median_implied_price: Optional[float]
    share_sub_dollar: Optional[float]
    suspect: bool
    reason: str


def unit_diagnostics(filing: Filing13F, low_share: float = 0.5,
                     high_median: float = 10_000.0) -> UnitDiagnostics:
    """Flag (never fix) filings whose normalised values imply implausible share prices.

    Implied price = value_usd / shares over SH rows without put/call. If most rows price
    below $1, the filer probably reported thousands in a dollars-era filing; if the median
    is above `high_median`, it probably reported dollars in a thousands-era filing. The
    ground truth keeps the rule (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03); a verified exception goes into a UnitOverride."""
    prices = [r.value_usd / r.ssh_prnamt for r in filing.rows
              if r.ssh_prnamt_type == "SH" and r.put_call is None and r.ssh_prnamt > 0 and r.value_usd > 0]
    if not prices:
        return UnitDiagnostics(rule_unit=filing.meta.value_unit, priceable_rows=0,
                               median_implied_price=None, share_sub_dollar=None,
                               suspect=False, reason="no priceable rows")
    med = statistics.median(prices)
    sub = sum(p < 1 for p in prices) / len(prices)
    if sub >= low_share:
        reason, suspect = f"{sub:.0%} of rows imply a price below $1: values look like thousands", True
    elif med > high_median:
        reason, suspect = f"median implied price ${med:,.0f}: values look like whole dollars", True
    else:
        reason, suspect = "implied prices plausible", False
    return UnitDiagnostics(rule_unit=filing.meta.value_unit, priceable_rows=len(prices),
                           median_implied_price=med, share_sub_dollar=sub,
                           suspect=suspect, reason=reason)


class RowUnitEvidence(BaseModel):
    rule_unit: Unit
    evidence_unit: Literal["USD", "USD_thousands", "ambiguous", "none", "n/a"]
    price_if_dollars: Optional[float] = None
    price_if_thousands: Optional[float] = None
    conflict: bool = False


def row_unit_evidence(row: HoldingRow, low: float = 1.0, high: float = 10_000.0) -> RowUnitEvidence:
    """Per-row unit evidence from the implied price per share. A reading is plausible when its price
    lies in [low, high]; exactly one plausible reading names the evidence unit. Options, PRN rows and
    zero-share rows carry no price evidence ("n/a")."""
    rule = value_unit_for_filing_date(row.filing_date)
    if row.ssh_prnamt_type != "SH" or row.put_call is not None or row.ssh_prnamt <= 0:
        return RowUnitEvidence(rule_unit=rule, evidence_unit="n/a")
    p_d = row.original_value / row.ssh_prnamt
    p_k = p_d * 1000
    ok_d, ok_k = low <= p_d <= high, low <= p_k <= high
    ev = "ambiguous" if ok_d and ok_k else USD if ok_d else USD_THOUSANDS if ok_k else "none"
    return RowUnitEvidence(rule_unit=rule, evidence_unit=ev, price_if_dollars=p_d, price_if_thousands=p_k,
                           conflict=ev in (USD, USD_THOUSANDS) and ev != rule)


def summary_total_difference(filing: Filing13F) -> Optional[int]:
    """Sum of row values minus the summary page's tableValueTotal (original units).
    Non-zero differences are reported, not reconciled."""
    if filing.meta.table_value_total is None:
        return None
    return sum(r.original_value for r in filing.rows) - filing.meta.table_value_total



class MergeStep(BaseModel):
    accession: str
    filing_date: date
    action: Literal["original", "restatement_replaced", "new_holdings_added"]
    rows_after: int


class AsFiledHoldings(BaseModel):
    filer_cik: str
    filer_name: str
    period: date
    rows: list[HoldingRow]
    history: list[MergeStep]
    filings: list[FilingMeta]


def merge_filings(original: Filing13F, amendments: Iterable[Filing13F] = ()) -> AsFiledHoldings:
    """As-filed holdings of one manager for one period.

    Amendments are applied in filing order: a RESTATEMENT replaces all rows so far, a
    NEW HOLDINGS amendment appends its rows. Each row keeps its own unit, which follows
    its own filing date, so a 2023 amendment to a 2022 quarter is read in dollars."""
    amendments = sorted(amendments, key=lambda f: (f.meta.filing_date, f.meta.amendment_no or 0,
                                                   f.meta.accession))
    if original.meta.is_amendment:
        raise ValueError(f"{original.meta.accession} is an amendment, not an original")
    for a in amendments:
        if (a.meta.filer_cik, a.meta.period_of_report) != (original.meta.filer_cik,
                                                           original.meta.period_of_report):
            raise ValueError(f"{a.meta.accession} is for a different filer or period")
        if not a.meta.is_amendment or a.meta.amendment_type not in ("RESTATEMENT", "NEW HOLDINGS"):
            raise ValueError(f"{a.meta.accession} has no amendment type")

    rows = list(original.rows)
    history = [MergeStep(accession=original.meta.accession, filing_date=original.meta.filing_date,
                         action="original", rows_after=len(rows))]
    for a in amendments:
        if a.meta.amendment_type == "RESTATEMENT":
            rows, action = list(a.rows), "restatement_replaced"
        else:
            rows, action = rows + list(a.rows), "new_holdings_added"
        history.append(MergeStep(accession=a.meta.accession, filing_date=a.meta.filing_date,
                                 action=action, rows_after=len(rows)))
    return AsFiledHoldings(filer_cik=original.meta.filer_cik, filer_name=original.meta.filer_name,
                           period=original.meta.period_of_report, rows=rows, history=history,
                           filings=[original.meta] + [a.meta for a in amendments])


def merge_all(filings: Iterable[Filing13F]) -> list[AsFiledHoldings]:
    """Group filings by (filer, period) and merge each group. Notices (13F-NT) carry no
    holdings and are skipped."""
    groups: dict[tuple, list[Filing13F]] = {}
    for f in filings:
        if f.meta.report_type == "13F NOTICE":
            continue
        groups.setdefault((f.meta.filer_cik, f.meta.period_of_report), []).append(f)
    out = []
    for key, fs in groups.items():
        originals = [f for f in fs if not f.meta.is_amendment]
        if len(originals) != 1:
            raise ValueError(f"{key}: expected one original filing, found {len(originals)}")
        out.append(merge_filings(originals[0], [f for f in fs if f.meta.is_amendment]))
    return out



class CoFilingGroup(BaseModel):
    """One row of the explicit co-filing group table. `members` are CIKs in priority
    order: when the same shared position is reported by several members, the row of the
    earliest member is kept."""
    group_id: str
    name: str
    members: list[str]
    note: str = ""


def suggest_groups(filings: Iterable[Filing13F]) -> list[set[str]]:
    """Candidate groups (connected components) from cover/summary-page manager lists.
    A suggestion only: the curated CoFilingGroup table is the input to aggregation."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for f in filings:
        find(f.meta.filer_cik)
        for om in f.meta.other_included_managers + f.meta.other_managers_reporting:
            if om.cik:
                parent[find(om.cik)] = find(f.meta.filer_cik)
    comps: dict[str, set[str]] = {}
    for x in list(parent):
        comps.setdefault(find(x), set()).add(x)
    return [c for c in comps.values() if len(c) > 1]


class DroppedRow(BaseModel):
    row: HoldingRow
    duplicate_of: str
    reason: str


class GroupHoldings(BaseModel):
    group_id: str
    period: date
    rows: list[HoldingRow]
    dropped: list[DroppedRow]


def _dup_key(r: HoldingRow):
    return (r.cusip, r.title_of_class, r.put_call, r.ssh_prnamt_type, r.ssh_prnamt, r.value_usd)


def _is_shared(r: HoldingRow) -> bool:
    return r.investment_discretion == "DFND" or bool(r.other_managers)


def group_rows(group: CoFilingGroup, holdings: Iterable[AsFiledHoldings]) -> GroupHoldings:
    """Holdings of a co-filing group for one period, shared filings de-duplicated.

    De-duplication rule (ours): a row of a later member is dropped when an earlier member
    already reported a row with the same CUSIP, class, put/call, share type, amount and
    dollar value, and either row is under shared discretion (DFND) or names other
    managers. Rows within one filing are never dropped: several rows for one CUSIP there
    are distinct sub-positions and are summed."""
    by_cik = {h.filer_cik: h for h in holdings if h.filer_cik in group.members}
    periods = {h.period for h in by_cik.values()}
    if len(periods) > 1:
        raise ValueError(f"members of {group.group_id} have different periods: {periods}")
    kept: list[HoldingRow] = []
    dropped: list[DroppedRow] = []
    seen: dict[tuple, HoldingRow] = {}
    for cik in group.members:
        if cik not in by_cik:
            continue
        new_seen = {}
        for r in by_cik[cik].rows:
            prev = seen.get(_dup_key(r))
            if prev is not None and (_is_shared(r) or _is_shared(prev)):
                dropped.append(DroppedRow(row=r, duplicate_of=prev.accession,
                                          reason="same shared position reported by an earlier member"))
                continue
            kept.append(r)
            new_seen.setdefault(_dup_key(r), r)
        for k, v in new_seen.items():
            seen.setdefault(k, v)
    return GroupHoldings(group_id=group.group_id, period=periods.pop() if periods else None,
                         rows=kept, dropped=dropped)



class HoldingsStore:
    """As-filed holdings by manager and period, plus the explicit group table."""

    def __init__(self, holdings: Iterable[AsFiledHoldings], groups: Iterable[CoFilingGroup] = ()):
        self.holdings = {(h.filer_cik, h.period): h for h in holdings}
        self.groups = {g.group_id: g for g in groups}

    @classmethod
    def from_filings(cls, filings: Iterable[Filing13F], groups: Iterable[CoFilingGroup] = ()):
        return cls(merge_all(filings), groups)

    def rows(self, entity: str, period: Union[date, str]) -> list[HoldingRow]:
        period = _to_date(period)
        if entity in self.groups:
            members = [h for (cik, p), h in self.holdings.items()
                       if p == period and cik in self.groups[entity].members]
            return group_rows(self.groups[entity], members).rows
        h = self.holdings.get((entity, period))
        if h is None:
            raise KeyError(f"no holdings for {entity} at {period}")
        return h.rows


class Position(BaseModel):
    entity: str
    cusip: str
    period: date
    value_usd: float
    shares: int
    principal: int
    original_values: list[tuple[int, str]]
    accessions: list[str]


def position(store: HoldingsStore, entity: str, cusip: str, period: Union[date, str],
             include_options: bool = False) -> Position:
    """Position of a manager (CIK) or co-filing group (group_id) in one CUSIP, in dollars.
    Put/call rows are excluded unless include_options (their value is the underlying's)."""
    rows = [r for r in store.rows(entity, period) if r.cusip == cusip.upper()
            and (include_options or r.put_call is None)]
    return Position(
        entity=entity, cusip=cusip.upper(), period=_to_date(period),
        value_usd=sum(r.value_usd for r in rows),
        shares=sum(r.ssh_prnamt for r in rows if r.ssh_prnamt_type == "SH"),
        principal=sum(r.ssh_prnamt for r in rows if r.ssh_prnamt_type == "PRN"),
        original_values=[(r.original_value, r.original_unit) for r in rows],
        accessions=sorted({r.accession for r in rows}),
    )


class PositionChange(BaseModel):
    start: Position
    end: Position
    change_usd: float
    change_pct: Optional[float]
    change_shares: int


def qoq_change(store: HoldingsStore, entity: str, cusip: str, period_from, period_to,
               include_options: bool = False) -> PositionChange:
    """Quarter-over-quarter change of a position, always in normalised dollars."""
    a = position(store, entity, cusip, period_from, include_options)
    b = position(store, entity, cusip, period_to, include_options)
    return PositionChange(start=a, end=b, change_usd=b.value_usd - a.value_usd,
                          change_pct=(b.value_usd / a.value_usd - 1) if a.value_usd else None,
                          change_shares=b.shares - a.shares)


POSITION_VALUE = "13f:PositionValue"
POSITION_SHARES = "13f:PositionShares"


def position_facts(store: HoldingsStore, entity: str, period: Union[date, str]) -> list[Fact]:
    """Facts (one value + one share count per CUSIP) for the binding check.

    original_value/original_unit are kept when every row of the position has the same
    unit, so a claim that copied the raw number can be matched and its unit judged."""
    out = []
    for cusip in sorted({r.cusip for r in store.rows(entity, period)}):
        p = position(store, entity, cusip, period)
        if not p.original_values:
            continue
        units = {u for _, u in p.original_values}
        orig_val = sum(v for v, _ in p.original_values) if len(units) == 1 else None
        common = dict(entity=entity, period_end=p.period, dimensions={"cusip": cusip},
                      accession=",".join(p.accessions), form="13F-HR")
        out.append(Fact(concept=POSITION_VALUE, value=p.value_usd, unit=USD,
                        original_value=orig_val, original_unit=units.pop() if len(units) == 1 else None,
                        **common))
        out.append(Fact(concept=POSITION_SHARES, value=p.shares, unit="shares", **common))
    return out
