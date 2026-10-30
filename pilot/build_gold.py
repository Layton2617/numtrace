"""Build the pilot gold fact sheet from the cached EDGAR files (data/raw/), using data_layer.

    python -m pilot.build_gold            # writes pilot/gold/, prints conflicts and mismatches

Outputs
  gold/pilot_gold.jsonl       one line per expected slot of the 16 questions
  gold/13f_rows.csv           every row of the 9 pilot 13F filings: original value, rule unit, evidence unit, conflict
  gold/nqdc_roll_forward.csv  402(i) balance roll-forward, FY2025 vs FY2024 proxy, per NEO

Text numbers that are not in structured data (Target Note 24 liability, Note 17 lines) are entered here with
the document they come from; each was read from the cached 10-K (see pilot/entities.md, EDGAR verification log).
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from data_layer.edgar_fetch import archive_url, cache_path, companyfacts, full_submission
from data_layer.nqdc import check_table_roll_forward, parse_nqdc_table
from data_layer.statements import get_metric, load_companyfacts, select
from data_layer.thirteen_f import (HoldingsStore, parse_full_submission, position, qoq_change,
                                   row_unit_evidence)

OUT = Path(__file__).parent / "gold"
TGT, CMCSA, PSCM, BRK = 27419, 1166691, 1336528, 1067983
M = 1e6

F13 = {
    "PSCM-2022Q3": (PSCM, "0001172661-22-002568"), "PSCM-2022Q4": (PSCM, "0001172661-23-000673"),
    "BRK-2022Q3": (BRK, "0000950123-22-012275"), "BRK-2022Q4": (BRK, "0000950123-23-002585"),
    "BRK-2023Q3": (BRK, "0000950123-23-010898"), "BRK-2023Q3-RS": (BRK, "0000950123-23-011029"),
    "BRK-2023Q3-NH": (BRK, "0000950123-24-005653"), "BRK-2023Q4": (BRK, "0000950123-24-002518"),
    "BRK-2023Q4-NH": (BRK, "0000950123-24-005664"),
}
PROXIES = {
    TGT: [("0001628280-26-027508", "tgt-20260426.htm", date(2026, 1, 31)),
          ("0001628280-25-020275", "tgt-20250428.htm", date(2025, 2, 1))],
    CMCSA: [("0001193125-26-177138", "cmcsa-20260424.htm", date(2025, 12, 31)),
            ("0001308179-25-000504", "cmcsa012438-def14a.htm", date(2024, 12, 31))],
}
DOCS = {
    "TGT-10K": (TGT, "0000027419-26-000016", "tgt-20260131.htm"),
    "TGT-10Q3": (TGT, "0000027419-25-000126", "tgt-20251101.htm"),
    "TGT-EDCP2025": (TGT, "0000027419-25-000018", "tgt-20250201xexhibit109.htm"),
    "CMCSA-10K": (CMCSA, "0001628280-26-004994", "cmcsa-20251231.htm"),
    "CMCSA-10Q3": (CMCSA, "0001166691-25-000054", "cmcsa-20250930.htm"),
    "CMCSA-DCP2005": (CMCSA, "0001628280-26-004994", "ex105-12312025.htm"),
}
CUSIP = {"CMG": "169656105", "HLT": "43300A203", "AAPL": "037833100", "CVX": "166764100", "CB": "H1467J104"}


def src(key: str, locator: str) -> dict:
    cik, acc, doc = DOCS[key]
    return {"accession": acc, "document": doc, "locator": locator, "url": archive_url(cik, acc, doc)}


def src13f(key: str, locator: str = "information table") -> dict:
    cik, acc = F13[key]
    return {"accession": acc, "document": f"{acc}.txt", "locator": locator,
            "url": archive_url(cik, acc, f"{acc}-index.html")}


def slot(qid, name, concept, entity, period, value, unit="USD", status="reported", source=None, **kw):
    return {"question_id": qid, "slot": name, "concept": concept, "entity": entity, "period": period,
            "value": value, "unit": unit, "status": status, "source": source, **kw}


def statements_gold() -> list[dict]:
    tgt = load_companyfacts(companyfacts(TGT))
    cm = load_companyfacts(companyfacts(CMCSA))
    E_T, E_C = "0000027419", "0001166691"
    fye_t, fye_c = date(2026, 1, 31), date(2025, 12, 31)

    def one(facts, ent, concept, end, start=None):
        f = select(facts, ent, concept, end, start)
        assert f is not None, (ent, concept, end)
        return f

    cur = get_metric(tgt, E_T, "long_term_debt_current", fye_t).fact
    non = get_metric(tgt, E_T, "long_term_debt_noncurrent", fye_t).fact
    total = get_metric(tgt, E_T, "total_long_term_debt", fye_t)
    assert total.status == "derived"
    notes = one(tgt, E_T, "us-gaap:LongTermDebt", fye_t)
    fy = one(tgt, E_T, "us-gaap:NetCashProvidedByUsedInOperatingActivities", fye_t, date(2025, 2, 2))
    ytd = one(tgt, E_T, "us-gaap:NetCashProvidedByUsedInOperatingActivities", date(2025, 11, 1), date(2025, 2, 2))
    c_cur = one(cm, E_C, "us-gaap:DebtCurrent", fye_c)
    c_non = one(cm, E_C, "us-gaap:LongTermDebtAndCapitalLeaseObligations", fye_c)
    c_fy = one(cm, E_C, "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", fye_c, date(2025, 1, 1))
    c_ytd = one(cm, E_C, "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", date(2025, 9, 30), date(2025, 1, 1))
    T, C = "Target Corporation", "Comcast Corporation"
    bs_t = src("TGT-10K", "Consolidated Statements of Financial Position; Note 17")
    return [
        slot("TGT-1", "current_portion", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent", T, "FY2025", cur.value,
             source=bs_t, printed="2,130", scale=M),
        slot("TGT-1", "noncurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligations", T, "FY2025", non.value,
             source=bs_t, printed="14,326", scale=M),
        slot("TGT-1", "total_including_current", "us-gaap:LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
             T, "FY2025", total.fact.value, status=total.status, source=bs_t, formula=total.fact.derivation,
             notes=f"not printed; Note 17 'Total notes and debentures' {notes.value / M:,.0f} is tagged us-gaap:LongTermDebt "
                   "and is the neighbouring-line trap"),
        slot("TGT-1", "as_of_date", "dei:DocumentPeriodEndDate", T, "FY2025", "2026-01-31", unit="date", source=bs_t),
        slot("TGT-2", "q4_operating_cash_flow", "us-gaap:NetCashProvidedByUsedInOperatingActivities", T, "Q4 FY2025",
             fy.value - ytd.value, status="derived", formula="FY - 9M YTD",
             inputs=[{"period": "FY2025 (2025-02-02..2026-01-31)", "value": fy.value, "accession": fy.accession},
                     {"period": "9M (2025-02-02..2025-11-01)", "value": ytd.value, "accession": ytd.accession}],
             source=src("TGT-10K", "Consolidated Statements of Cash Flows")),
        slot("TGT-2", "method_or_components", "text", T, "Q4 FY2025", None, unit="text", status="text",
             gold_text=f"FY {fy.value / M:,.0f} minus 9M YTD {ytd.value / M:,.0f} ($M)"),
        slot("CMCSA-1", "current_portion", "us-gaap:DebtCurrent", C, "FY2025", c_cur.value,
             source=src("CMCSA-10K", "Consolidated Balance Sheets"), printed="5,958", scale=M),
        slot("CMCSA-1", "noncurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligations", C, "FY2025", c_non.value,
             source=src("CMCSA-10K", "Consolidated Balance Sheets"), printed="92,979", scale=M),
        slot("CMCSA-1", "total_debt", "us-gaap:DebtAndCapitalLeaseObligations", C, "FY2025", c_cur.value + c_non.value,
             status="derived", formula="current + noncurrent", source=src("CMCSA-10K", "Consolidated Balance Sheets")),
        slot("CMCSA-2", "q4_capex", "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", C, "Q4 FY2025",
             c_fy.value - c_ytd.value, status="derived", formula="FY - 9M YTD",
             inputs=[{"period": "FY2025", "value": c_fy.value, "accession": c_fy.accession},
                     {"period": "9M to 2025-09-30", "value": c_ytd.value, "accession": c_ytd.accession}],
             source=src("CMCSA-10K", "Consolidated Statements of Cash Flows"),
             notes="8-K earnings-release cross-check not downloaded this week"),
    ]


def proxy_rows(cik):
    out = []
    for acc, doc, fye in PROXIES[cik]:
        html = cache_path(archive_url(cik, acc, doc)).read_text(encoding="utf-8", errors="replace")
        out.append(parse_nqdc_table(html, fiscal_year_end=fye, source=acc))
    return out


def nqdc_gold() -> tuple[list[dict], list[dict]]:
    T, C = "Target Corporation", "Comcast Corporation"
    tgt_cur, tgt_pri = proxy_rows(TGT)
    cm_cur, cm_pri = proxy_rows(CMCSA)
    roll = [{"entity": name, **c.model_dump()} for name, cur, pri in (("TGT", tgt_cur, tgt_pri), ("CMCSA", cm_cur, cm_pri))
            for c in check_table_roll_forward(cur, pri)]
    cornell = next(r for r in tgt_cur if r.executive.startswith("Brian C. Cornell"))
    roberts = next(r for r in cm_cur if r.executive.startswith("Brian L. Roberts"))
    p_t = {"accession": PROXIES[TGT][0][0], "document": PROXIES[TGT][0][1],
           "locator": "Nonqualified deferred compensation for Fiscal 2025",
           "url": archive_url(TGT, PROXIES[TGT][0][0], PROXIES[TGT][0][1])}
    p_c = {"accession": PROXIES[CMCSA][0][0], "document": PROXIES[CMCSA][0][1],
           "locator": "Nonqualified Deferred Compensation in and as of Fiscal Year-End",
           "url": archive_url(CMCSA, PROXIES[CMCSA][0][0], PROXIES[CMCSA][0][1])}
    s_t, s_c = f"NEO:{cornell.executive}", f"NEO:{roberts.executive}"
    tgt_note = src("TGT-10K", "Note 24 Defined Contribution Plans")
    cm_note = src("CMCSA-10K", "Note 11 Employee Benefit Plans, Deferred Compensation Plans")
    gold = [
        slot("TGT-3", "executive_contributions", "nqdc:ExecutiveContributions", T, "FY2025", cornell.executive_contributions, source=p_t, scope=s_t),
        slot("TGT-3", "registrant_contributions", "nqdc:RegistrantContributions", T, "FY2025", cornell.registrant_contributions, source=p_t, scope=s_t),
        slot("TGT-3", "aggregate_earnings", "nqdc:AggregateEarnings", T, "FY2025", cornell.aggregate_earnings, source=p_t, scope=s_t),
        slot("TGT-3", "year_end_balance", "nqdc:AggregateBalance", T, "FY2025", cornell.aggregate_balance, source=p_t, scope=s_t),
        slot("TGT-4", "plan_liability", "nqdc:PlanLiability", T, "FY2025", 717 * M, source=tgt_note, scope="company-wide",
             printed="717", scale=M, notes="text only; structured data tags DeferredCompensationLiabilityClassifiedNoncurrent = 650 (noncurrent part)"),
        slot("TGT-4", "funding_status", "text", T, "FY2025", None, unit="text", status="text", source=tgt_note,
             gold_text="unfunded; risk mitigated with company-owned life insurance and prepaid forward contracts on Target stock (general corporate assets)"),
        slot("TGT-4", "coli_value", "nqdc:COLICashSurrenderValue", T, "FY2025", 600 * M, scope="company-wide",
             source=src("TGT-10K", "Note 13 Other Noncurrent Assets"), printed="600", scale=M,
             notes="'Company-owned life insurance investments, net of loans' - an ASSET; prepaid forwards 18 (Note 8) also an asset"),
        slot("TGT-4", "current_investment_options", "text", T, "FY2025", None, unit="text", status="text",
             source=src("TGT-EDCP2025", "1.2.13 Crediting Rate Alternative"),
             gold_text="crediting rate alternatives = corresponding Target 401(k) Plan funds, plus a Target common stock fund (proxy); "
                       "not the 2017 statement's Stable Value / Money Market list"),
        slot("CMCSA-3", "executive_contributions", "nqdc:ExecutiveContributions", C, "FY2025", roberts.executive_contributions, source=p_c, scope=s_c),
        slot("CMCSA-3", "registrant_contributions", "nqdc:RegistrantContributions", C, "FY2025", None, status="not_reported",
             source=p_c, scope=s_c, notes="table has no registrant-contribution column; any number is invented"),
        slot("CMCSA-3", "aggregate_earnings", "nqdc:AggregateEarnings", C, "FY2025", roberts.aggregate_earnings, source=p_c, scope=s_c),
        slot("CMCSA-3", "year_end_balance", "nqdc:AggregateBalance", C, "FY2025", roberts.aggregate_balance, source=p_c, scope=s_c),
        slot("CMCSA-4", "benefit_obligation", "nqdc:PlanLiability", C, "FY2025", 5_097 * M, source=cm_note, scope="company-wide",
             printed="5,097", scale=M, notes="tagged us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent"),
        slot("CMCSA-4", "funding_status", "text", C, "FY2025", None, unit="text", status="text", source=cm_note,
             gold_text="unfunded; life insurance purchased to recover a portion of future payments"),
        slot("CMCSA-4", "coli_cash_surrender_value", "nqdc:COLICashSurrenderValue", C, "FY2025", 662 * M, source=cm_note,
             scope="company-wide", printed="662", scale=M,
             notes="an ASSET; the same note also prints 401(k) plan expense of 662 (different concept)"),
        slot("CMCSA-4", "base_salary_deferral_limit", "text", C, "FY2025", None, unit="text", status="text",
             source=src("CMCSA-DCP2005", "Section 2.42.3(a)(i)"),
             gold_text="the maximum base salary available for deferral is determined net of required withholdings and "
                       "deductions and shall in no event be more than 85% of base salary",
             stale_text="2024-05-14 version (FY2024 10-K Ex. 10.5): 'shall in no event be less than 85%' - a floor, not a cap",
             stale_source={"accession": "0001166691-25-000011", "document": "ex105-12312024.htm",
                           "url": archive_url(CMCSA, "0001166691-25-000011", "ex105-12312024.htm")}),
    ]
    return gold, roll


def load_13f():
    return {k: parse_full_submission(full_submission(cik, acc).decode("utf-8", "replace")) for k, (cik, acc) in F13.items()}


def f13_gold(filings) -> tuple[list[dict], list[dict], dict]:
    store = HoldingsStore.from_filings(filings.values())
    P, B = "0001336528", "0001067983"
    q3, q4, q3_23, q4_23 = date(2022, 9, 30), date(2022, 12, 31), date(2023, 9, 30), date(2023, 12, 31)
    PS, BH = "Pershing Square Capital Management, L.P.", "Berkshire Hathaway Inc"

    rows = []
    for key, f in filings.items():
        for r in f.rows:
            ev = row_unit_evidence(r)
            rows.append({"filing": key, "accession": r.accession, "form": f.meta.form_type,
                         "amendment_type": f.meta.amendment_type or "", "filing_date": r.filing_date,
                         "period": f.meta.period_of_report, "issuer": r.name_of_issuer, "class": r.title_of_class,
                         "cusip": r.cusip, "shares": r.ssh_prnamt, "sh_prn": r.ssh_prnamt_type, "put_call": r.put_call or "",
                         "other_managers": " ".join(map(str, r.other_managers)), "original_value": r.original_value,
                         "rule_unit": ev.rule_unit, "evidence_unit": ev.evidence_unit,
                         "price_if_dollars": round(ev.price_if_dollars, 4) if ev.price_if_dollars is not None else "",
                         "price_if_thousands": round(ev.price_if_thousands, 2) if ev.price_if_thousands is not None else "",
                         "conflict": ev.conflict, "value_usd": r.value_usd})

    cmg = position(store, P, CUSIP["CMG"], q4)
    hlt = qoq_change(store, P, CUSIP["HLT"], q3, q4)
    aapl = position(store, B, CUSIP["AAPL"], q4)
    cvx = qoq_change(store, B, CUSIP["CVX"], q3, q4)
    p_q3 = store.rows(P, q3)
    b_q3_23 = store.rows(B, q3_23)
    cb_q4 = position(store, B, CUSIP["CB"], q4_23)
    top = sorted(p_q3, key=lambda r: -r.value_usd)
    orig_vs_rs = _row_diff(filings["BRK-2023Q3"], filings["BRK-2023Q3-RS"])
    gold = [
        slot("PSCM-1", "shares", f"13f:Shares[{CUSIP['CMG']}]", PS, "2022Q4", cmg.shares, unit="shares", source=src13f("PSCM-2022Q4")),
        slot("PSCM-1", "market_value", f"13f:Value[{CUSIP['CMG']}]", PS, "2022Q4", cmg.value_usd, source=src13f("PSCM-2022Q4"),
             original_value=cmg.original_values[0][0], original_unit=cmg.original_values[0][1]),
        slot("PSCM-1", "as_of_date", "13f:PeriodOfReport", PS, "2022Q4", "2022-12-31", unit="date", source=src13f("PSCM-2022Q4", "cover page")),
        slot("PSCM-2", "q3_value", f"13f:Value[{CUSIP['HLT']}]", PS, "2022Q3", hlt.start.value_usd, source=src13f("PSCM-2022Q3"),
             original_value=hlt.start.original_values[0][0], original_unit=hlt.start.original_values[0][1]),
        slot("PSCM-2", "q4_value", f"13f:Value[{CUSIP['HLT']}]", PS, "2022Q4", hlt.end.value_usd, source=src13f("PSCM-2022Q4"),
             original_value=hlt.end.original_values[0][0], original_unit=hlt.end.original_values[0][1]),
        slot("PSCM-2", "change", f"13f:ValueChange[{CUSIP['HLT']}]", PS, "2022Q3-2022Q4", hlt.change_usd, status="derived",
             formula="q4 - q3 (both in dollars)", change_pct=hlt.change_pct),
        slot("PSCM-2", "share_change", f"13f:SharesChange[{CUSIP['HLT']}]", PS, "2022Q3-2022Q4", hlt.change_shares,
             unit="shares", status="derived"),
        slot("PSCM-3", "total_value", "13f:TableValueTotal", PS, "2022Q3", sum(r.value_usd for r in p_q3),
             source=src13f("PSCM-2022Q3", "summary page tableValueTotal"),
             original_value=filings["PSCM-2022Q3"].meta.table_value_total, original_unit="USD_thousands"),
        slot("PSCM-3", "number_of_positions", "13f:TableEntryTotal", PS, "2022Q3", len(p_q3), unit="count",
             source=src13f("PSCM-2022Q3", "summary page tableEntryTotal")),
        slot("PSCM-3", "top_positions", "text", PS, "2022Q3", None, unit="text", status="text",
             gold_text=", ".join(f"{r.name_of_issuer} {r.value_usd / 1e9:.3f}bn" for r in top)),
        slot("PSCM-4", "amendment_filed", "text", PS, "2022Q4", None, unit="text", status="text",
             gold_text="no 13F-HR/A for 2022-12-31 (EDGAR submissions API)"),
        slot("PSCM-4", "number_of_positions", "13f:TableEntryTotal", PS, "2022Q4", filings["PSCM-2022Q4"].meta.table_entry_total,
             unit="count", source=src13f("PSCM-2022Q4", "summary page")),
        slot("PSCM-4", "filing_date", "13f:FilingDate", PS, "2022Q4", "2023-02-13", unit="date", source=src13f("PSCM-2022Q4", "SGML header"),
             notes="accepted 2023-02-10 17:55 ET"),
        slot("BRK-1", "shares_total_filer_level", f"13f:Shares[{CUSIP['AAPL']}]", BH, "2022Q4", aapl.shares, unit="shares",
             source=src13f("BRK-2022Q4"), scope="filer (all other-manager rows)", rows=len(aapl.original_values)),
        slot("BRK-1", "market_value", f"13f:Value[{CUSIP['AAPL']}]", BH, "2022Q4", aapl.value_usd, source=src13f("BRK-2022Q4"),
             scope="filer (all other-manager rows)", original_unit="USD"),
        slot("BRK-2", "q3_value", f"13f:Value[{CUSIP['CVX']}]", BH, "2022Q3", cvx.start.value_usd, source=src13f("BRK-2022Q3"),
             original_value=sum(v for v, _ in cvx.start.original_values), original_unit="USD_thousands"),
        slot("BRK-2", "q4_value", f"13f:Value[{CUSIP['CVX']}]", BH, "2022Q4", cvx.end.value_usd, source=src13f("BRK-2022Q4"),
             original_value=sum(v for v, _ in cvx.end.original_values), original_unit="USD"),
        slot("BRK-2", "change", f"13f:ValueChange[{CUSIP['CVX']}]", BH, "2022Q3-2022Q4", cvx.change_usd, status="derived",
             formula="q4 - q3 (both in dollars)", change_pct=cvx.change_pct, share_change=cvx.change_shares),
        slot("BRK-3", "total_value", "13f:TableValueTotal", BH, "2023Q3", sum(r.value_usd for r in b_q3_23), status="derived",
             formula="restatement total + new-holdings total", source=src13f("BRK-2023Q3-RS", "summary page"),
             inputs=[{"accession": F13[k][1], "value": filings[k].meta.table_value_total} for k in ("BRK-2023Q3-RS", "BRK-2023Q3-NH")],
             notes=f"original vs restatement rows: {orig_vs_rs}"),
        slot("BRK-3", "number_of_positions", "13f:DistinctCusips", BH, "2023Q3",
             len({r.cusip for r in b_q3_23 if r.put_call is None}), unit="count", status="derived",
             distinct_issuers=len({r.name_of_issuer for r in b_q3_23}), rows=len(b_q3_23)),
        slot("BRK-3", "amendments_accounted", "text", BH, "2023Q3", None, unit="text", status="text",
             gold_text=f"RESTATEMENT {F13['BRK-2023Q3-RS'][1]} (2023-11-16) replaces the original; "
                       f"NEW HOLDINGS {F13['BRK-2023Q3-NH'][1]} (2024-05-15) adds Chubb"),
        slot("BRK-4", "held_at_2023-12-31", f"13f:Shares[{CUSIP['CB']}]", BH, "2023Q4", cb_q4.shares, unit="shares",
             source=src13f("BRK-2023Q4-NH"), value_usd=cb_q4.value_usd, gold_text="yes: CHUBB LIMITED, confidential until the amendment"),
        slot("BRK-4", "first_public_date", "13f:FilingDate", BH, "2023Q3", "2024-05-15", unit="date", source=src13f("BRK-2023Q3-NH", "SGML header")),
        slot("BRK-4", "first_quarter_held", "text", BH, "2023Q3", None, unit="text", status="text",
             gold_text=f"Q3 2023 (NEW HOLDINGS amendment {F13['BRK-2023Q3-NH'][1]})"),
    ]
    return gold, rows, {"top": top}


def _row_diff(a, b) -> str:
    amounts = lambda r: (r.cusip, r.put_call, r.ssh_prnamt, r.original_value)
    same_amounts = sorted(map(amounts, a.rows)) == sorted(map(amounts, b.rows))
    om = lambda f: sum(bool(r.other_managers) for r in f.rows)
    return (f"{'same' if same_amounts else 'different'} (cusip, shares, value) rows; rows naming other managers: "
            f"original {om(a)}/{len(a.rows)}, restatement {om(b)}/{len(b.rows)}")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    filings = load_13f()
    g13, rows, _ = f13_gold(filings)
    gn, roll = nqdc_gold()
    gold = statements_gold() + gn + g13
    order = [json.loads(l)["id"] for l in (Path(__file__).parent / "questions.jsonl").read_text().splitlines() if l.strip()]
    gold.sort(key=lambda g: order.index(g["question_id"]))
    with open(OUT / "pilot_gold.jsonl", "w") as fh:
        for g in gold:
            fh.write(json.dumps(g, default=str) + "\n")
    for name, data in (("13f_rows.csv", rows), ("nqdc_roll_forward.csv", roll)):
        with open(OUT / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)

    conflicts = [r for r in rows if r["conflict"]]
    by_ev = {}
    for r in rows:
        by_ev[r["evidence_unit"]] = by_ev.get(r["evidence_unit"], 0) + 1
    print(f"gold slots: {len(gold)} for {len({g['question_id'] for g in gold})} questions")
    print(f"13F rows: {len(rows)}; evidence units {by_ev}; conflicts: {len(conflicts)}")
    for r in conflicts:
        print(f"  CONFLICT {r['accession']} {r['issuer']} {r['original_value']} rule={r['rule_unit']} evidence={r['evidence_unit']} "
              f"px$={r['price_if_dollars']} px$k={r['price_if_thousands']}")
    for r in rows:
        if r["evidence_unit"] in ("ambiguous", "none"):
            print(f"  {r['evidence_unit'].upper()} {r['accession']} {r['issuer']} {r['original_value']} sh={r['shares']} "
                  f"px$={r['price_if_dollars']} px$k={r['price_if_thousands']}")
    for c in roll:
        print(f"  roll-forward {c['entity']} {c['executive']}: {c['status']} diff={c['difference']} {c['note']}")


if __name__ == "__main__":
    main()
