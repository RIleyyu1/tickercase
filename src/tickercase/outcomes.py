"""What the main number means for someone who holds the stock to the deadline (v0.23.0): three outcomes that add up to 1.

- ``reach``: the claim comes true (the price gets to the target on some day, or closes at or above it on the last day).
- ``between``: it does not, and the price on the last day is at or above today's price.
- ``below``: it does not, and the price on the last day is below today's price.

Each price method of the index splits its own 100 % into the three, and the splits are joined with the weights of the
index:

- M1 and M2 (the lognormal formula with implied or historical volatility): closed forms. For a claim that counts any
  day, a path that reached the target and still ended below today's price belongs to ``reach``; its share comes from
  the reflection principle, P(max >= b, X_T <= 0) = exp(2 nu b / sigma^2) * Phi((-2b - nu T) / (sigma sqrt(T))),
  with X = ln(S / S_0), b = ln(target / S_0) and nu = rate - sigma^2 / 2.
- M4 (the stock's own past): the windows are counted (``PriceBaseRate.down_windows`` and ``down_untouched``).

The rate of similar companies (M3) says nothing about where the price ends, so it is left out and the other weights
are scaled up. ``reach`` is then set to the main number of the page (which may hold M3 and the adjustment for events),
and the rest is divided between the other two in the proportion the methods gave. With no M3 and no events the result
is the weighted split itself.

It is a model output under the assumptions of the methods, shown beside the answer. It is not a forecast of a return
and not advice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Optional

from .models import CaseResult
from .oracle import p_end_above, p_touch

_N = NormalDist()
VOL_INPUT = {"M1": "implied_volatility", "M2": "historical_volatility"}


@dataclass(frozen=True)
class Outcomes:
    reach: float
    between: float
    below: float
    touch: bool                 # the claim counts any day before the deadline
    methods: tuple[str, ...]    # the methods the split comes from


def lognormal_split(spot: float, target: float, vol: float, years: float, rate: float, touch: bool) -> tuple[float, float, float]:
    """(reach, between, below) under the lognormal model of M1 and M2."""
    down = 1.0 - p_end_above(spot, spot, vol, years, rate)
    if not touch:
        reach = p_end_above(spot, target, vol, years, rate)
        return reach, max(0.0, 1.0 - reach - down), down
    reach = p_touch(spot, target, vol, years, rate)
    nu = rate - vol * vol / 2
    b = math.log(target / spot)
    sd = vol * math.sqrt(years)
    reached_then_down = math.exp(2 * nu * b / (vol * vol)) * _N.cdf((-2 * b - nu * years) / sd)
    below = max(0.0, down - reached_then_down)
    return reach, max(0.0, 1.0 - reach - below), below


def _method_split(result: CaseResult, mid: str, touch: bool) -> Optional[tuple[float, float, float]]:
    o = result.oracle
    m = next((x for x in o.methods if x.id == mid and x.status == "ok"), None)
    if m is None:
        return None
    if mid in VOL_INPUT:
        try:
            spot, target, years = float(m.inputs["spot"]), float(m.inputs["target"]), float(m.inputs["years"])
            vol, rate = float(m.inputs[VOL_INPUT[mid]]), float(m.inputs["rate"])
        except (KeyError, ValueError):
            return None
        if not (spot > 0 and target > spot and vol > 0 and years > 0):
            return None
        return lognormal_split(spot, target, vol, years, rate, touch)
    if mid == "M4":
        pr = result.price_base_rate
        if pr is None or not pr.windows:
            return None
        if touch:
            if pr.touch_hits is None or pr.down_untouched is None:
                return None
            reach, below = pr.touch_hits / pr.windows, pr.down_untouched / pr.windows
        else:
            if pr.down_windows is None:
                return None
            reach, below = pr.hits / pr.windows, pr.down_windows / pr.windows
        return reach, max(0.0, 1.0 - reach - below), below
    return None


def three(result: CaseResult) -> Optional[Outcomes]:
    """The three outcomes of a case, or None when they cannot be worked out (no main number, a target at or below
    today's price, a case stored before v0.23.0 whose history holds no down windows)."""
    o = result.oracle
    if o is None or o.low is None or o.basis != "index" or not o.weights:
        return None
    pr = result.price_base_rate
    if pr is not None and float(pr.required_return) <= 0:
        return None
    touch = o.condition == "touch"
    splits: dict[str, tuple[float, float, float]] = {}
    for mid in o.weights:
        if mid == "M3":
            continue
        split = _method_split(result, mid, touch)
        if split is None:      # a method of the index that cannot be split: no card, never a split from part of the index
            return None
        splits[mid] = split
    total = sum(o.weights[mid] for mid in splits)
    if not splits or total <= 0:
        return None
    between = sum(o.weights[mid] * s[1] for mid, s in splits.items()) / total
    below = sum(o.weights[mid] * s[2] for mid, s in splits.items()) / total
    if between + below <= 0:
        return Outcomes(o.low, 1.0 - o.low, 0.0, touch, tuple(splits))
    rest = max(0.0, 1.0 - o.low)
    return Outcomes(o.low, rest * between / (between + below), rest * below / (between + below), touch, tuple(splits))
