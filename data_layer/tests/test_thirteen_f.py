import re
from datetime import date

import pytest

from data_layer.thirteen_f import (
    USD, USD_THOUSANDS, CoFilingGroup, HoldingsStore, UnitOverride, group_rows,
    load_full_submission, merge_all, merge_filings, normalize_value, parse_filing,
    position, position_facts, qoq_change, row_unit_evidence, suggest_groups, summary_total_difference,
    unit_diagnostics, validate_xml, value_unit_for_filing_date,
)

A, B, C = "0009000001", "0009000002", "0009000003"
EXAMPLE, SHARED, NEWCO = "99999A101", "99999C103", "99999D104"


def real(fx, name):
    return load_full_submission(fx / "13f" / "real" / name)


def syn(fx, name):
    return load_full_submission(fx / "13f" / "synthetic" / name)


def synthetic_all(fx):
    return [load_full_submission(p) for p in sorted((fx / "13f" / "synthetic").glob("*.txt"))]



@pytest.mark.parametrize("filed, unit", [
    ("2022-11-14", USD_THOUSANDS), ("2023-01-02", USD_THOUSANDS),
    ("2023-01-03", USD), ("2023-02-14", USD), ("20230103", USD),
])
def test_unit_follows_filing_date(filed, unit):
    assert value_unit_for_filing_date(filed) == unit


def test_normalize_value():
    assert normalize_value(1_234_567, "2022-11-14") == 1_234_567_000
    assert normalize_value(1_198_000_000, date(2023, 2, 14)) == 1_198_000_000


def test_real_pre_period_filed_after_change_is_dollars(fx):
    f = real(fx, "0001140361-23-013281.txt")
    assert f.meta.period_of_report == date(2021, 12, 31)
    assert f.meta.filing_date == date(2023, 3, 23)
    assert f.meta.value_unit == USD
    spy = next(r for r in f.rows if "SPDR" in r.name_of_issuer)
    assert (spy.original_value, spy.value_usd, spy.ssh_prnamt) == (4_245_201, 4_245_201, 9_866)
    assert sum(r.value_usd for r in f.rows) == 11_019_796
    assert not unit_diagnostics(f).suspect


def test_real_thousands_era_filing(fx):
    f = real(fx, "0001619124-21-000002.txt")
    assert f.meta.value_unit == USD_THOUSANDS
    r = f.rows[0]
    assert r.original_unit == USD_THOUSANDS and r.value_usd == r.original_value * 1000
    assert not unit_diagnostics(f).suspect


def test_real_post_change_filing(fx):
    f = real(fx, "0001894188-23-000007.txt")
    assert f.meta.accession == "0001894188-23-000007"
    assert f.meta.form_type == "13F-HR" and f.meta.value_unit == USD
    assert len(f.rows) == f.meta.table_entry_total
    assert summary_total_difference(f) == 0


def test_filer_deviation_is_flagged_not_fixed(fx):
    f = real(fx, "0001790837-23-000002.txt")
    assert f.meta.value_unit == USD
    d = unit_diagnostics(f)
    assert d.suspect and d.share_sub_dollar > 0.9
    jnj = next(r for r in f.rows if "JOHNSON" in r.name_of_issuer.upper())
    assert (jnj.original_value, jnj.ssh_prnamt) == (8_484, 48_027)
    assert jnj.value_usd == 8_484

    ov = {f.meta.accession: UnitOverride(accession=f.meta.accession, unit=USD_THOUSANDS,
                                         reason="filer reported thousands after 2023-01-03 (verified)")}
    g = load_full_submission(fx / "13f" / "real" / "0001790837-23-000002.txt", unit_overrides=ov)
    jnj = next(r for r in g.rows if "JOHNSON" in r.name_of_issuer.upper())
    assert jnj.value_usd == 8_484_000
    assert g.meta.unit_basis.startswith("override:")


def test_kahn_dollars_before_change_flagged(fx):
    d = fx / "13f" / "real"
    f = parse_filing((d / "0001039565-22-000009.primary_doc.xml").read_bytes(),
                     (d / "0001039565-22-000009.infotable.xml").read_bytes(),
                     accession="0001039565-22-000009", filing_date="2022-05-02")
    assert f.meta.value_unit == USD_THOUSANDS
    diag = unit_diagnostics(f)
    assert diag.suspect and diag.median_implied_price > 10_000
    assert summary_total_difference(f) == -1


def test_row_unit_evidence_on_both_real_deviations(fx):
    bull = real(fx, "0001790837-23-000002.txt")
    jnj = row_unit_evidence(next(r for r in bull.rows if "JOHNSON" in r.name_of_issuer.upper()))
    assert (jnj.rule_unit, jnj.evidence_unit, jnj.conflict) == (USD, USD_THOUSANDS, True)
    d = fx / "13f" / "real"
    kahn = parse_filing((d / "0001039565-22-000009.primary_doc.xml").read_bytes(),
                        (d / "0001039565-22-000009.infotable.xml").read_bytes(),
                        accession="0001039565-22-000009", filing_date="2022-05-02")
    alcon = row_unit_evidence(next(r for r in kahn.rows if "ALCON" in r.name_of_issuer.upper()))
    assert (alcon.rule_unit, alcon.evidence_unit, alcon.conflict) == (USD_THOUSANDS, USD, True)
    assert alcon.price_if_dollars == pytest.approx(80.0)
    actiam = real(fx, "0001619124-21-000002.txt")
    assert not any(row_unit_evidence(r).conflict for r in actiam.rows)



def test_row_fields(fx):
    f = syn(fx, "A_2022Q3_13F-HR.txt")
    shared = next(r for r in f.rows if r.cusip == SHARED)
    assert shared.investment_discretion == "DFND" and shared.other_managers == [1]
    assert shared.voting_shared == 50_000 and shared.ssh_prnamt_type == "SH"
    put = next(r for r in f.rows if r.put_call == "Put")
    assert put.cusip == EXAMPLE
    assert f.meta.other_included_managers[0].cik == B
    assert f.meta.other_included_managers[0].sequence_number == 1


def test_cover_page_other_managers(fx):
    metlife = real(fx, "0001140361-23-013281.txt")
    assert metlife.meta.report_type == "13F COMBINATION REPORT"
    assert [m.cik for m in metlife.meta.other_managers_reporting] == [
        "0000931588", "0000728618", "0001529735"]
    nt = syn(fx, "C_2022Q4_13F-NT.txt")
    assert nt.meta.form_type == "13F-NT" and nt.rows == []
    assert nt.meta.other_managers_reporting[0].cik == A


def test_sec_sample_amendment(fx):
    s = fx / "13f" / "sec_samples"
    f = parse_filing((s / "Sample_13F-HRA.xml").read_bytes(), accession="SAMPLE-HRA",
                     filing_date="2021-11-01")
    assert f.meta.form_type == "13F-HR/A"
    assert f.meta.is_amendment and f.meta.amendment_type == "RESTATEMENT"
    assert f.meta.amendment_no == 45
    assert len(f.rows) == 169


def test_all_fixtures_follow_official_schema(fx):
    xsd = fx / "13f" / "xsd"
    for p in sorted((fx / "13f").glob("*/*.txt")):
        for block in re.findall(r"<XML>\s*(.*?)</XML>", p.read_text(), re.S):
            assert validate_xml(block, xsd) == [], p.name



def test_new_holdings_amendment_adds_rows_in_its_own_unit(fx):
    orig = syn(fx, "A_2022Q3_13F-HR.txt")
    amd = syn(fx, "A_2022Q3_13F-HRA_newholdings.txt")
    h = merge_filings(orig, [amd])
    assert len(h.rows) == len(orig.rows) + 1
    assert [s.action for s in h.history] == ["original", "new_holdings_added"]
    newco = next(r for r in h.rows if r.cusip == NEWCO)
    assert newco.original_unit == USD and newco.value_usd == 3_000_000
    assert newco.accession == "0009000001-23-000002"
    ex = next(r for r in h.rows if r.cusip == EXAMPLE and r.put_call is None)
    assert ex.value_usd == 1_234_567_000 and ex.accession == "0009000001-22-000005"


def test_restatement_replaces_not_adds(fx):
    orig = syn(fx, "A_2022Q4_13F-HR.txt")
    rest = syn(fx, "A_2022Q4_13F-HRA_restatement.txt")
    h = merge_filings(orig, [rest])
    assert len(h.rows) == len(rest.rows)
    assert {r.accession for r in h.rows} == {"0009000001-23-000003"}
    naive = sum(r.value_usd for r in orig.rows + rest.rows if r.cusip == EXAMPLE)
    merged = sum(r.value_usd for r in h.rows if r.cusip == EXAMPLE)
    assert merged == 1_199_000_000 and naive == 2_397_000_000


def test_merge_rejects_mismatched_period(fx):
    with pytest.raises(ValueError):
        merge_filings(syn(fx, "A_2022Q3_13F-HR.txt"), [syn(fx, "A_2022Q4_13F-HRA_restatement.txt")])



def test_suggest_groups_from_manager_lists(fx):
    assert suggest_groups(synthetic_all(fx)) == [{A, B, C}]


def test_group_dedup_shared_position(fx):
    holdings = [h for h in merge_all(synthetic_all(fx)) if h.period == date(2022, 12, 31)]
    g = CoFilingGroup(group_id="G-ALPHA", name="Synthetic Alpha", members=[A, B, C])
    gh = group_rows(g, holdings)
    assert len(gh.dropped) == 1
    assert gh.dropped[0].row.filer_cik == B and gh.dropped[0].row.cusip == SHARED
    store = HoldingsStore(holdings, [g])
    assert position(store, "G-ALPHA", SHARED, "2022-12-31").value_usd == 2_100_000
    assert position(store, A, SHARED, "2022-12-31").value_usd == 2_100_000
    assert position(store, B, SHARED, "2022-12-31").value_usd == 2_100_000
    assert position(store, "G-ALPHA", "99999E105", "2022-12-31").value_usd == 700_000



def test_qoq_change_in_dollars_boundary_pair(fx):
    """Walk-through 2 on the boundary pair: Q3 2022 (thousands) vs Q4 2022 (dollars)."""
    store = HoldingsStore.from_filings(synthetic_all(fx))
    ch = qoq_change(store, A, EXAMPLE, "2022-09-30", "2022-12-31")
    assert ch.start.value_usd == 1_234_567_000
    assert ch.end.value_usd == 1_199_000_000
    assert ch.start.original_values == [(1_234_567, USD_THOUSANDS)]
    assert ch.change_usd == 1_199_000_000 - 1_234_567_000
    assert -0.03 < ch.change_pct < -0.028
    assert ch.change_shares == 0


def test_position_excludes_options_by_default(fx):
    store = HoldingsStore.from_filings(synthetic_all(fx))
    assert position(store, A, EXAMPLE, "2022-09-30").value_usd == 1_234_567_000
    assert position(store, A, EXAMPLE, "2022-09-30", include_options=True).value_usd == 1_234_717_000


def test_position_facts_keep_original_unit(fx):
    store = HoldingsStore.from_filings(synthetic_all(fx))
    facts = position_facts(store, A, "2022-09-30")
    v = next(f for f in facts if f.concept == "13f:PositionValue" and f.dimensions["cusip"] == EXAMPLE)
    assert (v.value, v.unit, v.original_value, v.original_unit) == (
        1_234_567_000, USD, 1_234_567, USD_THOUSANDS)
