from datetime import date

import pytest

from data_layer.nqdc import (
    CompanyNQDCRecord, FundingAsset, NQDCTableRow, PlanDocumentVersion, check_roll_forward,
    check_table_roll_forward, normalize_column, parse_amount, parse_nqdc_table,
    table_rows_to_facts, totals_by_executive,
)


@pytest.mark.parametrize("header, field", [
    ("Executive Contributions in Last FY ($)(1)", "executive_contributions"),
    ("Registrant  Contributions  in Last FY  ($)(2)", "registrant_contributions"),
    ("Company Contributions in 2023", "registrant_contributions"),
    ("Aggregate Earnings in Last FY ($)(3)", "aggregate_earnings"),
    ("Aggregate Withdrawals/ Distributions ($)", "aggregate_withdrawals"),
    ("Aggregate Balance at Last FYE ($)(4)", "aggregate_balance"),
    ("Name", "executive"),
    ("Plan", "plan"),
])
def test_normalize_column(header, field):
    assert normalize_column(header) == field


@pytest.mark.parametrize("cell, value", [
    ("1,234", 1234.0), ("$ 1,234", 1234.0), ("(45,000)", -45000.0), ("—", 0.0),
    ("-", 0.0), ("", None), (None, None), ("321138", 321138.0),
    ("(1,909,423", -1909423.0),
])
def test_parse_amount(cell, value):
    assert parse_amount(cell) == value


def test_real_cabot_multi_plan_table(fx):
    rows = parse_nqdc_table((fx / "nqdc" / "cabot_def14a_fy2023_nqdc_table.html").read_text(),
                            fiscal_year_end=date(2023, 9, 30), source="Cabot DEF 14A FY2023")
    assert len(rows) == 9
    k = {r.plan: r for r in rows if r.executive == "Sean D. Keohane"}
    dcp, s401 = k["Deferred Compensation Plan"], k["Supplemental 401(k) Plan"]
    assert (dcp.executive_contributions, dcp.registrant_contributions, dcp.aggregate_earnings,
            dcp.aggregate_withdrawals, dcp.aggregate_balance) == (522852, 52285, 134265, 0, 2967182)
    assert (s401.executive_contributions, s401.registrant_contributions, s401.aggregate_earnings,
            s401.aggregate_balance) == (0, 127535, 247413, 2700426)
    kalita = next(r for r in rows if r.executive == "Karen A. Kalita" and r.plan == "Deferred Compensation Plan")
    assert kalita.aggregate_withdrawals == 20138
    zhu = [r for r in rows if r.executive == "Jeff Zhu"]
    assert len(zhu) == 1 and zhu[0].plan == "China Supplemental Pension Plan"
    assert zhu[0].aggregate_balance == 321138
    assert totals_by_executive(rows)["Sean D. Keohane"]["aggregate_balance"] == 2967182 + 2700426


def test_synthetic_single_plan_table_skips_total(fx):
    rows = parse_nqdc_table((fx / "nqdc" / "synthetic_proxy_fy2023_nqdc.html").read_text())
    assert [r.executive for r in rows] == ["Alex Example, CEO", "Blair Sample, CFO",
                                           "Casey Placeholder, COO"]
    assert rows[1].aggregate_earnings == -45000 and rows[1].aggregate_withdrawals == 100000
    assert rows[0].aggregate_withdrawals == 0


def test_roll_forward(fx):
    cur = parse_nqdc_table((fx / "nqdc" / "synthetic_proxy_fy2023_nqdc.html").read_text())
    pri = parse_nqdc_table((fx / "nqdc" / "synthetic_proxy_fy2022_nqdc.html").read_text())
    res = {r.executive: r for r in check_table_roll_forward(cur, pri)}
    assert res["Alex Example, CEO"].status == "ok"
    assert res["Blair Sample, CFO"].status == "ok"
    casey = res["Casey Placeholder, COO"]
    assert casey.status == "mismatch" and casey.difference == 48000
    assert check_roll_forward(cur[0], None).status == "not_checkable"


def test_roll_forward_tolerance():
    prior = NQDCTableRow(executive="X", aggregate_balance=1_000_000)
    cur = NQDCTableRow(executive="X", executive_contributions=10_000, aggregate_earnings=5_000,
                       aggregate_balance=1_015_400)
    assert check_roll_forward(cur, prior).status == "ok"
    assert check_roll_forward(cur, prior, abs_tol=100, rel_tol=0).status == "mismatch"


def _walkthrough_record():
    return CompanyNQDCRecord(
        entity="0009999999", fiscal_year_end=date(2023, 12, 31), plan_name="Deferred Compensation Plan",
        plan_type="elective_deferral", plan_liability=61_200_000,
        funding_assets=[FundingAsset(vehicle="coli_cash_surrender_value", value=74_900_000,
                                     concept="us-gaap:CashSurrenderValueOfLifeInsurance",
                                     in_rabbi_trust=True)],
        plan_documents=[
            PlanDocumentVersion(title="Deferred Compensation Plan", effective_date=date(2015, 1, 1), exhibit="10.8"),
            PlanDocumentVersion(title="Deferred Compensation Plan (amended and restated)",
                                effective_date=date(2022, 1, 1), exhibit="10.9"),
        ])


def test_company_record_facts_and_versions():
    rec = _walkthrough_record()
    facts = {f.concept: f for f in rec.to_facts()}
    assert facts["us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent"].value == 61_200_000
    coli = facts["us-gaap:CashSurrenderValueOfLifeInsurance"]
    assert coli.value == 74_900_000 and "rabbi trust" in coli.label
    assert rec.document_in_force(date(2021, 6, 30)).exhibit == "10.8"
    assert rec.document_in_force(date(2023, 12, 31)).exhibit == "10.9"
    assert rec.document_in_force(date(2010, 1, 1)) is None


def test_table_rows_to_facts(fx):
    rows = parse_nqdc_table((fx / "nqdc" / "cabot_def14a_fy2023_nqdc_table.html").read_text(),
                            fiscal_year_end=date(2023, 9, 30))
    facts = table_rows_to_facts(rows, "0000016040")
    bal = [f for f in facts if f.concept == "nqdc:aggregate_balance"
           and f.dimensions["executive"] == "Jeff Zhu"]
    assert len(bal) == 1 and bal[0].value == 321138


def test_real_comcast_table_in_inline_xbrl_document(fx):
    """Comcast 2026 proxy: no registrant column, negatives split as "(1,909,423" + ")", and the table sits in
    a multi-MB inline-XBRL document that opens with an XML declaration."""
    table = (fx / "nqdc" / "cmcsa_def14a_2026_nqdc_table.html").read_text()
    doc = ('<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
           "<table><tr><td>Registrant contributions</td><td>balance of the plan</td></tr></table>"
           + table + "</body></html>")
    rows = {r.executive: r for r in parse_nqdc_table(doc, fiscal_year_end=date(2025, 12, 31))}
    cav = rows["Michael J. Cavanagh"]
    assert (cav.executive_contributions, cav.aggregate_earnings, cav.aggregate_withdrawals,
            cav.aggregate_balance) == (9_358_186, -1_909_423, -5_150_700, 43_321_897)
    assert rows["Brian L. Roberts"].registrant_contributions is None


def test_roll_forward_treats_printed_negative_withdrawals_as_outflow():
    prior = NQDCTableRow(executive="Michael J. Cavanagh", aggregate_balance=41_023_834)
    cur = NQDCTableRow(executive="Michael J. Cavanagh", executive_contributions=9_358_186,
                       aggregate_earnings=-1_909_423, aggregate_withdrawals=-5_150_700,
                       aggregate_balance=43_321_897)
    assert check_roll_forward(cur, prior).status == "ok"
    assert check_roll_forward(cur.model_copy(update={"aggregate_withdrawals": 5_150_700}), prior).status == "ok"
