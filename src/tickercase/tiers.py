"""The five words for a probability range, used by every page (simple view, console, report text, narrative facts).

One rule, so the same result never reads "likely" on one page and "possible" on another. The word follows where the
whole range sits, not its middle: a range that crosses even odds, or lies on both sides of a band, is "uncertain".

    whole range at or below 10 %         very unlikely        几乎不可能
    whole range below 40 %               unlikely             不太可能
    anything else                        possible, uncertain  有可能，但说不准
    whole range above 60 %               likely               比较可能
    whole range at or above 85 %         very likely          很可能
"""

from __future__ import annotations

from typing import Optional

# key -> (English, Chinese)
TIERS: dict[str, tuple[str, str]] = {
    "almost_no": ("Very unlikely", "几乎不可能"),
    "unlikely": ("Unlikely", "不太可能"),
    "uncertain": ("Possible, but uncertain", "有可能，但说不准"),
    "likely": ("Likely", "比较可能"),
    "very_likely": ("Very likely", "很可能"),
    "unknown": ("Not enough data", "数据不足"),
}
ORDER = ("almost_no", "unlikely", "uncertain", "likely", "very_likely")
# the same bands for one probability on a ruler: (key, English, Chinese)
BANDS = (("almost_no", "Very unlikely ≤10%", "几乎不可能 ≤10%"), ("unlikely", "Unlikely 10–40%", "不太可能 10–40%"),
         ("uncertain", "Uncertain 40–60%", "说不准 40–60%"), ("likely", "Likely 60–85%", "比较可能 60–85%"),
         ("very_likely", "Very likely ≥85%", "很可能 ≥85%"))
EDGES = (0.10, 0.40, 0.60, 0.85)


def tier(low: Optional[float], high: Optional[float]) -> str:
    if low is None or high is None:
        return "unknown"
    if high <= 0.10:
        return "almost_no"
    if low >= 0.85:
        return "very_likely"
    if high < 0.40:
        return "unlikely"
    if low > 0.60:
        return "likely"
    return "uncertain"


def words(low: Optional[float], high: Optional[float]) -> tuple[str, str]:
    return TIERS[tier(low, high)]
