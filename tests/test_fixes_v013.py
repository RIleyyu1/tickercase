"""Bugs found by the persona review of v0.13 and fixed in v0.13.2. Data here is SYNTHETIC."""

from datetime import date
from decimal import Decimal

from tickercase.calculations import calculate, deadline_from
from tickercase.extract import extract_claim
from tickercase.providers.base import ProviderError
from tickercase.providers.calendar import EarningsCalendarProvider
from tickercase.service import CaseService, growth_from_base_end, note_zh
from tickercase.storage import CaseStore
from tickercase.ui import cockpit as ck
from tickercase.validation import confirm, validate_draft

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, offline_factory, ps_draft
from test_analysis import SHARES, annual, facts, market, run


def test_whole_years_are_calendar_years():
    # "2 years" used to end on 2028-10-27 and "3 years" on 2029-10-28 from the same day
    assert deadline_from(date(2026, 10, 28), Decimal("2")) == date(2028, 10, 28)
    assert deadline_from(date(2026, 10, 28), Decimal("3")) == date(2029, 10, 28)
    assert deadline_from(date(2028, 2, 29), Decimal("1")) == date(2029, 2, 28)
    assert deadline_from(date(2026, 10, 28), Decimal("0.17")) == date(2026, 12, 29)  # parts of a year stay in days


def test_a_derived_share_count_is_checked_on_its_own_rate():
    # the target count was current x (1 + rate) ** horizon, but E3 measured it from the reported date over a longer span,
    # so the default trend looked lower than itself and short claims were judged "contrary"
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]
    draft = ps_draft(target_assumed_shares=None, current_shares="95000000", share_change_mode="trend", share_change_rate="0.0104",
                     horizon_years="0.17", target_price="60", valuation_multiple="20")
    out = run(draft, facts(revenue, share_points=SHARES), market())
    e3 = next(i for i in out.items if i.id == "E3")
    assert Decimal(e3.measured["implied_annual_change"]) == Decimal("0.0104")
    assert e3.stance != "contrary" and "1.04%" in e3.detail_zh and "当前股份数" in e3.detail_zh
    r2 = next(r for r in out.rechecks if r.id == "R2")
    assert "任何时候" not in r2.trigger_zh and "at any time" not in r2.trigger


def test_required_growth_runs_from_the_base_period_end_when_the_base_is_reported():
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]
    claim = validate_draft(ps_draft(base_annual_metric="200000000", valuation_multiple="20"), today=FIXED_TODAY).claim
    out = run(ps_draft(base_annual_metric="200000000", valuation_multiple="20"), facts(revenue, share_points=SHARES), market())
    e1 = next(i for i in out.items if i.id == "E1")
    before = next(c for c in calculate(claim) if c.name == "required_metric_cagr")
    after = next(c for c in growth_from_base_end(calculate(claim), claim, out.items) if c.name == "required_metric_cagr")
    assert before.value != after.value  # 5 years from today vs 5.75 years from the end of FY2025
    assert after.value.quantize(Decimal("0.0001")) == Decimal(e1.measured["required_cagr_from_reported"]).quantize(Decimal("0.0001"))
    assert after.inputs["base_period_end"] == "2025-12-31" and "years_from_base_period_end" in after.formula
    assert out.sensitivity.base_offset_years == "0.7474"  # four decimals, so "h + offset" in the note is the year count used
    # the user's own base, not the reported one, keeps the claim's horizon
    own = validate_draft(ps_draft(base_annual_metric="150000000", valuation_multiple="20"), today=FIXED_TODAY).claim
    same = next(c for c in growth_from_base_end(calculate(own), own, out.items) if c.name == "required_metric_cagr")
    assert same.formula.endswith("horizon_years) - 1")


def test_ten_thousand_and_a_valuation_in_the_sentence_are_read():
    known = {"NVDA", "IONQ", "TSLA", "AAPL"}.__contains__
    btc = extract_claim("比特币明年10万", today=date(2026, 10, 28), is_known_ticker=known)
    assert btc.target_price == Decimal("100000") and btc.ticker is None
    ionq = extract_claim("IONQ 2 年内涨到 100，按 40 倍市盈率", today=date(2026, 10, 28), is_known_ticker=known)
    assert (ionq.target_price, ionq.valuation_method, ionq.valuation_multiple) == (Decimal("100"), "price_to_earnings", Decimal("40"))
    nvda = extract_claim("NVDA 40倍PE 三年后翻倍", today=date(2026, 10, 28), is_known_ticker=known)
    assert nvda.target_multiple == Decimal("2") and nvda.valuation_multiple == Decimal("40")  # "40 倍" is the multiple, not "rises 40 times"
    tsla = extract_claim("TSLA will hit 500 in 2 years at 30x P/S", today=date(2026, 10, 28), is_known_ticker=known)
    assert (tsla.target_price, tsla.valuation_method) == (Decimal("500"), "price_to_sales")
    # times, percentages and dates are not prices
    assert extract_claim("NVDA 涨 3 倍", today=date(2026, 10, 28), is_known_ticker=known).target_multiple == Decimal("3")
    assert extract_claim("TSLA in 2 years 500", today=date(2026, 10, 28), is_known_ticker=known).target_price == Decimal("500")


def test_provider_notes_read_in_chinese():
    assert note_zh("showing the 40 most recent of 58 matching filings") == "只列出最近的 40 份（符合条件的共 58 份）"
    assert note_zh("revenue series combines concepts: Revenues, SalesRevenueNet") == "营收序列合并了多个 XBRL 概念：Revenues, SalesRevenueNet"
    assert note_zh("something new") == "something new"


def test_no_earnings_date_is_a_note_not_a_failed_source(tmp_path, monkeypatch):
    def none_listed(self, ticker, *, today):
        raise ProviderError("no_earnings_date", "the calendar lists no upcoming earnings date")

    monkeypatch.setattr(EarningsCalendarProvider, "next_earnings", none_listed)
    settings = make_settings(tmp_path)
    svc = CaseService(settings, fetcher_factory=offline_factory(settings), store=CaseStore(tmp_path / "cases"), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    draft = ps_draft()
    result = svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")
    assert result.data_steps["earnings_date"] == "ok" and not any(e.code == "no_earnings_date" for e in result.provider_errors)
    assert any("暂未列出下一次财报日期" in w for w in result.warnings_zh)
    assert ck.warnings_of(result, True) == []  # no red lamp for it


def test_rows_round_to_the_total_shown():
    assert sum(ck.rounded_to_total([1.04, 1.04, 0.12], 2.2)) == __import__("pytest").approx(2.2)
    assert ck.rounded_to_total([-2.24, -9.26, 0.02, -0.71, 0.0, -0.16], -12.4) == __import__("pytest").approx([-2.2, -9.3, 0.0, -0.7, 0.0, -0.2], abs=1e-9)


# ---------------------------------------------------------------- round 2 of the retest


def test_whole_months_are_calendar_months_and_years_keep_their_day():
    assert deadline_from(date(2026, 10, 28), Decimal("0.0833")) == date(2026, 11, 28)  # "一个月内"
    assert deadline_from(date(2026, 10, 28), Decimal("0.1667")) == date(2026, 12, 28)  # "两个月内"
    assert deadline_from(date(2026, 8, 31), Decimal("0.0833")) == date(2026, 9, 30)  # no 31 September
    known = {"NVDA", "TSLA"}.__contains__
    assert extract_claim("NVDA 一个月内涨到 220", today=date(2026, 10, 28), is_known_ticker=known).horizon_years == Decimal("0.0833")
    nextyear = extract_claim("特斯拉明年要涨到1000", today=date(2026, 10, 28), is_known_ticker=known)
    assert deadline_from(date(2026, 10, 28), nextyear.horizon_years) == date(2027, 12, 31)  # it ended on 12-29


def test_a_default_multiple_equal_to_today_after_rounding_supports():
    # the default is today's multiple rounded to two decimals: 23.76 against 23.7512 used to read "1.00x", neutral
    revenue = [annual(2022, 100_000_000), annual(2025, 199_990_000)]
    out = run(ps_draft(valuation_multiple="23.76", current_shares="95000000"), facts(revenue, share_points=SHARES), market())
    e2 = next(i for i in out.items if i.id == "E2")
    assert e2.stance == "supporting" and "与当前水平相同" in e2.detail_zh


def test_profit_does_not_come_with_a_loss_warning():
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]
    profit = [annual(2022, 10_000_000), annual(2025, 30_000_000)]
    out = run(ps_draft(valuation_multiple="20"), facts(revenue, net_income=profit, share_points=SHARES), market())
    e4 = next(i for i in out.items if i.id == "E4")
    assert "处于盈利" in e4.detail_zh and "稀释" not in e4.detail_zh


def test_target_not_above_the_price_is_flagged():
    v = validate_draft(ps_draft(target_price="40"), today=FIXED_TODAY)
    assert any("不高于参考价" in w for w in v.warnings_zh)


def test_catalyst_numbers_skip_dates_and_allow_written_precision():
    from tickercase.catalysts import _within_written_precision, _without_dates

    assert "11" not in _without_dates("预计 11月5日 发布财报") and "2027" not in _without_dates("Nov. 5, 2027 earnings, 2027-H1")
    assert _within_written_precision("2.6亿", Decimal("2.6"), "亿", [Decimal("255400000")])
    assert not _within_written_precision("2.6亿", Decimal("2.6"), "亿", [Decimal("240000000")])
    assert ck.problem_text("summary: 2.6亿 is not in the cited sources", True) == "摘要里的 2.6亿 在引用的来源中找不到"


def test_a_range_that_rounds_to_one_number_keeps_a_decimal():
    assert ck.range_text(0.099, 0.104) == ("9.9%", "10.4%")
    assert ck.range_text(0.17, 0.18) == ("17%", "18%")
    assert ck.range_str(0.0, 0.0004) == "<0.1%" and ck.range_str(0.17, 0.18) == "17%–18%"


def test_one_month_windows_do_not_count_for_a_touch_claim():
    from tickercase.models import DataMode, PriceBaseRate
    from tickercase.oracle import build_oracle
    from datetime import datetime, timezone

    claim = validate_draft(ps_draft(price_condition="touch", horizon_years="0.0833"), today=FIXED_TODAY).claim
    rate = PriceBaseRate(symbol="X", window_months=1, windows=300, hits=150, rate=0.5, required_return=Decimal("0.2"), history_start=date(2000, 1, 1),
                         touch_hits=None, source_url="u", retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc), data_mode=DataMode.LIVE)
    o = build_oracle(claim, market=market(), options=None, base_rate=None, price_rate=rate, risk_free=Decimal("0.04"), today=FIXED_TODAY)
    m4 = next(m for m in o.methods if m.id == "M4")
    assert m4.touch_probability is None and any("只有一个月末" in lim.zh for lim in m4.limitations)
    assert o.low is None or abs(o.low - 0.5) > 1e-9  # not the bottom of the range


# ------------------------------------------------------------------ third review round


def test_the_latest_close_comes_from_the_quote_when_the_bar_has_none():
    # just after 00:00 UTC Yahoo sent the 10-28 bar with open/high/low but no close, and the run fell back to 10-27
    from tickercase.http_client import FakeHttpClient
    from tickercase.providers.market import CHART_URL, YahooChartProvider, daily_closes

    stamps = [1793021400, 1793107800, 1793194200]  # 10-26, 10-27, 10-28 at 09:30 New York
    payload = {"chart": {"result": [{
        "meta": {"currency": "USD", "gmtoffset": -14400, "regularMarketPrice": 58.6, "regularMarketTime": 1793217600},  # 10-28 16:00
        "timestamp": stamps,
        "indicators": {"quote": [{"open": [53.0, 54.1, 58.5], "high": [54.0, 59.18, 59.13], "low": [52.0, 53.57, 56.94], "close": [53.82, 58.31, None]}],
                       "adjclose": [{"adjclose": [53.82, 58.31, None]}]},
    }], "error": None}}
    url = CHART_URL.format(symbol="RKLB")
    snap = YahooChartProvider(FakeHttpClient({url: payload})).fetch_history("RKLB")
    assert snap.last_date == date(2026, 10, 28) and snap.last_close == Decimal("58.6")
    assert daily_closes(payload, url)[-1] == (date(2026, 10, 28), 58.6)
    # a quote time on another day than the empty bar does not fill it
    payload["chart"]["result"][0]["meta"]["regularMarketTime"] = 1793131200  # 10-27 16:00
    assert YahooChartProvider(FakeHttpClient({url: payload})).fetch_history("RKLB").last_date == date(2026, 10, 27)


def test_a_year_like_price_after_a_price_word_is_a_target():
    known = {"NVDA", "AZO", "TSLA", "COST"}.__contains__
    today = date(2026, 10, 29)
    azo = extract_claim("AZO 两年内涨到 2000", today=today, is_known_ticker=known)
    assert azo.target_price == Decimal("2000") and azo.horizon_years == Decimal("2")
    assert extract_claim("TSLA by 2030 reaches 2000", today=today, is_known_ticker=known).target_price == Decimal("2000")
    assert extract_claim("COST $1950 within 18 months", today=today, is_known_ticker=known).target_price == Decimal("1950")
    year = extract_claim("NVDA 2030年涨到 500", today=today, is_known_ticker=known)  # a year with 年 stays a year
    assert year.target_price == Decimal("500") and year.horizon_text.startswith("2030")
    assert extract_claim("NVDA 到 2030 会涨到 500", today=today, is_known_ticker=known).target_price == Decimal("500")


def test_next_month_and_next_week_are_horizons():
    known = {"NVDA"}.__contains__
    today = date(2026, 10, 29)
    assert extract_claim("英伟达下个月能涨到500吗", today=today, is_known_ticker=known).horizon_years == Decimal("0.0833")
    week = extract_claim("NVDA 下周到 220", today=today, is_known_ticker=known)
    assert week.horizon_years == Decimal("0.0192") and week.horizon_text == "下周"


def test_a_mis_scaled_company_is_left_out_of_the_base_rate():
    # Tigo Energy filed one year's revenue as 81,323,000 under one concept and 81,323,000,000 under the other
    from tickercase.http_client import FakeHttpClient
    from tickercase.oracle import band_note
    from tickercase.providers.sec_frames import FRAMES_URL, SecFramesBaseRateProvider

    def frame(rows):
        return {"data": [{"cik": c, "entityName": f"Co {c}", "val": v} for c, v in rows]}

    rfc = "RevenueFromContractWithCustomerExcludingAssessedTax"
    start = [(i, 100) for i in range(1, 41)]
    end = [(i, 300 if i <= 4 else 110) for i in range(1, 41)]
    responses = {
        FRAMES_URL.format(concept=rfc, year=2022): frame(start), FRAMES_URL.format(concept=rfc, year=2025): frame(end),
        FRAMES_URL.format(concept="Revenues", year=2022): frame([(1, 0.1)]),  # company 1: 1000x apart in the start year
        FRAMES_URL.format(concept="Revenues", year=2025): frame([(2, 300)]),  # company 2: agrees, stays
    }
    br = SecFramesBaseRateProvider(FakeHttpClient(responses)).base_rate(
        metric="annual_revenue", base_value=Decimal(100), required_cagr=Decimal("0.4"), end_year=2025, years=3)
    assert br.companies == 39 and br.achieved == 3 and br.dropped_mis_scaled == 1 and not br.band_widened
    assert "0.5–2 倍" in band_note(br).zh
    assert "0.25–4 倍" in band_note(br.model_copy(update={"band_widened": True})).zh


def test_small_chances_never_read_as_zero_or_minus_zero():
    assert ck.range_str(1e-6, 0.006) == "<0.1%–0.6%" and ck.range_str(0.17, 0.31) == "17%–31%"
    assert ck.range_str(1e-6, 0.0002) == "<0.1%" and ck.range_str(0.0, 0.0) == "0%"
    assert ck.pct_small(0.004) == "0.4%" and ck.num_small(1e-7) == "<0.1" and ck.num_small(0.006) == "0.6"
    assert ck.signed(-0.00001) == "0.0%" and ck.signed(0.0123) == "+1.2%"


def test_the_expected_year_may_come_from_the_sources_own_date():
    from tickercase.catalysts import _says_year
    from tickercase.models import CatalystSource

    later = CatalystSource(id="N01", kind="news", day=date(2026, 10, 21), text="Cybercab production starts later this year", url="u")
    nxt = CatalystSource(id="N02", kind="news", day=date(2026, 9, 25), text="volume production next year", url="u")
    assert _says_year(later, 2026) and not _says_year(later, 2027)
    assert _says_year(nxt, 2027) and not _says_year(nxt, 2028)


def _report(draft, revenue):
    from tickercase.report import build_report

    out = run(draft, facts(revenue, share_points=SHARES), market())
    claim = validate_draft(draft, today=FIXED_TODAY).claim
    rep = build_report(claim, calculate(claim), verdict=out.verdict, items=out.items, rechecks=out.rechecks, probability=None,
                       facts=facts(revenue, share_points=SHARES), market=market(), today=FIXED_TODAY)
    return out, rep


def test_short_claims_state_the_move_by_the_date_not_a_yearly_rate():
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]
    out, rep = _report(ps_draft(horizon_years="0.0833", target_price="60", valuation_multiple="20"), revenue)
    e6 = next(i for i in out.items if i.id == "E6")
    assert "每年" not in e6.detail_zh.split("；")[1] and "涨 20.00%" in e6.detail_zh
    price_row = next(r for layer in rep.layers for r in layer.rows if r.signal.zh == "股价")
    assert "不足一年" in price_row.meaning.zh and "每年" not in price_row.meaning.zh.split("；")[0]  # the 5-year history stays yearly
    assert all(r.signal.zh not in ("未来 3 个月", "下一个财年") for r in rep.time_view)  # both would fall after the deadline


def test_the_claims_path_ends_at_the_target_and_equal_multiples_make_one_row():
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]
    out, rep = _report(ps_draft(target_price="1500", valuation_multiple="20"), revenue)
    path = next(x for x in rep.scenarios if x.name.zh == "观点需要的路径")
    assert path.price == "1,500.00" and path.vs_target == "0.0%"
    today_mult = Decimal(next(i for i in out.items if i.id == "E2").measured["current_multiple"])
    _, rep2 = _report(ps_draft(target_price="1500", valuation_multiple=format(today_mult.quantize(Decimal("0.01")), "f")), revenue)
    names = [x.name.zh for x in rep2.scenarios]
    assert "延续过去增速 + 今天的估值（与观点假设相同）" in names and "延续过去增速 + 观点假设的估值" not in names
    assert not any("市场愿意给出" in u.zh for u in rep2.upside)  # no "expansion" to today's own multiple


def test_time_left_under_a_year_is_in_days():
    from tickercase.structure import _left_en, _left_zh

    assert _left_zh(0.17) == "62 天" and _left_zh(2.5) == "2.50 年" and _left_en(1 / 365.25) == "1 day"


def test_the_no_filings_note_reads_in_chinese():
    zh = note_zh("no 10-K, 10-Q filings found in the parsed range of the SEC response. Other form types present: 6-K (40). "
                 "This is a retrieval result for this window, not proof that no such filing exists.")
    assert zh.startswith("在 SEC 返回的范围里没有找到 10-K, 10-Q 申报。返回里有其他类型：6-K (40)。") and "proof" not in zh


def test_one_month_windows_have_no_touch_count_when_the_price_is_moved():
    from datetime import datetime, timezone

    from tickercase.models import DataMode, PriceBaseRate
    from tickercase.oracle import build_oracle
    from tickercase.structure import shifted_range

    claim = validate_draft(ps_draft(price_condition="touch", horizon_years="0.0833", target_price="60"), today=FIXED_TODAY).claim
    closes = [(date(2000 + i // 12, i % 12 + 1, 28), 40.0 + (i % 7)) for i in range(240)]
    rate = PriceBaseRate(symbol="X", window_months=1, windows=239, hits=10, rate=10 / 239, required_return=Decimal("0.2"), history_start=date(2000, 1, 1),
                         touch_hits=None, source_url="u", retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc), data_mode=DataMode.LIVE)
    o = build_oracle(claim, market=market(), options=None, base_rate=None, price_rate=rate, risk_free=Decimal("0.04"), today=FIXED_TODAY)
    moved = shifted_range(claim, o, 62.0, market=market(), options=None, base_rate=None, price_rate=rate, closes=closes,
                          risk_free=Decimal("0.04"), today=FIXED_TODAY)
    assert "M4" not in moved.methods and moved.low == 1.0  # above the target already: touched


# ------------------------------------------------------------------ fourth review round


def test_monthly_windows_are_months_even_when_yahoo_sends_quarters():
    # AMD's full history came back in quarterly bars, so "24 months" were partly 24 quarters
    from datetime import datetime, timezone

    from tickercase.providers.market import MONTHLY_URL, monthly_closes

    days = [date(2000, m, 1) for m in (1, 4, 7, 10)] + [date(2001, m, 1) for m in range(1, 13)]
    stamps = [int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) for d in days]
    payload = {"chart": {"result": [{"timestamp": stamps, "indicators": {"adjclose": [{"adjclose": [10.0 + i for i in range(len(days))]}]}}]}}
    closes = monthly_closes(payload, MONTHLY_URL.format(symbol="X"))
    assert [d for d, _ in closes] == days[4:]  # only the run of consecutive months, from 2001-01
    assert "period1=0" in MONTHLY_URL and "range=max" not in MONTHLY_URL


def test_funds_and_commodity_trusts_are_not_similar_companies():
    from tickercase.http_client import FakeHttpClient
    from tickercase.providers.sec_frames import FRAMES_URL, FUND_RE, SecFramesBaseRateProvider

    assert FUND_RE.search("ISHARES GOLD TRUST") and FUND_RE.search("SPDR GOLD TRUST") and FUND_RE.search("Grayscale Bitcoin Trust (BTC)")
    assert not FUND_RE.search("Vornado Realty Trust") and not FUND_RE.search("Northern Trust Corp") and not FUND_RE.search("Gold Fields Ltd")
    rfc = "RevenueFromContractWithCustomerExcludingAssessedTax"
    names = {1: "SPDR GOLD TRUST", 2: "iShares Gold Trust"}

    def frame(rows):
        return {"data": [{"cik": c, "entityName": names.get(c, f"Co {c}"), "val": v} for c, v in rows]}

    responses = {FRAMES_URL.format(concept=rfc, year=2022): frame([(i, 100) for i in range(1, 41)]),
                 FRAMES_URL.format(concept=rfc, year=2025): frame([(i, 300 if i <= 4 else 110) for i in range(1, 41)]),
                 FRAMES_URL.format(concept="Revenues", year=2022): frame([]), FRAMES_URL.format(concept="Revenues", year=2025): frame([])}
    br = SecFramesBaseRateProvider(FakeHttpClient(responses)).base_rate(
        metric="annual_revenue", base_value=Decimal(100), required_cagr=Decimal("0.4"), end_year=2025, years=3)
    assert br.dropped_funds == 2 and br.companies == 38 and br.achieved == 2


def test_probabilities_are_written_one_way():
    from tickercase import fmt

    assert fmt.prob(0.0) == "0.0%" and fmt.prob(1.0) == "100.0%"  # exact counts: 0 of 71, 69 of 69
    assert fmt.prob(0.0004) == "<0.1%" and fmt.prob(0.9996) == ">99.9%" and fmt.prob(0.006, 0) == "0.6%"
    assert fmt.prob_range(0.0, 0.0004) == "<0.1%" and fmt.prob_range(0.0, 0.159, 1) == "0.0%–15.9%"
    assert fmt.about("<0.1%") == "<0.1%" and fmt.about("18%") == "≈ 18%"


def test_the_required_growth_in_the_table_is_e1s_own():
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]
    claim = validate_draft(ps_draft(base_annual_metric="200000000", valuation_multiple="20"), today=FIXED_TODAY).claim
    out = run(ps_draft(base_annual_metric="200000000", valuation_multiple="20"), facts(revenue, share_points=SHARES), market())
    e1 = next(i for i in out.items if i.id == "E1")
    after = next(c for c in growth_from_base_end(calculate(claim), claim, out.items) if c.name == "required_metric_cagr")
    assert after.value == Decimal(e1.measured["required_cagr_from_reported"])  # 172.63% in one place and 172.62% in another before


def test_a_price_is_not_a_past_year_in_the_checks():
    from tickercase.validation import _claim_year_warnings

    assert _claim_year_warnings("AZO 两年内涨到 2000", Decimal(2), date(2026, 10, 29)) == []
    assert _claim_year_warnings("NVDA 2025年涨到 500", Decimal(2), date(2026, 10, 29))


def test_half_and_tenths_are_targets():
    known = {"NVDA"}.__contains__
    assert extract_claim("英伟达半年能涨一半吗", today=date(2026, 10, 29), is_known_ticker=known).target_multiple == Decimal("1.5")
    assert extract_claim("NVDA 一年内涨三成", today=date(2026, 10, 29), is_known_ticker=known).target_multiple == Decimal("1.3")


def _rep(draft, revenue, **kw):
    from tickercase.report import build_report

    out = run(draft, facts(revenue, share_points=SHARES), market())
    claim = validate_draft(draft, today=FIXED_TODAY).claim
    return build_report(claim, calculate(claim), verdict=out.verdict, items=out.items, rechecks=out.rechecks, probability=None,
                        facts=facts(revenue, share_points=SHARES), market=market(), today=FIXED_TODAY, **kw)


def test_insider_counts_over_part_of_the_filings_say_so():
    from tickercase.models import DataMode, InsiderSummary

    ins = InsiderSummary(window_start=date(2025, 10, 1), filings_listed=80, filings_read=40, sales=14, sale_value=Decimal("1420000000"),
                         sellers=3, data_mode=DataMode.LIVE)
    rep = _rep(ps_draft(valuation_multiple="20"), [annual(2022, 100_000_000), annual(2025, 200_000_000)], insiders=ins)
    assert any("读取的最近 40 份里" in h.zh and h.en.startswith("Insiders made no open-market purchases") for h in rep.headwinds)
    row = next(r for layer in rep.layers for r in layer.rows if r.signal.zh.startswith("内部人公开市场交易"))
    assert "只统计了 80 份中最近的 40 份" in row.data.zh


def test_the_price_history_says_how_many_years_it_covers():
    rep = _rep(ps_draft(valuation_multiple="20"), [annual(2022, 100_000_000), annual(2025, 200_000_000)])
    assert any(r.signal.zh == "过去 5 年股价" for layer in rep.layers for r in layer.rows)  # 2021-10-01 to 2026-09-30



def test_a_rate_that_rounds_to_zero_has_no_minus_sign():
    # AAPL's sensitivity table printed "-0.0%" for a required growth of -0.0004
    from tickercase.fmt import pct

    assert pct(Decimal("-0.0004")) == "0.0%" and pct(-0.00004, 2) == "0.00%" and pct(-0.0001, 1, True) == "0.0%"
    assert pct(Decimal("0.2245"), 2) == "22.45%" and pct(0.012, 1, True) == "+1.2%" and pct(Decimal("24598.82"), 2) == "2,459,882.00%"
