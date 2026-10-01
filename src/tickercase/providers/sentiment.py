"""Market-wide and event signals: Polymarket stock contracts and the CNN Fear & Greed index.

Polymarket: https://gamma-api.polymarket.com/public-search?q=<query>  (no key)
CNN:        https://production.dataviz.cnn.io/index/fearandgreed/graphdata (browser headers needed)
Both are context signals with short horizons; they are shown, not used in the probability range.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, Optional
from urllib.parse import quote

from ..http_client import JsonFetcher
from ..models import PredictionMarket, PredictionMarkets, SentimentSnapshot
from .base import Provider, ProviderError, ProviderParseError

POLYMARKET_URL = "https://gamma-api.polymarket.com/public-search?q={q}"
POLYMARKET_EVENT = "https://polymarket.com/event/{slug}"
FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


def _dt(text: Any) -> Optional[datetime]:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _yes_price(market: Mapping[str, Any]) -> Optional[float]:
    try:
        outcomes = market.get("outcomes")
        prices = market.get("outcomePrices")
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
        prices = json.loads(prices) if isinstance(prices, str) else prices
        idx = [str(o).lower() for o in outcomes].index("yes") if outcomes else 0
        return float(prices[idx])
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        return None


class PolymarketProvider(Provider):
    provider_id = "polymarket"
    display_name = "Polymarket"
    capabilities = ("event_contracts",)
    limitations = ("stock contracts are usually monthly or weekly, far shorter than multi-year claims",)

    def __init__(self, client: JsonFetcher):
        self.client = client

    def search(self, ticker: str, *, now: datetime, limit: int = 8) -> PredictionMarkets:
        url = POLYMARKET_URL.format(q=quote(ticker))
        fetched = self.client.get_json(url)
        events = fetched.payload.get("events") if isinstance(fetched.payload, Mapping) else None
        if not isinstance(events, list):
            raise ProviderParseError("parse_error", "public-search: expected an events array", url=url)
        found: list[PredictionMarket] = []
        tag = f"({ticker.upper()})"
        for event in events:
            if not isinstance(event, Mapping) or tag not in str(event.get("title", "")).upper():
                continue
            end = _dt(event.get("endDate"))
            if end is not None and end < now:
                continue
            for m in event.get("markets") or []:
                if not isinstance(m, Mapping) or m.get("closed"):
                    continue
                found.append(PredictionMarket(
                    question=str(m.get("question") or event.get("title")), probability_yes=_yes_price(m),
                    end_date=_dt(m.get("endDate")) or end,
                    volume=float(m["volume"]) if isinstance(m.get("volume"), (int, float, str)) and str(m.get("volume")).replace(".", "", 1).isdigit() else None,
                    url=POLYMARKET_EVENT.format(slug=event.get("slug", "")),
                ))
        found.sort(key=lambda x: (x.end_date or now, -(x.volume or 0)))
        return PredictionMarkets(query=ticker, markets=found[:limit], source_url=url, retrieved_at=fetched.retrieved_at,
                                 source_captured_at=fetched.source_captured_at, data_mode=fetched.data_mode)


class FearGreedProvider(Provider):
    provider_id = "cnn_fear_greed"
    display_name = "CNN Fear & Greed index"
    capabilities = ("market_sentiment",)
    limitations = ("composite of 7 US market indicators; market-wide, not specific to the stock",)

    def __init__(self, client: JsonFetcher):
        self.client = client

    def snapshot(self) -> SentimentSnapshot:
        fetched = self.client.get_json(FEAR_GREED_URL)
        fg = fetched.payload.get("fear_and_greed") if isinstance(fetched.payload, Mapping) else None
        if not isinstance(fg, Mapping) or not isinstance(fg.get("score"), (int, float)):
            raise ProviderError("parse_error", "fear_and_greed.score missing", url=FEAR_GREED_URL)
        num = lambda k: float(fg[k]) if isinstance(fg.get(k), (int, float)) else None  # noqa: E731
        return SentimentSnapshot(score=float(fg["score"]), rating=str(fg.get("rating", "")), previous_week=num("previous_1_week"),
                                 previous_month=num("previous_1_month"), as_of=_dt(fg.get("timestamp")), source_url=FEAR_GREED_URL,
                                 retrieved_at=fetched.retrieved_at, source_captured_at=fetched.source_captured_at, data_mode=fetched.data_mode)
