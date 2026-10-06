"""Daily closing prices from the public Yahoo Finance chart endpoint.

Endpoint: https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=5y&interval=1d
No API key. The endpoint is unofficial: it has no published terms for automated
use, no service guarantee and may change or rate-limit without notice. Results
are labelled with their source and retrieval time; a failure is reported as a
provider error and no other price source is substituted.

Volatility is the sample standard deviation of daily log returns of the
adjusted close, scaled by sqrt(252). It describes the past, not the future.

When the response carries open, high, low and volume, the same bars are kept as
candles for the K-line chart (daily, weekly and monthly, aggregated here). A
response with closes only gives no candles; nothing is filled in.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

from ..http_client import FetchedJson, JsonFetcher
from ..models import Candle, KLine, MarketSnapshot, PricePoint
from .base import Provider, ProviderError, ProviderParseError

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5y&interval=1d"
TRADING_DAYS = 252
MIN_RETURNS_FOR_VOLATILITY = 60
KLINE_DAYS, KLINE_WEEKS, KLINE_MONTHS = 130, 110, 61


def yahoo_symbol(ticker: str) -> str:
    """SEC tickers use '-' or '.' for share classes; Yahoo uses '-' (BRK-B)."""
    return ticker.strip().upper().replace(".", "-")


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _plain(value: float, places: int) -> Decimal:
    """Rounded Decimal without trailing zeros or exponent notation (50.0 -> 50, 12.30 -> 12.3)."""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return Decimal(text)


def annualized_volatility(closes: list[float]) -> Optional[float]:
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    if len(returns) < MIN_RETURNS_FOR_VOLATILITY:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def _volume(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _bar(day: date, o: float, h: float, low: float, c: float, v: Optional[int]) -> Candle:
    return Candle(day=day, open=round(o, 4), high=round(h, 4), low=round(low, 4), close=round(c, 4), volume=v)


def aggregate(bars: list[Candle], key) -> list[Candle]:
    """Group consecutive daily bars by key (ISO week or month): first open, highest high, lowest low, last close, summed volume."""
    out: list[Candle] = []
    group: list[Candle] = []
    for bar in bars:
        if group and key(bar.day) != key(group[-1].day):
            out.append(_merge(group))
            group = []
        group.append(bar)
    if group:
        out.append(_merge(group))
    return out


def _merge(group: list[Candle]) -> Candle:
    vols = [b.volume for b in group if b.volume is not None]
    return _bar(group[-1].day, group[0].open, max(b.high for b in group), min(b.low for b in group), group[-1].close,
                sum(vols) if vols else None)


def build_kline(bars: list[Candle]) -> Optional[KLine]:
    if not bars:
        return None
    return KLine(daily=bars[-KLINE_DAYS:], weekly=aggregate(bars, lambda d: d.isocalendar()[:2])[-KLINE_WEEKS:],
                 monthly=aggregate(bars, lambda d: (d.year, d.month))[-KLINE_MONTHS:])


class YahooChartProvider(Provider):
    provider_id = "yahoo_finance_chart"
    display_name = "Yahoo Finance chart (unofficial)"
    capabilities = ("daily_close", "historical_volatility")
    limitations = (
        "unofficial endpoint without published terms or service guarantee",
        "prices are not verified against an exchange feed",
    )

    def __init__(self, client: JsonFetcher):
        self.client = client

    def fetch_history(self, ticker: str) -> MarketSnapshot:
        symbol = yahoo_symbol(ticker)
        fetched = self.client.get_json(CHART_URL.format(symbol=symbol))
        return self.parse(symbol, fetched)

    def parse(self, symbol: str, fetched: FetchedJson) -> MarketSnapshot:
        url = fetched.url
        payload = fetched.payload
        chart = payload.get("chart") if isinstance(payload, Mapping) else None
        if not isinstance(chart, Mapping):
            raise ProviderParseError("parse_error", "chart: expected an object", url=url)
        error = chart.get("error")
        if error:
            desc = error.get("description") if isinstance(error, Mapping) else str(error)
            raise ProviderError("unknown_symbol", f"quote source has no data for '{symbol}': {desc}", url=url)
        results = chart.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            raise ProviderParseError("parse_error", "chart.result: expected a non-empty array", url=url)
        result = results[0]
        meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
        stamps = result.get("timestamp")
        indicators = result.get("indicators") if isinstance(result.get("indicators"), Mapping) else {}
        quote = (indicators.get("quote") or [{}])[0] if isinstance(indicators.get("quote"), list) else {}
        adj = (indicators.get("adjclose") or [{}])[0] if isinstance(indicators.get("adjclose"), list) else {}
        closes = quote.get("close") if isinstance(quote, Mapping) else None
        adjcloses = adj.get("adjclose") if isinstance(adj, Mapping) else None
        if not isinstance(stamps, list) or not isinstance(closes, list):
            raise ProviderParseError("parse_error", "chart: timestamp and close arrays are required", url=url)
        if not isinstance(adjcloses, list) or len(adjcloses) != len(stamps):
            adjcloses = closes
        offset = meta.get("gmtoffset") if isinstance(meta.get("gmtoffset"), int) else 0

        opens, highs, lows, vols = (quote.get(k) if isinstance(quote, Mapping) else None for k in ("open", "high", "low", "volume"))
        has_ohlc = all(isinstance(x, list) and len(x) == len(stamps) for x in (opens, highs, lows))
        rows: list[tuple[date, float, float]] = []
        bars: list[Candle] = []
        for i, ts in enumerate(stamps):
            if isinstance(ts, bool) or not isinstance(ts, int) or i >= len(closes):
                continue
            close, adj_close = _num(closes[i]), _num(adjcloses[i]) if i < len(adjcloses) else None
            if close is None:
                continue
            day = (datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(seconds=offset)).date()
            rows.append((day, close, adj_close or close))
            if has_ohlc:
                o, h, low = _num(opens[i]), _num(highs[i]), _num(lows[i])
                if None not in (o, h, low) and low <= min(o, close) and h >= max(o, close):
                    v = _volume(vols[i]) if isinstance(vols, list) and i < len(vols) else None
                    bars.append(_bar(day, o, h, low, close, v))
        if not rows:
            raise ProviderError("no_prices", f"quote source returned no usable closing prices for '{symbol}'", url=url)

        vol = annualized_volatility([r[2] for r in rows])
        weekly = rows[::5]
        if weekly[-1] is not rows[-1]:
            weekly.append(rows[-1])
        last_day, last_close, _ = rows[-1]
        currency = meta.get("currency") if isinstance(meta.get("currency"), str) else None
        return MarketSnapshot(
            provider_id=self.provider_id,
            symbol=symbol,
            currency=currency.upper() if currency else None,
            last_close=_plain(last_close, 6),
            last_date=last_day,
            history_start=rows[0][0],
            observations=len(rows),
            annualized_volatility=Decimal(str(round(vol, 6))) if vol is not None else None,
            volatility_window=f"daily log returns of adjusted close, {rows[0][0]} to {last_day} ({len(rows) - 1} returns)",
            price_series=[PricePoint(day=d, close=_plain(c, 4)) for d, c, _ in weekly],
            kline=build_kline(bars),
            source_url=url,
            retrieved_at=fetched.retrieved_at,
            source_captured_at=fetched.source_captured_at,
            data_mode=fetched.data_mode,
        )


MONTHLY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=max&interval=1mo"


def monthly_closes(payload: Any, url: str) -> list[tuple[date, float]]:
    """One adjusted close per calendar month (the month's last bar; falls back to closes)."""
    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    results = chart.get("result") if isinstance(chart, Mapping) else None
    if not isinstance(results, list) or not results:
        raise ProviderError("no_prices", "monthly chart has no result", url=url)
    r = results[0]
    stamps = r.get("timestamp") or []
    ind = r.get("indicators") or {}
    adj = ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose") or ((ind.get("quote") or [{}])[0] or {}).get("close") or []
    by_month: dict[tuple[int, int], tuple[date, float]] = {}
    for ts, px in zip(stamps, adj):
        v = _num(px)
        if isinstance(ts, int) and not isinstance(ts, bool) and v is not None:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            by_month[(d.year, d.month)] = (d, v)  # Yahoo may return weekly bars for range=max; keep the last close of each month
    return [by_month[k] for k in sorted(by_month)]


def price_base_rate(closes: list[tuple[date, float]], months: int, required_return: Decimal):
    """(windows, hits, median return, best return) over every start month with a full window."""
    if months < 1 or len(closes) <= months:
        return 0, 0, None, None
    returns = sorted(closes[i + months][1] / closes[i][1] - 1 for i in range(len(closes) - months))
    hits = sum(1 for x in returns if x >= float(required_return))
    return len(returns), hits, returns[len(returns) // 2], returns[-1]


def price_touch_hits(closes: list[tuple[date, float]], months: int, required_return: Decimal) -> int:
    """Windows in which some month-end close reached the required rise (month-end closes miss intramonth highs)."""
    if months < 1 or len(closes) <= months:
        return 0
    need = 1 + float(required_return)
    return sum(1 for i in range(len(closes) - months) if max(c for _, c in closes[i + 1: i + months + 1]) / closes[i][1] >= need)
