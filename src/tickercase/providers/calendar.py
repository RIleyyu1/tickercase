"""The next earnings date (v0.12), a catalyst every stock has: Yahoo Finance quoteSummary, calendarEvents module.

Endpoint: https://query2.finance.yahoo.com/v10/finance/quoteSummary/<SYMBOL>?modules=calendarEvents
It needs the session cookie and crumb, so it goes through ``YahooAuthedClient`` like the option chains. Yahoo marks
dates the company has not confirmed (isEarningsDateEstimate); the page shows that mark.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional

from ..http_client import JsonFetcher
from ..models import EarningsDate
from .base import Provider, ProviderError, ProviderParseError
from .market import yahoo_symbol

CALENDAR_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=calendarEvents"


def _raw(node: Any) -> Optional[float]:
    value = node.get("raw") if isinstance(node, Mapping) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class EarningsCalendarProvider(Provider):
    provider_id = "yahoo_finance_calendar"
    display_name = "Yahoo Finance earnings calendar (unofficial)"
    capabilities = ("earnings_date",)
    limitations = ("dates may be Yahoo's estimate until the company announces them",)

    def __init__(self, client: JsonFetcher):
        self.client = client

    def next_earnings(self, ticker: str, *, today: date) -> EarningsDate:
        url = CALENDAR_URL.format(symbol=yahoo_symbol(ticker))
        fetched = self.client.get_json(url)
        result = ((fetched.payload or {}).get("quoteSummary") or {}).get("result") if isinstance(fetched.payload, Mapping) else None
        if not isinstance(result, list) or not result:
            raise ProviderParseError("parse_error", "quoteSummary.result: expected a non-empty array", url=url)
        earnings = ((result[0] or {}).get("calendarEvents") or {}).get("earnings") or {}
        days = sorted(date.fromisoformat(d["fmt"]) for d in earnings.get("earningsDate") or [] if isinstance(d, Mapping) and isinstance(d.get("fmt"), str))
        upcoming = [d for d in days if d >= today]
        if not upcoming:
            raise ProviderError("no_earnings_date", "the calendar lists no upcoming earnings date", url=url)
        return EarningsDate(day=upcoming[0], estimate=bool(earnings.get("isEarningsDateEstimate")), eps_average=_raw(earnings.get("earningsAverage")),
                            revenue_average=_raw(earnings.get("revenueAverage")), source_url=url, retrieved_at=fetched.retrieved_at,
                            data_mode=fetched.data_mode)
