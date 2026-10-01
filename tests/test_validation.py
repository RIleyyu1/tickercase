from decimal import Decimal
from datetime import datetime, timezone

import pytest

from tickercase.models import ClaimDraft
from tickercase.validation import ConfirmationError, confirm, confirmation_state, fingerprint, validate_draft

from conftest import FIXED_TODAY, ps_draft


def issues(draft):
    return {(i.field, i.code) for i in validate_draft(draft, today=FIXED_TODAY).issues}


def test_valid_draft():
    r = validate_draft(ps_draft(), today=FIXED_TODAY)
    assert r.ok and r.claim.ticker == "SYNT"


def test_missing_core_fields_are_blocking():
    r = validate_draft(ClaimDraft(ticker="SYNT"), today=FIXED_TODAY)
    assert not r.ok
    blocking = {m.field for m in r.missing_fields if m.blocking}
    assert {"target_price", "reference_price", "horizon_years", "target_assumed_shares", "valuation_multiple", "valuation_method", "currency", "claim_text", "reference_price_date"} <= blocking


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("target_price", "0", "must_be_positive"),
        ("reference_price", "-5", "must_be_positive"),
        ("target_price", "NaN", "nan_not_allowed"),
        ("target_price", "Infinity", "infinity_not_allowed"),
        ("reference_price", "-inf", "infinity_not_allowed"),
        ("horizon_years", "0", "must_be_positive"),
        ("valuation_multiple", "-3", "must_be_positive"),
        ("target_assumed_shares", "0", "must_be_positive"),
        ("target_price", "abc", "not_a_number"),
        ("target_price", float("nan"), "nan_not_allowed"),
        ("target_price", float("inf"), "infinity_not_allowed"),
        ("base_annual_metric", "nan", "nan_not_allowed"),
        ("reference_price_date", "2026-13-01", "invalid_date"),
        ("reference_price_date", "2027-01-01", "date_in_future"),
        ("currency", "US", "invalid_currency"),
        ("valuation_method", "ev_to_ebitda", "unsupported_method"),
        ("ticker", "not a ticker", "invalid_ticker"),
    ],
)
def test_invalid_values(field, value, code):
    assert (field, code) in issues(ps_draft(**{field: value}))


def test_currency_mismatch_rejected():
    assert ("base_metric_currency", "currency_mismatch") in issues(ps_draft(base_metric_currency="EUR"))


def test_negative_base_metric_is_valid_input():
    assert validate_draft(ps_draft(base_annual_metric="-5000000"), today=FIXED_TODAY).ok


def test_thousands_separators_accepted():
    r = validate_draft(ps_draft(target_assumed_shares="100,000,000"), today=FIXED_TODAY)
    assert r.ok and str(r.claim.target_assumed_shares) == "100000000"


def test_confirm_requires_valid_input():
    with pytest.raises(ConfirmationError):
        confirm(ps_draft(target_price="NaN"), today=FIXED_TODAY)


def test_confirmation_invalidated_by_any_edit():
    draft = ps_draft()
    c = confirm(draft, today=FIXED_TODAY, now=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert confirmation_state(draft, c) == "confirmed"
    assert confirmation_state(ps_draft(target_price="101"), c) == "stale"
    assert confirmation_state(ps_draft(target_assumed_shares="100000001"), c) == "stale"
    assert confirmation_state(draft, None) == "unconfirmed"


def test_fingerprint_ignores_whitespace_and_ticker_case():
    assert fingerprint(ps_draft(ticker=" synt ")) == fingerprint(ps_draft(ticker="SYNT"))


def test_target_shares_derived_from_yearly_change():
    r = validate_draft(ps_draft(target_assumed_shares=None, current_shares="95000000", share_change_rate="0.01", share_change_mode="trend"),
                       today=FIXED_TODAY)
    assert r.ok and r.claim.target_shares_derived
    assert r.claim.target_assumed_shares == Decimal("99845955")  # 95M x 1.01^5 = 99,845,954.76, whole shares
    from tickercase.calculations import calculate
    names = [c.name for c in calculate(r.claim)]
    assert names.index("target_assumed_shares") == names.index("target_market_cap") - 1


def test_absolute_mode_uses_the_entered_count_and_rate_needs_current_shares():
    r = validate_draft(ps_draft(target_assumed_shares="100000000", share_change_rate="0.05", share_change_mode="absolute"), today=FIXED_TODAY)
    assert r.ok and not r.claim.target_shares_derived and r.claim.target_assumed_shares == Decimal("100000000")
    r = validate_draft(ps_draft(target_assumed_shares=None, current_shares=None, share_change_rate="0.01"), today=FIXED_TODAY)
    assert not r.ok and any(m.field == "current_shares" and m.blocking for m in r.missing_fields)


def test_extreme_share_change_warns_before_confirmation():
    # the screenshot case: 3.95B current shares, 100M target shares left over from an example
    r = validate_draft(ps_draft(current_shares="3949547394", target_assumed_shares="100000000"), today=FIXED_TODAY)
    assert r.ok and any("check that the share inputs belong to this company" in w for w in r.warnings)
    assert any("股份数每年变化" in w for w in r.warnings_zh)
    issues = {(i.field, i.code) for i in validate_draft(ps_draft(share_change_rate="2"), today=FIXED_TODAY).issues}
    assert ("share_change_rate", "out_of_range") in issues
