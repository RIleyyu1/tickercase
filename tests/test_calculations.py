from decimal import Decimal

import pytest

from tickercase.calculations import RATE_TOLERANCE, annualized_rate, calculate
from tickercase.validation import validate_draft

from conftest import FIXED_TODAY, pe_draft, ps_draft


def calcs(draft):
    result = validate_draft(draft, today=FIXED_TODAY)
    assert result.ok, result.issues
    return {c.name: c for c in calculate(result.claim)}


def close(a: Decimal, b: str) -> bool:
    return abs(a - Decimal(b)) < RATE_TOLERANCE


def test_ps_acceptance_case():
    c = calcs(ps_draft())
    assert c["required_return"].value == Decimal("1")
    assert c["target_market_cap"].value == Decimal("10000000000")
    assert c["required_annual_revenue"].value == Decimal("400000000")
    # acceptance value "about 0.148698355" (9 decimals); exact value is 2 ** 0.2 - 1
    assert abs(c["annualized_price_return"].value - Decimal("0.148698355")) < Decimal("5e-10")
    assert close(c["annualized_price_return"].value, "0.148698354997035")
    assert close(c["required_metric_cagr"].value, "0.148698354997035")
    assert c["required_metric_cagr"].status == "ok"
    assert c["target_market_cap"].unit == "USD"
    assert c["required_annual_revenue"].formula == "target_market_cap / assumed_price_to_sales"
    assert c["target_market_cap"].inputs == {"target_price": "100", "target_assumed_shares": "100000000"}


def test_pe_acceptance_case():
    c = calcs(pe_draft())
    assert c["required_annual_net_income"].value == Decimal("500000000")
    assert "required_annual_revenue" not in c
    assert close(c["required_metric_cagr"].value, "0.148698354997035")


def test_results_follow_inputs_not_hardcoded():
    c = calcs(ps_draft(target_price="150"))
    assert c["required_return"].value == Decimal("2")
    assert c["target_market_cap"].value == Decimal("15000000000")
    assert c["required_annual_revenue"].value == Decimal("600000000")
    c = calcs(ps_draft(target_assumed_shares="120000000"))
    assert c["target_market_cap"].value == Decimal("12000000000")
    assert c["required_annual_revenue"].value == Decimal("480000000")
    assert c["required_return"].value == Decimal("1")  # price inputs unchanged
    c = calcs(ps_draft(horizon_years="10"))
    assert close(c["annualized_price_return"].value, "0.07177346253629313")  # 2 ** 0.1 - 1


def test_ratios_are_ratios_not_percent():
    c = calcs(ps_draft(target_price="75"))
    assert c["required_return"].value == Decimal("0.5")
    assert c["required_return"].unit == "ratio"


def test_negative_or_zero_base_net_income_is_not_computable_but_rest_survives():
    for base in ("-10000000", "0"):
        c = calcs(pe_draft(base_annual_metric=base))
        cagr = c["required_metric_cagr"]
        assert cagr.status == "not_computable" and cagr.value is None
        assert "undefined" in cagr.reason
        assert c["required_annual_net_income"].value == Decimal("500000000")


def test_missing_base_metric_only_affects_growth_rate():
    c = calcs(ps_draft(base_annual_metric=None, base_metric_currency=None))
    assert c["required_metric_cagr"].status == "not_computable"
    assert c["required_metric_cagr"].reason == "base_annual_revenue not provided"
    assert c["required_annual_revenue"].value == Decimal("400000000")


def test_missing_base_currency_blocks_only_growth_rate():
    c = calcs(ps_draft(base_metric_currency=None))
    assert c["required_metric_cagr"].reason == "base metric currency not provided"


def test_current_vs_target_shares_are_separate():
    c = calcs(ps_draft(current_shares="80000000"))
    assert c["implied_share_count_change"].value == Decimal("0.25")
    assert c["target_market_cap"].value == Decimal("10000000000")  # uses target shares only


def test_fractional_horizon_and_decimal_precision():
    c = calcs(ps_draft(horizon_years="2.5", target_price="0.3", reference_price="0.1"))
    assert c["required_return"].value == Decimal("2")  # exact with Decimal, 0.3/0.1 != 3 in float
    expected = Decimal(3) ** (Decimal(1) / Decimal("2.5")) - 1
    assert abs(c["annualized_price_return"].value - expected) < RATE_TOLERANCE


def test_annualized_rate_guards():
    with pytest.raises(ValueError):
        annualized_rate(Decimal(1), Decimal(0), Decimal(1))
    with pytest.raises(ValueError):
        annualized_rate(Decimal(1), Decimal(1), Decimal(0))
    assert annualized_rate(Decimal(5), Decimal(5), Decimal(3)) == 0
