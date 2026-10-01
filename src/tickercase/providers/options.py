"""Option chains from Yahoo Finance (needs the authenticated client in live mode).

URLs (crumb added by the client, not part of the snapshot key):
  https://query2.finance.yahoo.com/v7/finance/options/<SYM>              -> expirations + nearest chain
  https://query2.finance.yahoo.com/v7/finance/options/<SYM>?date=<unix>  -> chain for one expiry

The expiry used is the first one on or after the claim's target date, or the
longest available when the target date is beyond every listed expiry.
Implied volatility at the target strike is interpolated linearly between the
two nearest strikes with a usable quote; beyond the highest strike the
highest strike's IV is used and the result is marked as extrapolated.

For event estimates the provider also reads the at-the-money IV of up to
TERM_MAX other expiries (about monthly for the first half year, then every 2-6 months to 24 months).
Each is one extra request; an expiry that fails is skipped.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

from ..http_client import FetchedJson, JsonFetcher
from ..models import OptionQuote, OptionsSnapshot, TermPoint
from .base import Provider, ProviderError, ProviderParseError
from .market import yahoo_symbol

OPTIONS_URL = "https://query2.finance.yahoo.com/v7/finance/options/{symbol}"
MIN_IV, MAX_IV = 0.01, 5.0
TERM_DAYS = (30, 60, 91, 121, 152, 182, 243, 304, 365, 456, 547, 730)
TERM_MAX = 12


def _f(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _usable(q: OptionQuote) -> bool:
    return q.implied_volatility is not None and MIN_IV < q.implied_volatility < MAX_IV and (q.open_interest > 0 or (q.bid or 0) > 0)


def iv_at(calls: list[OptionQuote], strike: float) -> tuple[Optional[float], bool]:
    """(IV at the strike, extrapolated?) from usable quotes sorted by strike."""
    pts = sorted(((float(q.strike), q.implied_volatility) for q in calls if _usable(q)), key=lambda x: x[0])
    if not pts:
        return None, False
    if strike <= pts[0][0]:
        return pts[0][1], strike < pts[0][0]
    if strike >= pts[-1][0]:
        return pts[-1][1], strike > pts[-1][0]
    for (k0, v0), (k1, v1) in zip(pts, pts[1:]):
        if k0 <= strike <= k1:
            w = (strike - k0) / (k1 - k0) if k1 > k0 else 0
            return v0 + w * (v1 - v0), False
    return None, False


class YahooOptionsProvider(Provider):
    provider_id = "yahoo_finance_options"
    display_name = "Yahoo Finance option chains (unofficial)"
    capabilities = ("option_chain", "implied_volatility")
    limitations = ("unofficial endpoint; quotes may be stale outside market hours", "implied volatility as published by the source")

    def __init__(self, client: JsonFetcher):
        self.client = client

    @staticmethod
    def _result(fetched: FetchedJson) -> Mapping[str, Any]:
        payload = fetched.payload
        chain = payload.get("optionChain") if isinstance(payload, Mapping) else None
        if not isinstance(chain, Mapping):
            raise ProviderParseError("parse_error", "optionChain: expected an object", url=fetched.url)
        if chain.get("error"):
            raise ProviderError("no_options", f"no option chain: {chain.get('error')}", url=fetched.url)
        results = chain.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            raise ProviderError("no_options", "this ticker has no listed options", url=fetched.url)
        return results[0]

    @staticmethod
    def _calls(res: Mapping[str, Any]) -> list[OptionQuote]:
        options = res.get("options")
        if not isinstance(options, list) or not options or not isinstance(options[0], Mapping):
            return []
        calls: list[OptionQuote] = []
        for c in options[0].get("calls", []) or []:
            if not isinstance(c, Mapping) or _f(c.get("strike")) is None:
                continue
            calls.append(OptionQuote(
                strike=Decimal(str(c["strike"])), implied_volatility=_f(c.get("impliedVolatility")),
                open_interest=int(c.get("openInterest") or 0), bid=_f(c.get("bid")), ask=_f(c.get("ask")), last=_f(c.get("lastPrice")),
            ))
        calls.sort(key=lambda q: q.strike)
        return calls

    def _term(self, symbol: str, expiries: list[date], stamp_by_day: dict, today: date, spot: float, have: dict[date, float]) -> list[TermPoint]:
        wanted: list[date] = []
        for days in TERM_DAYS:
            d = next((e for e in expiries if (e - today).days >= days), None)
            if d is not None and d not in wanted and d not in have:
                wanted.append(d)
        points = dict(have)
        for d in wanted[:TERM_MAX]:
            try:
                res = self._result(self.client.get_json(f"{OPTIONS_URL.format(symbol=symbol)}?date={stamp_by_day[d]}"))
            except Exception:  # one missing expiry only thins the term structure
                continue
            iv, _ = iv_at(self._calls(res), spot)
            if iv:
                points[d] = iv
        return [TermPoint(expiry=d, atm_iv=v) for d, v in sorted(points.items())]

    def fetch_chain(self, ticker: str, target_date: date, target_price: Decimal, *, today: Optional[date] = None) -> OptionsSnapshot:
        symbol = yahoo_symbol(ticker)
        first = self.client.get_json(OPTIONS_URL.format(symbol=symbol))
        res = self._result(first)
        stamps = [x for x in res.get("expirationDates", []) if isinstance(x, int) and not isinstance(x, bool)]
        if not stamps:
            raise ProviderError("no_options", f"no option expirations listed for {symbol}", url=first.url)
        expiries = sorted(datetime.fromtimestamp(x, tz=timezone.utc).date() for x in stamps)
        stamp_by_day = {datetime.fromtimestamp(x, tz=timezone.utc).date(): x for x in stamps}
        chosen = next((d for d in expiries if d >= target_date), expiries[-1])
        fetched = self.client.get_json(f"{OPTIONS_URL.format(symbol=symbol)}?date={stamp_by_day[chosen]}")
        res = self._result(fetched)
        quote = res.get("quote") if isinstance(res.get("quote"), Mapping) else {}
        spot = _f(quote.get("regularMarketPrice"))
        options = res.get("options")
        if not isinstance(options, list) or not options or not isinstance(options[0], Mapping):
            raise ProviderParseError("parse_error", "options: expected a non-empty array", url=fetched.url)
        calls = self._calls(res)
        if not calls:
            raise ProviderError("no_options", f"no calls listed for the {chosen} expiry", url=fetched.url)
        target_iv, extrapolated = iv_at(calls, float(target_price))
        atm_iv, _ = iv_at(calls, spot) if spot else (None, False)
        retrieved = fetched.retrieved_at
        term = self._term(symbol, expiries, stamp_by_day, today or retrieved.date(), spot, {chosen: atm_iv} if atm_iv else {}) if spot else []
        return OptionsSnapshot(
            symbol=symbol,
            underlying_price=Decimal(str(round(spot, 4))) if spot else None,
            expiry=chosen,
            days_to_expiry=(chosen - retrieved.date()).days,
            expirations=expiries,
            atm_iv=atm_iv,
            target_iv=target_iv,
            target_iv_extrapolated=extrapolated,
            max_strike=calls[-1].strike,
            oi_at_or_above_target=sum(q.open_interest for q in calls if q.strike >= target_price),
            total_call_oi=sum(q.open_interest for q in calls),
            calls=calls,
            term=term,
            source_url=fetched.url,
            retrieved_at=retrieved,
            source_captured_at=fetched.source_captured_at,
            data_mode=fetched.data_mode,
        )
