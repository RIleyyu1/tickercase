"""Base rates from SEC XBRL frames: how often did similar-sized US companies grow as fast as the claim needs?

Endpoint: https://data.sec.gov/api/xbrl/frames/us-gaap/<concept>/USD/CY<year>.json
Each frame lists one annual value per filer for a calendar year. Revenue
concepts are merged per company in a fixed order. A company counts when it
reported in both the start and end year, its start value was within a size
band around the claim's company (0.5x-2x, widened to 0.25x-4x when fewer than
30 companies qualify), and both values are positive.

Survivorship: companies acquired, delisted or no longer filing are missing,
so the rate is biased upwards. The note on the result says so.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from statistics import median
from typing import Any, Mapping, Optional, Sequence

from ..calculations import _ctx, annualized_rate
from ..http_client import JsonFetcher
from ..models import BaseRate, DataMode
from .base import Provider, ProviderError

FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/CY{year}.json"
REVENUE_FRAME_CONCEPTS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")
NET_INCOME_FRAME_CONCEPTS = ("NetIncomeLoss",)
BANDS = ((Decimal("0.5"), Decimal("2")), (Decimal("0.25"), Decimal("4")))
MIN_COMPANIES = 30


def _frame_values(payload: Any) -> dict[int, tuple[Decimal, str]]:
    out: dict[int, tuple[Decimal, str]] = {}
    rows = payload.get("data") if isinstance(payload, Mapping) else None
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        cik, val, name = row.get("cik"), row.get("val"), row.get("entityName")
        if isinstance(cik, bool) or not isinstance(cik, int) or isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        out[cik] = (Decimal(str(val)), name if isinstance(name, str) else str(cik))
    return out


class SecFramesBaseRateProvider(Provider):
    provider_id = "sec_xbrl_frames"
    display_name = "SEC XBRL frames (all filers)"
    capabilities = ("growth_base_rate",)
    limitations = ("survivors only", "calendar-year frames; fiscal years ending far from December are aligned by SEC")

    def __init__(self, client: JsonFetcher):
        self.client = client

    def _merged(self, concepts: Sequence[str], year: int) -> tuple[dict[int, tuple[Decimal, str]], list[str], set]:
        merged: dict[int, tuple[Decimal, str]] = {}
        urls, modes = [], set()
        for concept in concepts:
            url = FRAMES_URL.format(concept=concept, year=year)
            fetched = self.client.get_json(url)
            urls.append(url)
            modes.add(fetched.data_mode)
            for cik, value in _frame_values(fetched.payload).items():
                merged.setdefault(cik, value)  # earlier concept wins
        return merged, urls, modes

    def base_rate(self, *, metric: str, base_value: Decimal, required_cagr: Decimal, end_year: int, years: int,
                  exclude_cik: Optional[int] = None) -> BaseRate:
        if years < 1:
            raise ProviderError("horizon_too_short", "base rate needs a horizon of at least one year")
        if base_value <= 0:
            raise ProviderError("nonpositive_base", "base rate needs a positive starting value")
        concepts = REVENUE_FRAME_CONCEPTS if metric == "annual_revenue" else NET_INCOME_FRAME_CONCEPTS
        start_year = end_year - years
        start, urls_a, modes_a = self._merged(concepts, start_year)
        end, urls_b, modes_b = self._merged(concepts, end_year)
        growth: list[tuple[Decimal, str, Decimal, Decimal]] = []
        band_used = BANDS[0]
        for low_f, high_f in BANDS:
            low, high = base_value * low_f, base_value * high_f
            growth = []
            for cik, (v0, name) in start.items():
                if cik == exclude_cik or cik not in end or not low <= v0 <= high:
                    continue
                v1 = end[cik][0]
                if v0 > 0 and v1 > 0:
                    growth.append((annualized_rate(v1, v0, Decimal(years)), name, v0, v1))
            band_used = (low_f, high_f)
            if len(growth) >= MIN_COMPANIES:
                break
        modes = modes_a | modes_b
        mode = DataMode.SYNTHETIC if DataMode.SYNTHETIC in modes else DataMode.REPLAY if DataMode.REPLAY in modes else DataMode.LIVE
        low, high = base_value * band_used[0], base_value * band_used[1]
        if not growth:
            return BaseRate(metric=metric, start_year=start_year, end_year=end_year, size_low=low, size_high=high, companies=0,
                            achieved=0, required_cagr=required_cagr, concepts=list(concepts), source_urls=urls_a + urls_b, data_mode=mode)
        rates = sorted(g[0] for g in growth)
        achieved = [g for g in growth if g[0] >= required_cagr]
        below = sum(1 for r in rates if r < required_cagr)
        with localcontext(_ctx()):
            p90 = rates[min(len(rates) - 1, int(len(rates) * 0.9))]
        examples = [
            {"name": name, "start": format(v0, "f"), "end": format(v1, "f"), "cagr": format(r.quantize(Decimal("0.0001")), "f")}
            for r, name, v0, v1 in sorted(achieved or growth, key=lambda g: -g[0])[:5]
        ]
        return BaseRate(
            metric=metric, start_year=start_year, end_year=end_year, size_low=low, size_high=high,
            companies=len(growth), achieved=len(achieved), rate=len(achieved) / len(growth), required_cagr=required_cagr,
            percentile_of_required=below / len(rates), median_cagr=Decimal(median(rates)).quantize(Decimal("0.0001")),
            p90_cagr=p90.quantize(Decimal("0.0001")), examples=examples, concepts=list(concepts), source_urls=urls_a + urls_b,
            data_mode=mode,
        )
