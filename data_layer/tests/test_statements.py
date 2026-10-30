from datetime import date

import pytest

from data_layer.statements import (
    CONCEPT_BINDINGS, binding_for, derive_q4, facts_to_frame, get_metric, load_companyfacts, select,
)

AAPL = "0000320193"
FY23 = date(2023, 9, 30)


@pytest.fixture
def aapl(fx):
    return load_companyfacts(fx / "companyfacts" / "CIK0000320193_subset.json")


def test_load_companyfacts_rows(aapl):
    f = select(aapl, AAPL, "us-gaap:LongTermDebt", FY23)
    assert f.value == 105_103_000_000
    assert (f.form, f.accession, f.filed) == ("10-K", "0000320193-23-000106", date(2023, 11, 3))
    assert f.fiscal_period == "FY" and f.unit == "USD"
    df = facts_to_frame(aapl)
    assert {"concept", "unit", "value", "period_start", "period_end", "fiscal_period", "form",
            "filed", "accession"} <= set(df.columns)


def test_first_vs_latest_filed_policy(aapl):
    first = select(aapl, AAPL, "us-gaap:LongTermDebt", FY23, policy="first")
    assert first.value == 105_103_000_000
    all_vals = {f.value for f in aapl if f.concept == "us-gaap:LongTermDebt" and f.period_end == FY23}
    assert all_vals == {105_103_000_000, 105_100_000_000}


def test_debt_concepts_are_distinct(aapl):
    total = get_metric(aapl, AAPL, "total_long_term_debt", FY23)
    noncur = get_metric(aapl, AAPL, "long_term_debt_noncurrent", FY23)
    cur = get_metric(aapl, AAPL, "long_term_debt_current", FY23)
    assert total.status == noncur.status == cur.status == "reported"
    assert (total.fact.value, noncur.fact.value, cur.fact.value) == (
        105_103_000_000, 95_281_000_000, 9_822_000_000)
    assert noncur.fact.value + cur.fact.value == total.fact.value


def test_total_debt_derived_when_unreported(aapl):
    facts = [f for f in aapl if f.concept != "us-gaap:LongTermDebt"]
    m = get_metric(facts, AAPL, "total_long_term_debt", FY23)
    assert m.status == "derived" and m.fact.derived
    assert m.fact.value == 105_103_000_000
    assert m.fact.derivation == "LongTermDebtNoncurrent + LongTermDebtCurrent"


def test_q4_net_income_is_derived(aapl):
    m = derive_q4(aapl, AAPL, "net_income", FY23)
    assert m.status == "derived" and m.fact.derived
    assert m.fact.value == 96_995_000_000 - 74_039_000_000 == 22_956_000_000
    assert m.fact.period_start == date(2023, 7, 2)
    assert m.fact.derivation == "discrete Q4 = FY - 9M YTD"


def test_q4_eps_not_derivable(aapl):
    m = derive_q4(aapl, AAPL, "eps_diluted", FY23)
    assert m.status == "not_available" and "not additive" in m.note
    assert CONCEPT_BINDINGS["eps_diluted"].derivation is None


def test_binding_table_lookup():
    assert binding_for("us-gaap:LongTermDebtNoncurrent").metric == "long_term_debt_noncurrent"
    b = binding_for("total_long_term_debt")
    assert b.concepts == ["us-gaap:LongTermDebt"]
    assert "us-gaap:LongTermDebtNoncurrent" in b.not_to_confuse_with
    assert binding_for("us-gaap:Nonexistent") is None


def test_nvidia_deferred_comp_noncurrent(fx):
    nv = load_companyfacts(fx / "companyfacts" / "CIK0001045810_subset.json")
    m = get_metric(nv, "0001045810", "deferred_comp_liability_noncurrent", date(2021, 1, 31))
    assert m.status == "reported" and m.fact.value == 33_000_000
    assert get_metric(nv, "0001045810", "deferred_comp_liability_total",
                      date(2021, 1, 31)).status == "not_available"


def test_target_total_long_term_debt_uses_its_own_binding():
    from data_layer.binding_check import Claim as BClaim, check_claim
    from data_layer.facts import Fact
    from data_layer.statements import TARGET, get_metric

    fye = date(2026, 1, 31)
    mk = lambda c, v: Fact(entity=TARGET, concept=c, value=v, unit="USD", period_end=fye, form="10-K",
                           accession="0000027419-26-000016")
    facts = [mk("us-gaap:LongTermDebt", 14_398e6), mk("us-gaap:LongTermDebtAndCapitalLeaseObligations", 14_326e6),
             mk("us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent", 2_130e6)]
    total = get_metric(facts, TARGET, "total_long_term_debt", fye)
    assert total.status == "derived" and total.fact.value == 16_456e6
    assert total.fact.concept == "us-gaap:LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"
    assert get_metric(facts, TARGET, "long_term_debt_noncurrent", fye).fact.value == 14_326e6
    store = facts + [total.fact]
    wrong = check_claim(BClaim(entity=TARGET, concept_as_stated="total long-term debt", period_end=fye, value=14.4e9), store)
    assert wrong.verdict == "right_number_wrong_account" and wrong.nearest_fact.concept == "us-gaap:LongTermDebt"
    right = check_claim(BClaim(entity=TARGET, concept_as_stated="total long-term debt", period_end=fye, value=16.46e9), store)
    assert right.verdict == "correct"
    other = get_metric([f.model_copy(update={"entity": "0000000001"}) for f in facts], "0000000001", "total_long_term_debt", fye)
    assert other.status == "reported" and other.fact.value == 14_398e6
