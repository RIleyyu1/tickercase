"""Optional price-probability reference under a lognormal model.

Model: ln(S_T) ~ Normal(ln(S_0) + (mu - sigma^2 / 2) * T, sigma^2 * T)
  P(S_T >= K) = Phi((ln(S_0 / K) + (mu - sigma^2 / 2) * T) / (sigma * sqrt(T)))

S_0 is the user's confirmed reference price, T the confirmed horizon, mu the
user's drift assumption and sigma either the user's volatility or the
historical volatility of the adjusted close. This is a model output under
these assumptions. It is reported next to the case and never feeds the
verdict. Floats are used here; the deterministic claim calculations stay in
``calculations.py``.
"""

from __future__ import annotations

import math
from decimal import Decimal
from statistics import NormalDist
from typing import Optional

from .models import MarketSnapshot, ProbabilityPoint, ProbabilityReference, ValidatedClaim

LADDER_MULTIPLES = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
_N = NormalDist()

LIMITATIONS = [
    "Constant drift and volatility; real prices have jumps, fat tails and changing volatility.",
    "Historical volatility describes the past window only; it is not a forecast.",
    "Drift is your assumption. The result moves strongly with it.",
    "Not market-implied: no option prices or prediction-market prices are used.",
    "Shown as a reference next to the case. It does not change the verdict and is not a price prediction.",
]


def prob_at_or_above(spot: float, level: float, drift: float, vol: float, years: float) -> float:
    if level <= 0:
        return 1.0
    sd = vol * math.sqrt(years)
    z = (math.log(spot / level) + (drift - vol * vol / 2) * years) / sd
    return _N.cdf(z)


def quantile_price(spot: float, drift: float, vol: float, years: float, q: float) -> float:
    return spot * math.exp((drift - vol * vol / 2) * years + _N.inv_cdf(q) * vol * math.sqrt(years))


def _d(x: float) -> Decimal:
    return Decimal(str(round(x, 4)))


def probability_reference(claim: ValidatedClaim, market: Optional[MarketSnapshot]) -> Optional[ProbabilityReference]:
    """Return None when the user did not ask for it (no drift assumption)."""
    if claim.probability_drift is None:
        return None
    if claim.probability_volatility is not None:
        vol_dec, vol_source = claim.probability_volatility, "user assumption"
    elif market is not None and market.annualized_volatility is not None:
        vol_dec, vol_source = market.annualized_volatility, f"historical, {market.volatility_window} ({market.data_mode.value})"
    else:
        return ProbabilityReference(
            status="not_computable",
            drift=claim.probability_drift,
            horizon_years=claim.horizon_years,
            reason="no volatility: enter one, or run with a data mode that returns price history",
            limitations=LIMITATIONS,
        )

    spot, drift, vol, years = float(claim.reference_price), float(claim.probability_drift), float(vol_dec), float(claim.horizon_years)
    target = float(claim.target_price)
    levels = sorted({round(spot * m, 4) for m in LADDER_MULTIPLES} | {round(target, 4)})
    return ProbabilityReference(
        status="ok",
        spot=claim.reference_price,
        drift=claim.probability_drift,
        volatility=vol_dec,
        volatility_source=vol_source,
        horizon_years=claim.horizon_years,
        target_price=claim.target_price,
        target_probability=prob_at_or_above(spot, target, drift, vol, years),
        median_price=_d(quantile_price(spot, drift, vol, years, 0.5)),
        p10_price=_d(quantile_price(spot, drift, vol, years, 0.1)),
        p90_price=_d(quantile_price(spot, drift, vol, years, 0.9)),
        points=[ProbabilityPoint(price=_d(k), probability_at_or_above=prob_at_or_above(spot, k, drift, vol, years)) for k in levels],
        assumptions=[
            f"S_0 = confirmed reference price {claim.reference_price} on {claim.reference_price_date}",
            f"drift mu = {claim.probability_drift} per year (user assumption)",
            f"volatility sigma = {vol_dec} per year ({vol_source})",
            f"T = {claim.horizon_years} years (confirmed horizon)",
        ],
        limitations=LIMITATIONS,
    )
