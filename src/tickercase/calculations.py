"""Deterministic calculations. Pure functions: no I/O, no clock, no network.

Precision
---------
All arithmetic uses ``decimal.Decimal`` in a local context with 34 significant
digits (IEEE 754 decimal128 precision) and ROUND_HALF_EVEN. Division and
multiplication of the inputs are exact whenever the true result fits in 34
digits. Non-integer exponents (annualised rates) are computed as
``exp(ln(ratio) / years)``; that result is correctly rounded to 34 digits but
is not exact, so tests compare it with an absolute tolerance of 1e-12.

Values are returned unrounded as ratios (0.25 means 25%). Rounding for
display happens only in the presentation layer.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Optional

from .models import CalculationItem, ValidatedClaim, ValuationMethod

PRECISION = 34
RATE_TOLERANCE = Decimal("1e-12")


def _ctx() -> Context:
    return Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)


def _s(value: Decimal) -> str:
    return format(value, "f")


def total_return(target_price: Decimal, reference_price: Decimal) -> Decimal:
    with localcontext(_ctx()):
        return target_price / reference_price - 1


def annualized_rate(end_value: Decimal, start_value: Decimal, years: Decimal) -> Decimal:
    """(end/start) ** (1/years) - 1 for positive end and start values."""
    if start_value <= 0 or end_value <= 0:
        raise ValueError("annualized_rate requires positive start and end values")
    if years <= 0:
        raise ValueError("years must be positive")
    with localcontext(_ctx()):
        ratio = end_value / start_value
        if ratio == 1:
            return Decimal(0)
        return (ratio.ln() / years).exp() - 1


def market_cap(price: Decimal, shares: Decimal) -> Decimal:
    with localcontext(_ctx()):
        return price * shares


def required_metric(target_market_cap: Decimal, multiple: Decimal) -> Decimal:
    with localcontext(_ctx()):
        return target_market_cap / multiple


def calculate(claim: ValidatedClaim) -> list[CalculationItem]:
    """Run every calculation the confirmed inputs allow."""
    ccy = claim.currency
    items: list[CalculationItem] = []
    price_inputs = {
        "target_price": _s(claim.target_price),
        "reference_price": _s(claim.reference_price),
        "reference_price_date": claim.reference_price_date.isoformat(),
    }

    items.append(
        CalculationItem(
            name="required_return",
            status="ok",
            value=total_return(claim.target_price, claim.reference_price),
            unit="ratio",
            formula="target_price / reference_price - 1",
            inputs=price_inputs,
            assumptions=["reference price is a manual value entered by the user"],
        )
    )
    items.append(
        CalculationItem(
            name="annualized_price_return",
            status="ok",
            value=annualized_rate(claim.target_price, claim.reference_price, claim.horizon_years),
            unit="ratio per year",
            formula="(target_price / reference_price) ** (1 / horizon_years) - 1",
            inputs={**price_inputs, "horizon_years": _s(claim.horizon_years)},
            assumptions=["horizon_years is a user assumption"],
        )
    )

    if claim.target_shares_derived and claim.current_shares is not None and claim.share_change_rate is not None:
        items.append(
            CalculationItem(
                name="target_assumed_shares",
                status="ok",
                value=claim.target_assumed_shares,
                unit="shares",
                formula="current_shares * (1 + share_change_rate) ** horizon_years, rounded to whole shares",
                inputs={"current_shares": _s(claim.current_shares), "share_change_rate": _s(claim.share_change_rate),
                        "horizon_years": _s(claim.horizon_years)},
                assumptions=[f"share_change_rate is a user assumption ({claim.share_change_mode or 'rate'})"],
            )
        )

    cap = market_cap(claim.target_price, claim.target_assumed_shares)
    items.append(
        CalculationItem(
            name="target_market_cap",
            status="ok",
            value=cap,
            unit=ccy,
            formula="target_price * target_assumed_shares",
            inputs={"target_price": _s(claim.target_price), "target_assumed_shares": _s(claim.target_assumed_shares)},
            assumptions=["target_assumed_shares is the user's assumed share count at the target date, separate from the current count"],
        )
    )

    if claim.current_shares is not None:
        with localcontext(_ctx()):
            change = claim.target_assumed_shares / claim.current_shares - 1
        items.append(
            CalculationItem(
                name="implied_share_count_change",
                status="ok",
                value=change,
                unit="ratio",
                formula="target_assumed_shares / current_shares - 1",
                inputs={"target_assumed_shares": _s(claim.target_assumed_shares), "current_shares": _s(claim.current_shares)},
                assumptions=["current_shares is a manual reference value entered by the user"],
            )
        )

    method = claim.valuation_method
    metric_name = method.metric_name
    required_name = f"required_{metric_name}"
    req = required_metric(cap, claim.valuation_multiple)
    items.append(
        CalculationItem(
            name=required_name,
            status="ok",
            value=req,
            unit=f"{ccy} per year",
            formula=f"target_market_cap / {method.multiple_name}",
            inputs={"target_market_cap": _s(cap), method.multiple_name: _s(claim.valuation_multiple)},
            assumptions=[f"{method.multiple_name} at the target date is a user assumption"],
        )
    )

    items.append(_metric_cagr(claim, required_name, req))
    return items


def _metric_cagr(claim: ValidatedClaim, required_name: str, required_value: Decimal) -> CalculationItem:
    method = claim.valuation_method
    base_label = "base_annual_revenue" if method is ValuationMethod.PRICE_TO_SALES else "base_annual_net_income"
    name = "required_metric_cagr"
    formula = f"({required_name} / {base_label}) ** (1 / horizon_years) - 1"
    inputs = {required_name: _s(required_value), "horizon_years": _s(claim.horizon_years)}
    assumptions = [f"{base_label} is a manual reference value entered by the user"]
    if claim.base_metric_period:
        assumptions.append(f"base period: {claim.base_metric_period}")

    reason: Optional[str] = None
    if claim.base_annual_metric is None:
        reason = f"{base_label} not provided"
    elif claim.base_metric_currency is None:
        reason = "base metric currency not provided"
    elif claim.base_metric_currency != claim.currency:
        reason = f"base metric currency {claim.base_metric_currency} differs from {claim.currency}"
    elif claim.base_annual_metric <= 0:
        reason = (
            f"{base_label} is {_s(claim.base_annual_metric)}; a compound growth rate from a zero or "
            "negative base is undefined"
        )
    if claim.base_annual_metric is not None:
        inputs[base_label] = _s(claim.base_annual_metric)

    if reason is not None:
        return CalculationItem(
            name=name, status="not_computable", value=None, unit="ratio per year",
            formula=formula, inputs=inputs, assumptions=assumptions, reason=reason,
        )
    return CalculationItem(
        name=name,
        status="ok",
        value=annualized_rate(required_value, claim.base_annual_metric, claim.horizon_years),
        unit="ratio per year",
        formula=formula,
        inputs=inputs,
        assumptions=assumptions,
    )
