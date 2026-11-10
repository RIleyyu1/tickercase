"""How probabilities are written on every page: one rule, so the same number never reads two ways.

A small chance never reads as 0% ("<0.1%"), a near-certain one never as 100% (">99.9%"), and only an exact 0 or 1
(0 of 71 windows, 69 of 69) is written as 0.0% or 100.0%. Below 1% one decimal is kept, so 0.6% does not round to 1%.
"""

from __future__ import annotations

from typing import Optional


def prob(p: Optional[float], digits: int = 1) -> str:
    """'17.9%', '0.6%', '<0.1%', '>99.9%'; whole percents with digits=0 ('17%')."""
    if p is None:
        return "—"
    if p <= 0:
        return f"{0:.{digits}f}%"
    if p >= 1:
        return f"{100:.{digits}f}%"
    if p < 0.001:
        return "<0.1%"
    if p > 0.999 or round(p * 100, digits) >= 100:
        return ">99.9%"
    return f"{p * 100:.{max(digits, 1) if p < 0.01 else digits}f}%"


def prob_num(p: float) -> str:
    """The same in percent points without the sign, for compact lists: 'M1 0.6', 'M1 <0.1'."""
    return prob(p, 1).removesuffix("%")


def prob_ends(lo: float, hi: float, digits: int = 0) -> tuple[str, str]:
    """The two ends of a range, with one decimal when whole percents would print the same number twice."""
    if digits == 0 and round(lo * 100) == round(hi * 100) and round(lo * 1000) != round(hi * 1000):
        digits = 1
    return prob(lo, digits), prob(hi, digits)


def prob_range(lo: float, hi: float, digits: int = 0) -> str:
    """'17%–18%', '9.9%–10.4%', '<0.1%–0.6%', or one value when both ends read the same ('<0.1%')."""
    if 0 < hi < 0.001:  # the whole range is below a tenth of a percent, even when its lower end is an exact 0
        return "<0.1%"
    a, b = prob_ends(lo, hi, digits)
    return a if a == b else f"{a}–{b}"


def pct(value, digits: int = 1, signed: bool = False) -> str:
    """A rate or change in percent, '12.34%' / '+1.2%' with thousands separators; a value that rounds to zero is '0.0%',
    never '-0.0%' (a required growth of -0.0004 printed as '-0.0%' in the sensitivity table)."""
    if value is None:
        return "—"
    x = value * 100
    if round(float(x), digits) == 0:
        return f"{0:.{digits}f}%"
    return f"{x:{'+' if signed else ''},.{digits}f}%"


def about(text: str) -> str:
    """'≈ 18%', but '<0.1%' and '>99.9%' already say they are bounds."""
    return text if text.startswith(("<", ">")) else f"≈ {text}"
