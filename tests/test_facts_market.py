"""SEC XBRL company facts and market chart parsing. All payloads here are SYNTHETIC."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tickercase.http_client import FakeHttpClient, LiveHttpClient, FetchError, TransportResponse
from tickercase.models import Candle, DataMode
from tickercase.providers.base import ProviderError
from tickercase.providers.market import CHART_URL, KLINE_DAYS, YahooChartProvider, annualized_volatility, build_kline, yahoo_symbol
from tickercase.providers.sec import TICKERS_URL
from tickercase.providers.sec_facts import COMPANYFACTS_URL, SecCompanyFactsProvider, annual_series, share_series

from conftest import TICKERS_PAYLOAD

FACTS_URL = COMPANYFACTS_URL.format(cik="0001234567")


def fy(start, end, val, accn, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": int(filed[:4]), "fp": "FY", "form": form, "filed": filed}


def test_annual_series_filters_quarters_dedupes_restatements_and_merges_concepts():
    gaap = {
        "Revenues": {"units": {"USD": [
            fy("2020-01-01", "2020-12-31", 90, "0001234567-21-000001", "2021-02-01"),
            fy("2021-01-01", "2021-12-31", 100, "0001234567-22-000001", "2022-02-01"),
        ]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            fy("2021-01-01", "2021-12-31", 101, "0001234567-23-000001", "2023-02-01"),  # comparative, later filing
            fy("2022-01-01", "2022-12-31", 120, "0001234567-23-000001", "2023-02-01"),
            fy("2022-10-01", "2022-12-31", 35, "0001234567-23-000001", "2023-02-01"),  # quarter inside a 10-K
            fy("2022-01-01", "2022-12-31", 999, "0001234567-22-000050", "2022-11-01", form="10-Q"),
            {"start": "bad", "end": "2022-12-31", "val": 1, "accn": "x", "form": "10-K", "filed": "2023-02-01"},
        ]}},
    }
    series = annual_series(gaap, ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"))
    assert [(p.period_end.year, p.value, p.concept[:7]) for p in series] == [
        (2020, Decimal(90), "Revenue"), (2021, Decimal(101), "Revenue"), (2022, Decimal(120), "Revenue")]
    assert series[0].concept == "Revenues"  # only the fallback concept has 2020
    assert series[1].concept.startswith("RevenueFromContract")  # preferred concept wins for 2021


def test_share_series_keeps_latest_filing_per_date_and_drops_bad_rows():
    dei = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2025-01-31", "val": 100, "accn": "a-1", "form": "10-K", "filed": "2025-02-20"},
        {"end": "2025-01-31", "val": 101, "accn": "a-2", "form": "10-K/A", "filed": "2025-03-20"},
        {"end": "2025-04-30", "val": 0, "accn": "a-3", "form": "10-Q", "filed": "2025-05-05"},
        {"end": "2025-07-31", "val": 99, "accn": "a-4", "form": "S-1", "filed": "2025-08-05"},
    ]}}}
    pts = share_series(dei)
    assert [(p.period_end, p.value) for p in pts] == [(date(2025, 1, 31), Decimal(101))]


def test_facts_provider_end_to_end_and_errors():
    payload = {"entityName": "Synthetic Test Co", "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        fy("2024-01-01", "2024-12-31", -5, "0001234567-25-000001", "2025-02-01")]}}}, "dei": {}}}
    fake = FakeHttpClient({TICKERS_URL: TICKERS_PAYLOAD, FACTS_URL: payload})
    facts = SecCompanyFactsProvider(fake).fetch_facts("test")
    assert facts.cik == "0001234567" and facts.company_name == "Synthetic Test Co"
    assert facts.net_income[0].value == Decimal(-5) and facts.revenue == []
    assert any("no annual revenue" in n for n in facts.notes) and any("shares outstanding" in n for n in facts.notes)
    assert facts.data_mode is DataMode.SYNTHETIC

    with pytest.raises(ProviderError) as exc:
        SecCompanyFactsProvider(FakeHttpClient({TICKERS_URL: TICKERS_PAYLOAD, FACTS_URL: {"facts": {}}})).fetch_facts("TEST")
    assert exc.value.code == "no_xbrl_facts"
    with pytest.raises(ProviderError) as exc:
        SecCompanyFactsProvider(FakeHttpClient({TICKERS_URL: TICKERS_PAYLOAD, FACTS_URL: []})).fetch_facts("TEST")
    assert exc.value.code == "parse_error"


def chart(closes, adj=None, error=None, start=1_700_000_000):
    stamps = [start + i * 86400 for i in range(len(closes))]
    return {"chart": {"result": None if error else [{
        "meta": {"currency": "usd", "gmtoffset": -14400},
        "timestamp": stamps,
        "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": adj if adj is not None else closes}]},
    }], "error": error}}


def test_market_parse_skips_nulls_and_computes_volatility():
    closes = [100.0 * (1.01 if i % 2 else 0.99) ** 1 for i in range(80)]
    closes[3] = None
    url = CHART_URL.format(symbol="TEST")
    snap = YahooChartProvider(FakeHttpClient({url: chart(closes)})).fetch_history("test")
    assert snap.symbol == "TEST" and snap.currency == "USD" and snap.observations == 79
    assert snap.annualized_volatility is not None and snap.annualized_volatility > 0
    assert snap.price_series[-1].day == snap.last_date
    assert snap.last_close == Decimal("101")


def test_market_short_history_has_no_volatility_and_errors_are_typed():
    url = CHART_URL.format(symbol="TEST")
    snap = YahooChartProvider(FakeHttpClient({url: chart([10.0, 11.0, 12.0])})).fetch_history("TEST")
    assert snap.annualized_volatility is None
    with pytest.raises(ProviderError) as exc:
        YahooChartProvider(FakeHttpClient({url: chart([], error={"description": "No data found"})})).fetch_history("TEST")
    assert exc.value.code == "unknown_symbol"
    with pytest.raises(ProviderError) as exc:
        YahooChartProvider(FakeHttpClient({url: chart([None, None])})).fetch_history("TEST")
    assert exc.value.code == "no_prices"


def ohlc_chart(rows, start=1_700_000_000):
    """rows of (open, high, low, close, volume), one per calendar day; adjusted close = close."""
    stamps = [start + i * 86400 for i in range(len(rows))]
    o, h, low, c, v = (list(x) for x in zip(*rows))
    return {"chart": {"result": [{
        "meta": {"currency": "USD", "gmtoffset": -14400},
        "timestamp": stamps,
        "indicators": {"quote": [{"open": o, "high": h, "low": low, "close": c, "volume": v}], "adjclose": [{"adjclose": c}]},
    }], "error": None}}


def test_market_keeps_candles_and_aggregates_weeks_and_months():
    rows = [(10.0 + i, 11.0 + i, 9.0 + i, 10.5 + i, 1000 + i) for i in range(70)]
    rows[5] = (None, 20.0, 9.0, 15.0, 7)  # no open: no candle, but the close still counts
    rows[6] = (15.0, 14.0, 9.0, 15.5, 7)  # high below the body: inconsistent bar, dropped
    url = CHART_URL.format(symbol="TEST")
    snap = YahooChartProvider(FakeHttpClient({url: ohlc_chart(rows)})).fetch_history("TEST")
    k = snap.kline
    assert snap.observations == 70 and k is not None and len(k.daily) == 68
    assert all(c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) for c in k.daily)
    # a week: first open, highest high, lowest low, last close, summed volume, labelled by its last day
    week = [c for c in k.daily if c.day.isocalendar()[:2] == k.weekly[2].day.isocalendar()[:2]]
    assert k.weekly[2] == Candle(day=week[-1].day, open=week[0].open, high=max(c.high for c in week), low=min(c.low for c in week),
                                 close=week[-1].close, volume=sum(c.volume for c in week))
    assert [(c.day.year, c.day.month) for c in k.monthly] == [(2023, 11), (2023, 12), (2024, 1)]
    assert sum(c.volume for c in k.monthly) == sum(c.volume for c in k.daily)
    assert k.daily[-1].day == snap.last_date and k.monthly[-1].close == k.daily[-1].close


def test_market_with_closes_only_has_no_candles_and_kline_is_trimmed():
    url = CHART_URL.format(symbol="TEST")
    assert YahooChartProvider(FakeHttpClient({url: chart([10.0, 11.0, 12.0])})).fetch_history("TEST").kline is None
    snap = YahooChartProvider(FakeHttpClient({url: ohlc_chart([(10.0, 11.0, 9.0, 10.0, 5)] * 300)})).fetch_history("TEST")
    assert len(snap.kline.daily) == KLINE_DAYS and snap.kline.daily[-1].day == snap.last_date
    assert build_kline([]) is None


def test_volatility_formula_and_symbol_mapping():
    assert annualized_volatility([1.0] * 100) == 0.0
    assert annualized_volatility([1.0, 2.0]) is None
    assert yahoo_symbol("brk.b") == "BRK-B"


def test_market_client_does_not_require_email_but_sec_client_does():
    ok = TransportResponse(200, {}, b'{"a": 1}')
    market = LiveHttpClient(user_agent="TickerCase/0.2", require_contact_email=False, service_name="Yahoo Finance",
                            transport=lambda u, h, t: ok, sleep=lambda s: None)
    assert market.get_json("https://example.org/x").payload == {"a": 1}
    sec = LiveHttpClient(user_agent="TickerCase/0.2", transport=lambda u, h, t: ok, sleep=lambda s: None)
    with pytest.raises(FetchError) as exc:
        sec.get_json("https://data.sec.gov/x")
    assert exc.value.code == "missing_user_agent"
    forbidden = LiveHttpClient(user_agent="TickerCase/0.2", require_contact_email=False, service_name="Yahoo Finance",
                               transport=lambda u, h, t: TransportResponse(403, {}, b""), sleep=lambda s: None)
    with pytest.raises(FetchError) as exc:
        forbidden.get_json("https://example.org/x")
    assert exc.value.code == "forbidden" and "Yahoo Finance" in exc.value.message and "SEC" not in exc.value.message
