"""Deterministic evidence checks, verdict rules and recheck conditions. All data here is SYNTHETIC."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tickercase.analysis import analyze
from tickercase.calculations import calculate
from tickercase.models import DataMode, MarketSnapshot, MetricPoint, PricePoint, ReportedFacts
from tickercase.validation import validate_draft

from conftest import FIXED_TODAY, pe_draft, ps_draft

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def annual(year, value, filed=None):
    return MetricPoint(period_start=date(year, 1, 1), period_end=date(year, 12, 31), value=Decimal(value), unit="USD",
                       concept="Revenues", form="10-K", accession=f"0001234567-{(year + 1) % 100:02d}-000001",
                       filed=filed or date(year + 1, 2, 20))


def shares(day, value):
    return MetricPoint(period_end=day, value=Decimal(value), unit="shares", concept="dei:EntityCommonStockSharesOutstanding",
                       form="10-Q", accession="0001234567-26-000002", filed=day)


def facts(revenue=(), net_income=(), share_points=(), mode=DataMode.LIVE):
    return ReportedFacts(provider_id="sec_xbrl_companyfacts", ticker="SYNT", cik="0001234567", revenue=list(revenue),
                         net_income=list(net_income), shares_outstanding=list(share_points), source_url="https://example.org/f",
                         retrieved_at=T0, data_mode=mode)


def market(close="50", mode=DataMode.LIVE):
    return MarketSnapshot(provider_id="yahoo_finance_chart", symbol="SYNT", currency="USD", last_close=Decimal(close),
                          last_date=date(2026, 9, 30), history_start=date(2021, 10, 1), observations=1250,
                          annualized_volatility=Decimal("0.3"), volatility_window="w",
                          price_series=[PricePoint(day=date(2021, 10, 1), close=Decimal("30")), PricePoint(day=date(2026, 9, 30), close=Decimal(close))],
                          source_url="https://example.org/c", retrieved_at=T0, data_mode=mode)


SHARES = [shares(date(2023, 7, 28), 92_100_000), shares(date(2026, 7, 24), 95_000_000)]


def run(draft, f=None, m=None, filings=()):
    claim = validate_draft(draft, today=FIXED_TODAY).claim
    return analyze(claim, calculate(claim), facts=f, market=m, filings=list(filings), today=FIXED_TODAY)


def stances(outcome):
    return {i.id: i.stance for i in outcome.items}


def test_partially_supported_when_growth_gap_is_small():
    revenue = [annual(2022, 140_000_000), annual(2023, 160_000_000), annual(2024, 180_000_000), annual(2025, 200_000_000)]
    out = run(ps_draft(current_shares="95000000"), facts(revenue, [annual(2025, 40_000_000)], SHARES), market())
    s = stances(out)
    assert s["E1"] == "neutral" and s["E2"] == "neutral" and s["E3"] == "supporting" and s["E4"] == "neutral"
    assert out.verdict.label == "partially_supported"
    e1 = next(i for i in out.items if i.id == "E1")
    assert Decimal(e1.measured["required_cagr_from_reported"]) == pytest.approx(Decimal("0.148698354997035"), abs=Decimal("1e-12"))
    assert e1.measured["reported_window"] == "2022-12-31 to 2025-12-31"
    assert e1.sources[0].url.endswith("-index.htm")
    ids = {r.id for r in out.rechecks}
    assert {"R1", "R2", "R3", "R9"} <= ids


def test_supported_today_when_history_beats_requirement():
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]  # 26% a year > 14.9% required
    out = run(ps_draft(valuation_multiple="20"), facts(revenue, share_points=SHARES), market())
    assert stances(out)["E1"] == "supporting" and stances(out)["E2"] == "supporting"
    assert out.verdict.label == "supported_today"


def test_not_supported_when_gap_is_severe():
    ni = [annual(2022, 28_000_000), annual(2025, 40_000_000)]
    out = run(pe_draft(base_annual_metric="40000000"), facts(net_income=ni, share_points=SHARES), market())
    assert stances(out)["E1"] == "contrary"
    assert out.verdict.label == "not_supported_today"
    assert any("exceeds" in r for r in out.verdict.rationale)


def test_not_supported_when_growth_and_another_check_are_contrary():
    # P/S 40 -> required 250M, 8.7%/yr vs 1.0%/yr reported (gap 7.6 pp); today's P/S 28.8 -> 40 is 1.39x
    revenue = [annual(2022, 160_000_000), annual(2025, 165_000_000)]
    out = run(ps_draft(valuation_multiple="40"), facts(revenue, share_points=SHARES), market())
    s = stances(out)
    assert s["E1"] == "contrary" and s["E2"] == "contrary"
    assert out.verdict.label == "not_supported_today"


def test_partially_supported_when_only_growth_is_moderately_contrary():
    # P/S 30 -> required 333M, 15.1%/yr vs 1.0%/yr reported (gap 14 pp, below the 15 pp limit); 30 is 1.04x today's 28.8
    revenue = [annual(2022, 160_000_000), annual(2025, 165_000_000)]
    out = run(ps_draft(valuation_multiple="30"), facts(revenue, share_points=SHARES), market())
    assert stances(out)["E1"] == "contrary" and stances(out)["E2"] == "neutral"
    assert out.verdict.label == "partially_supported"


def test_net_loss_base_is_contrary_and_not_supported():
    out = run(pe_draft(), facts(net_income=[annual(2024, 10_000_000), annual(2025, -5_000_000)], share_points=SHARES), market())
    assert stances(out)["E1"] == "contrary" and stances(out)["E2"] == "neutral"
    assert out.verdict.label == "not_supported_today"


def test_missing_data_gives_insufficiently_specified_and_recheck_for_each_gap():
    out = run(ps_draft())
    assert set(stances(out).values()) == {"missing"}
    assert out.verdict.label == "insufficiently_specified"
    assert {"R-E1", "R-E2", "R-E3", "R-E5"} <= {r.id for r in out.rechecks}


def test_single_year_history_is_missing_not_contrary():
    out = run(ps_draft(), facts([annual(2025, 200_000_000)], share_points=SHARES), market())
    assert stances(out)["E1"] == "missing" and out.verdict.label == "insufficiently_specified"


def test_aggressive_buyback_assumption_is_contrary():
    revenue = [annual(2022, 100_000_000), annual(2025, 200_000_000)]
    out = run(ps_draft(target_assumed_shares="60000000", valuation_multiple="20"), facts(revenue, share_points=SHARES), market())
    assert stances(out)["E3"] == "contrary"
    assert out.verdict.label == "partially_supported"


def test_non_usd_claim_cannot_be_compared():
    out = run(ps_draft(currency="EUR", base_metric_currency="EUR"), facts([annual(2022, 1), annual(2025, 2)], share_points=SHARES), market())
    assert stances(out)["E1"] == "missing" and stances(out)["E2"] == "missing"


def test_input_cross_checks_warn():
    revenue = [annual(2022, 140_000_000), annual(2025, 200_000_000)]
    out = run(ps_draft(reference_price="40", base_annual_metric="250000000", current_shares="80000000"),
              facts(revenue, share_points=SHARES), market())
    text = " ".join(out.warnings)
    assert "reference price" in text and "base revenue" in text and "share count" in text


def test_stale_filings_are_missing_and_synthetic_data_is_disclosed():
    old = [annual(2022, 140_000_000, filed=date(2023, 2, 1)), annual(2024, 190_000_000, filed=date(2025, 2, 1))]
    out = run(ps_draft(), facts(old, mode=DataMode.SYNTHETIC), market(mode=DataMode.SYNTHETIC))
    assert stances(out)["E5"] == "missing"
    assert out.verdict.limitations[0].startswith("Synthetic example data")


def test_verdict_text_never_claims_impossibility_or_guarantee():
    out = run(pe_draft(), facts(net_income=[annual(2022, 28_000_000), annual(2025, 40_000_000)]), market())
    joined = " ".join(out.verdict.limitations)
    assert "does not mean the target price is impossible" in joined and "not a guarantee" in joined
