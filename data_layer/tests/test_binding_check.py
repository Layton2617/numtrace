"""The three walk-throughs of the proposal (page 1) as unit tests, plus real-data variants."""
from datetime import date

import pytest

from data_layer.binding_check import Claim, check_claim, half_unit_of_last_digit, values_match
from data_layer.facts import Fact
from data_layer.nqdc import CompanyNQDCRecord, FundingAsset
from data_layer.statements import load_companyfacts
from data_layer.thirteen_f import HoldingsStore, load_full_submission, position_facts

FYE = date(2023, 12, 31)
CO = "0009999999"



def test_rounding_tolerance():
    assert half_unit_of_last_digit(4.12e9) == 5e6
    assert half_unit_of_last_digit(6.13) == pytest.approx(0.005)
    assert half_unit_of_last_digit(1e9) == 5e7
    assert values_match(4.12e9, 4_123_000_000)
    assert not values_match(4.12e9, 4_130_000_000)
    assert values_match(1.2e6, 1_234_567)
    assert not values_match(1_198_000_000, 1_199_000_000)
    assert values_match(1_198_000_000, 1_199_000_000, rel_tol=0.001)



@pytest.fixture
def debt_note():
    """Illustrative numbers of walk-through 1 (synthetic, in dollars)."""
    mk = lambda c, v: Fact(entity=CO, concept=c, value=v, unit="USD", period_end=FYE,
                           form="10-K", accession="0009999999-24-000001")
    return [mk("us-gaap:LongTermDebtNoncurrent", 4_120e6), mk("us-gaap:LongTermDebtCurrent", 750e6),
            mk("us-gaap:LongTermDebt", 4_870e6)]


def test_a_noncurrent_line_claimed_as_total(debt_note):
    claim = Claim(entity=CO, concept_as_stated="total long-term debt", period_end=FYE,
                  value=4.12e9)
    r = check_claim(claim, debt_note)
    assert r.value_ok is True
    assert r.binding_ok is False
    assert r.verdict == "right_number_wrong_account"
    assert r.failed_dimensions == ["concept"]
    assert r.nearest_fact.concept == "us-gaap:LongTermDebtNoncurrent"
    assert r.expected_fact.concept == "us-gaap:LongTermDebt" and r.expected_fact.value == 4_870e6


def test_a_correct_and_wrong_value(debt_note):
    ok = check_claim(Claim(entity=CO, concept_as_stated="total long-term debt", period_end=FYE,
                           value=4.87e9), debt_note)
    assert ok.verdict == "correct" and ok.value_ok and ok.binding_ok
    bad = check_claim(Claim(entity=CO, concept_as_stated="total long-term debt", period_end=FYE,
                            value=5.2e9), debt_note)
    assert bad.verdict == "wrong_value" and bad.value_ok is False and bad.binding_ok is None
    assert bad.nearest_fact.value == 4_870e6


def test_a_real_apple_fy2023(fx):
    facts = load_companyfacts(fx / "companyfacts" / "CIK0000320193_subset.json")
    claim = Claim(entity="0000320193", concept_as_stated="total long-term debt",
                  period_end=date(2023, 9, 30), value=95.281e9)
    r = check_claim(claim, facts)
    assert r.verdict == "right_number_wrong_account"
    assert r.nearest_fact.concept == "us-gaap:LongTermDebtNoncurrent"
    assert r.expected_fact.value == 105_103_000_000
    r2 = check_claim(claim.model_copy(update={"value": 110.087e9}), facts)
    assert r2.value_ok and "period" in r2.failed_dimensions and "concept" not in r2.failed_dimensions


def test_a_scale_slip(debt_note):
    r = check_claim(Claim(entity=CO, concept_as_stated="long-term debt, net of current portion",
                          period_end=FYE, value=4120), debt_note)
    assert r.value_ok and r.failed_dimensions == ["unit"]



@pytest.fixture
def store(fx):
    d = fx / "13f" / "synthetic"
    return HoldingsStore.from_filings([load_full_submission(d / "A_2022Q3_13F-HR.txt"),
                                       load_full_submission(d / "A_2022Q4_13F-HR.txt")])


A, EXAMPLE = "0009000001", "99999A101"


def test_b_q3_thousands_read_as_dollars(store):
    facts = position_facts(store, A, "2022-09-30") + position_facts(store, A, "2022-12-31")
    q3 = Claim(entity=A, concept_as_stated="position value", period_end=date(2022, 9, 30),
               value=1_234_567, dimensions={"cusip": EXAMPLE})
    r = check_claim(q3, facts)
    assert r.value_ok is True and r.binding_ok is False
    assert r.failed_dimensions == ["unit"]
    assert r.nearest_reading == (1_234_567, "USD_thousands")
    assert r.expected_fact.value == 1_234_567_000
    rounded = check_claim(q3.model_copy(update={"value": 1.2e6}), facts)
    assert rounded.verdict == "right_number_wrong_account" and rounded.failed_dimensions == ["unit"]

    q4 = Claim(entity=A, concept_as_stated="position value", period_end=date(2022, 12, 31),
               value=1_198_000_000, dimensions={"cusip": EXAMPLE})
    assert check_claim(q4, facts).verdict == "correct"


def test_b_restated_quarter_is_the_truth(fx):
    d = fx / "13f" / "synthetic"
    s = HoldingsStore.from_filings([load_full_submission(d / "A_2022Q4_13F-HR.txt"),
                                    load_full_submission(d / "A_2022Q4_13F-HRA_restatement.txt")])
    facts = position_facts(s, A, "2022-12-31")
    stale = Claim(entity=A, concept_as_stated="position value", period_end=date(2022, 12, 31),
                  value=1_198_000_000, dimensions={"cusip": EXAMPLE})
    assert check_claim(stale, facts).verdict == "wrong_value"
    double = stale.model_copy(update={"value": 2_397_000_000})
    assert check_claim(double, facts).verdict == "wrong_value"



@pytest.fixture
def nqdc_facts():
    rec = CompanyNQDCRecord(
        entity=CO, fiscal_year_end=FYE, plan_name="Deferred Compensation Plan",
        plan_liability=61_200_000,
        funding_assets=[FundingAsset(vehicle="coli_cash_surrender_value", value=74_900_000,
                                     concept="us-gaap:CashSurrenderValueOfLifeInsurance",
                                     in_rabbi_trust=True)])
    ceo = Fact(entity=CO, concept="nqdc:aggregate_balance", value=8_400_000, unit="USD",
               period_end=FYE, dimensions={"executive": "CEO"})
    return rec.to_facts() + [ceo]


def test_c_coli_claimed_as_plan_liability(nqdc_facts):
    claim = Claim(entity=CO, concept_as_stated="deferred compensation obligation",
                  period_end=FYE, value=74.9e6)
    r = check_claim(claim, nqdc_facts)
    assert r.value_ok is True and r.binding_ok is False
    assert r.verdict == "right_number_wrong_account"
    assert r.nearest_fact.concept == "us-gaap:CashSurrenderValueOfLifeInsurance"
    assert r.expected_fact.value == 61_200_000


def test_c_ceo_balance_claimed_as_plan_liability(nqdc_facts):
    r = check_claim(Claim(entity=CO, concept_as_stated="plan liability", period_end=FYE,
                          value=8.4e6), nqdc_facts)
    assert r.verdict == "right_number_wrong_account"
    assert r.nearest_fact.concept == "nqdc:aggregate_balance"


def test_no_ground_truth():
    r = check_claim(Claim(entity=CO, concept_as_stated="cash and cash equivalents",
                          period_end=FYE, value=1e6), [])
    assert r.verdict == "no_ground_truth"
