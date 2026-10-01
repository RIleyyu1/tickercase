"""Oracle layers: option math, base rates, Form 4 parsing, sentiment sources, synthesis. All data SYNTHETIC."""

import math
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tickercase.http_client import FakeHttpClient
from tickercase.models import DataMode, OptionQuote, PriceBaseRate
from tickercase.oracle import build_oracle, independent_windows, p_end_above, p_touch
from tickercase.providers.insiders import InsiderProvider, parse_form4, xml_url
from tickercase.providers.market import MONTHLY_URL, monthly_closes, price_base_rate
from tickercase.providers.options import OPTIONS_URL, YahooOptionsProvider, iv_at
from tickercase.providers.sec import SUBMISSIONS_URL, TICKERS_URL
from tickercase.providers.sec_frames import FRAMES_URL, SecFramesBaseRateProvider
from tickercase.providers.sentiment import FEAR_GREED_URL, POLYMARKET_URL, FearGreedProvider, PolymarketProvider
from tickercase.providers.yahoo_auth import YahooAuthedClient
from tickercase.validation import validate_draft

from conftest import FIXED_TODAY, TICKERS_PAYLOAD, ps_draft
from test_analysis import market

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


# ------------------------------------------------------------------ option math

def test_probability_formulas():
    assert p_end_above(100, 100, 0.3, 1, 0.045) == pytest.approx(0.5, abs=1e-12)  # r = sigma^2 / 2: median at spot
    assert p_touch(100, 90, 0.3, 1, 0.04) == 1.0
    for level in (120, 200, 400):
        end, touch = p_end_above(100, level, 0.6, 3, 0.04), p_touch(100, level, 0.6, 3, 0.04)
        assert 0 < end < touch < 1
    # zero drift: touching is exactly twice ending above (reflection principle)
    vol = 0.5
    r = vol * vol / 2
    assert p_touch(100, 150, vol, 2, r) == pytest.approx(2 * p_end_above(100, 150, vol, 2, r), rel=1e-9)


def test_iv_interpolation_and_extrapolation():
    calls = [OptionQuote(strike=Decimal(k), implied_volatility=iv, open_interest=10) for k, iv in ((50, 0.5), (60, 0.6), (70, 0.7))]
    calls.append(OptionQuote(strike=Decimal(80), implied_volatility=0.9, open_interest=0, bid=0))  # no quote: ignored
    assert iv_at(calls, 55) == (pytest.approx(0.55), False)
    assert iv_at(calls, 100) == (0.7, True)
    assert iv_at([], 100) == (None, False)


def test_options_provider_picks_expiry_after_target():
    def ts(d):
        return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    e1, e2 = date(2027, 1, 15), date(2028, 1, 21)
    chain = lambda e: {"optionChain": {"result": [{"expirationDates": [ts(e1), ts(e2)], "quote": {"regularMarketPrice": 50},  # noqa: E731
                                                   "options": [{"calls": [{"strike": 40, "impliedVolatility": 0.5, "openInterest": 5},
                                                                          {"strike": 60, "impliedVolatility": 0.6, "openInterest": 7}]}]}], "error": None}}
    base = OPTIONS_URL.format(symbol="TEST")
    fake = FakeHttpClient({base: chain(e1), f"{base}?date={ts(e1)}": chain(e1), f"{base}?date={ts(e2)}": chain(e2)})
    snap = YahooOptionsProvider(fake).fetch_chain("test", date(2027, 6, 1), Decimal(60))
    assert snap.expiry == e2 and snap.oi_at_or_above_target == 7 and snap.target_iv == pytest.approx(0.6)
    later = YahooOptionsProvider(fake).fetch_chain("test", date(2030, 1, 1), Decimal(100))
    assert later.expiry == e2 and later.target_iv_extrapolated


def test_authed_client_adds_crumb_and_keeps_snapshot_url():
    client = YahooAuthedClient(min_interval_seconds=0)
    seen = []

    def fake_open(url):
        seen.append(url)
        if url.endswith("getcrumb"):
            return 200, b"abc123", {}
        if "fc.yahoo.com" in url:
            return 404, b"", {}
        return 200, b'{"ok": 1}', {}
    client._open = fake_open
    got = client.get_json("https://query2.finance.yahoo.com/v7/finance/options/TEST?date=1")
    assert got.url.endswith("?date=1") and got.payload == {"ok": 1}
    assert seen[-1].endswith("date=1&crumb=abc123")


# ------------------------------------------------------------------ base rates

def frame(rows):
    return {"data": [{"cik": c, "entityName": f"Co {c}", "val": v} for c, v in rows]}


def test_frames_base_rate_band_widening_and_self_exclusion():
    start = [(i, 100) for i in range(1, 41)] + [(99, 100)]
    end = [(i, 300 if i <= 4 else 110) for i in range(1, 41)] + [(99, 1000)]
    responses = {
        FRAMES_URL.format(concept="RevenueFromContractWithCustomerExcludingAssessedTax", year=2022): frame(start),
        FRAMES_URL.format(concept="RevenueFromContractWithCustomerExcludingAssessedTax", year=2025): frame(end),
        FRAMES_URL.format(concept="Revenues", year=2022): frame([]),
        FRAMES_URL.format(concept="Revenues", year=2025): frame([]),
    }
    br = SecFramesBaseRateProvider(FakeHttpClient(responses)).base_rate(
        metric="annual_revenue", base_value=Decimal(100), required_cagr=Decimal("0.4"), end_year=2025, years=3, exclude_cik=99)
    assert br.companies == 40 and br.achieved == 4 and br.rate == pytest.approx(0.1)
    assert br.examples[0]["name"].startswith("Co ")
    assert br.data_mode is DataMode.SYNTHETIC


def test_monthly_resampling_and_price_base_rate():
    stamps = [int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()) + 7 * 86400 * i for i in range(120)]  # weekly bars
    closes = [10 + i for i in range(120)]
    payload = {"chart": {"result": [{"timestamp": stamps, "indicators": {"adjclose": [{"adjclose": closes}]}}]}}
    monthly = monthly_closes(payload, MONTHLY_URL.format(symbol="X"))
    assert len(monthly) in (27, 28) and all(a[0] < b[0] for a, b in zip(monthly, monthly[1:]))
    windows, hits, med, best = price_base_rate(monthly, 12, Decimal("0.5"))
    assert windows == len(monthly) - 12 and 0 <= hits <= windows and best >= med


# ------------------------------------------------------------------ insiders

FORM4 = """<ownershipDocument><aff10b5One>1</aff10b5One>
<reportingOwner><reportingOwnerId><rptOwnerName>Jane Exec</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>10</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionCoding><transactionCode>F</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>50</value></transactionShares><transactionPricePerShare><value>10</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def test_parse_form4_codes_and_plan():
    txns = parse_form4(FORM4, date(2026, 5, 1), "u")
    assert [(t.code, t.value) for t in txns] == [("S", Decimal("10000.00")), ("F", Decimal("500.00"))]
    assert txns[0].plan_10b5_1 and txns[0].role == "CFO"


def test_insider_summary_counts_by_type():
    cik = "0001234567"
    subs = {"cik": "1234567", "filings": {"recent": {
        "form": ["4", "4", "10-Q", "4"], "filingDate": ["2026-09-01", "2026-05-01", "2026-08-01", "2024-01-01"],
        "accessionNumber": ["0001234567-26-000003", "0001234567-26-000002", "0001234567-26-000001", "0001234567-24-000001"],
        "primaryDocument": ["xslF345X05/a.xml", "xslF345X05/b.xml", "q.htm", "xslF345X05/c.xml"]}}}
    purchase = FORM4.replace("<transactionCode>S</transactionCode>", "<transactionCode>P</transactionCode>").replace("<aff10b5One>1</aff10b5One>", "")
    responses = {TICKERS_URL: TICKERS_PAYLOAD, SUBMISSIONS_URL.format(cik=cik): subs,
                 xml_url(cik, "0001234567-26-000003", "xslF345X05/a.xml"): FORM4,
                 xml_url(cik, "0001234567-26-000002", "xslF345X05/b.xml"): purchase}
    s = InsiderProvider(FakeHttpClient(responses)).summary("TEST", today=FIXED_TODAY)
    assert (s.filings_listed, s.filings_read) == (2, 2)  # the 2024 filing is outside the 12-month window
    assert (s.sales, s.purchases, s.tax_withholding) == (1, 1, 2)
    assert s.sale_value == Decimal("10000.00") and s.plan_sale_value == Decimal("10000.00")
    assert s.net_open_market_value == Decimal(0)


# ------------------------------------------------------------------ sentiment sources

def test_polymarket_filters_by_ticker_and_date():
    payload = {"events": [
        {"title": "What will Test Co (TEST) hit in October?", "slug": "t1", "endDate": "2026-11-01T00:00:00Z",
         "markets": [{"question": "Will TEST hit $70?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.2","0.8"]', "volume": "1000"}]},
        {"title": "Old (TEST) market", "slug": "t2", "endDate": "2025-01-01T00:00:00Z", "markets": [{"question": "old"}]},
        {"title": "Other (OTHR) market", "slug": "t3", "endDate": "2026-12-01T00:00:00Z", "markets": [{"question": "x"}]},
    ]}
    res = PolymarketProvider(FakeHttpClient({POLYMARKET_URL.format(q="TEST"): payload})).search("TEST", now=NOW)
    assert [m.question for m in res.markets] == ["Will TEST hit $70?"] and res.markets[0].probability_yes == 0.2


def test_fear_greed_parse():
    payload = {"fear_and_greed": {"score": 28.1, "rating": "fear", "previous_1_month": 45, "timestamp": "2026-10-01T00:00:00+00:00"}}
    snap = FearGreedProvider(FakeHttpClient({FEAR_GREED_URL: payload})).snapshot()
    assert snap.score == 28.1 and snap.previous_month == 45


# ------------------------------------------------------------------ synthesis

def test_history_with_few_independent_windows_is_context_only():
    claim = validate_draft(ps_draft(), today=FIXED_TODAY).claim
    short = PriceBaseRate(symbol="X", window_months=60, windows=12, hits=12, rate=1.0, required_return=Decimal(1), history_start=date(2020, 1, 1),
                          source_url="u", retrieved_at=NOW, data_mode=DataMode.LIVE)
    assert independent_windows(short) == 1
    o = build_oracle(claim, market=market(), options=None, base_rate=None, price_rate=short, risk_free=Decimal("0.04"), today=FIXED_TODAY)
    m4 = next(m for m in o.methods if m.id == "M4")
    assert m4.status == "ok" and m4.probability == 1.0
    assert o.high < 1.0  # M4 not used in the range
    assert any("重叠" in lim.zh for lim in m4.limitations)


def test_no_method_gives_unknown():
    claim = validate_draft(ps_draft(), today=FIXED_TODAY).claim
    o = build_oracle(claim, market=None, options=None, base_rate=None, price_rate=None, risk_free=None, today=FIXED_TODAY)
    assert o.tier == "unknown" and o.low is None and all(m.status == "not_computable" for m in o.methods)
