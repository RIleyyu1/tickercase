import math
from decimal import Decimal

from tickercase.probability import prob_at_or_above, probability_reference, quantile_price
from tickercase.validation import validate_draft

from conftest import FIXED_TODAY, ps_draft
from test_analysis import market


def claim(**kw):
    return validate_draft(ps_draft(**kw), today=FIXED_TODAY).claim


def test_closed_form_values():
    # zero drift adjustment at mu = sigma^2 / 2: P(S_T >= S_0) is exactly 0.5
    assert math.isclose(prob_at_or_above(50, 50, 0.045, 0.3, 5), 0.5, abs_tol=1e-12)
    # median equals S_0 * exp((mu - sigma^2/2) T)
    assert math.isclose(quantile_price(50, 0.07, 0.3, 5, 0.5), 50 * math.exp((0.07 - 0.045) * 5), rel_tol=1e-12)
    # monotone in the price level
    assert prob_at_or_above(50, 40, 0.07, 0.3, 5) > prob_at_or_above(50, 100, 0.07, 0.3, 5)


def test_not_requested_without_drift():
    assert probability_reference(claim(), market()) is None


def test_uses_historical_volatility_and_includes_target():
    ref = probability_reference(claim(probability_drift="0.07"), market())
    assert ref.status == "ok" and ref.volatility == Decimal("0.3") and "historical" in ref.volatility_source
    assert any(p.price == Decimal("100") for p in ref.points)
    expected = prob_at_or_above(50, 100, 0.07, 0.3, 5)
    assert math.isclose(ref.target_probability, expected, rel_tol=1e-12)
    probs = [p.probability_at_or_above for p in ref.points]
    assert probs == sorted(probs, reverse=True)


def test_user_volatility_wins_and_missing_volatility_is_reported():
    ref = probability_reference(claim(probability_drift="0.05", probability_volatility="0.5"), market())
    assert ref.volatility == Decimal("0.5") and ref.volatility_source == "user assumption"
    none = probability_reference(claim(probability_drift="0.05"), None)
    assert none.status == "not_computable" and "volatility" in none.reason


def test_validation_of_probability_inputs():
    issues = {(i.field, i.code) for i in validate_draft(ps_draft(probability_drift="2", probability_volatility="-1"), today=FIXED_TODAY).issues}
    assert ("probability_drift", "out_of_range") in issues and ("probability_volatility", "must_be_positive") in issues
    r = validate_draft(ps_draft(probability_volatility="0.3"), today=FIXED_TODAY)
    assert r.ok and any("probability_drift is empty" in w for w in r.warnings)


def test_field_sources_validated_and_part_of_fingerprint():
    from tickercase.validation import fingerprint

    bad = validate_draft(ps_draft(field_sources={"nope": "x"}), today=FIXED_TODAY)
    assert ("field_sources", "unknown_field") in {(i.field, i.code) for i in bad.issues}
    a = ps_draft(field_sources={"reference_price": "src A"})
    b = ps_draft(field_sources={"reference_price": "src B"})
    assert fingerprint(a) != fingerprint(b)
    ok = validate_draft(a, today=FIXED_TODAY)
    assert ok.claim.field_sources == {"reference_price": "src A"}
