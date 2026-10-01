"""Shared test helpers. All SEC payloads built here are SYNTHETIC (hand-written), not network recordings."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from tickercase.config import Settings
from tickercase.http_client import FakeHttpClient
from tickercase.models import ClaimDraft, DataMode

ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
FIXED_TODAY = date(2026, 10, 1)
SYNTHETIC_DIR = ROOT / "examples" / "sec_synthetic_snapshots"


def ps_draft(**overrides) -> ClaimDraft:
    values = dict(
        claim_text="SYNT reaches $100 in five years",
        ticker="SYNT",
        currency="USD",
        target_price="100",
        reference_price="50",
        reference_price_date="2026-09-30",
        reference_price_source="synthetic",
        horizon_years="5",
        target_assumed_shares="100000000",
        valuation_method="price_to_sales",
        valuation_multiple="25",
        base_annual_metric="200000000",
        base_metric_currency="USD",
        base_metric_period="FY2025",
        filings_since="2025-01-01",
    )
    values.update(overrides)
    return ClaimDraft(**values)


def pe_draft(**overrides) -> ClaimDraft:
    base = dict(valuation_method="price_to_earnings", valuation_multiple="20", base_annual_metric="250000000")
    base.update(overrides)
    return ps_draft(**base)


def recent_block(rows):
    cols = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "primaryDocDescription")
    block = {c: [] for c in cols}
    for row in rows:
        for c, v in zip(cols, row):
            block[c].append(v)
    return block


def submissions_payload(rows, files=None, name="Synthetic Test Co"):
    return {"cik": "1234567", "name": name, "filings": {"recent": recent_block(rows), "files": files or []}}


TICKERS_PAYLOAD = {
    "0": {"cik_str": 1234567, "ticker": "TEST", "title": "Synthetic Test Co"},
    "1": {"cik_str": 7654321, "ticker": "BRK-B", "title": "Synthetic Dash Ticker Co"},
}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        sec_user_agent=None,
        sec_mode="synthetic",
        snapshot_dir=tmp_path / "snapshots",
        synthetic_dir=SYNTHETIC_DIR,
        case_dir=tmp_path / "cases",
        timeout_seconds=1.0,
        max_retries=1,
        min_interval_seconds=0.0,
        max_retry_after_seconds=5.0,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def offline_factory(settings: Settings):
    """Real fetchers for SEC (live mode still needs a User-Agent); market, options and web sources never touch the network."""
    from tickercase.service import build_fetcher

    def factory(mode: str, source: str):
        if source in ("market", "options", "web") and mode in ("live", "record"):
            return FakeHttpClient({}, data_mode=DataMode.LIVE)
        return build_fetcher(mode, settings, source)

    return factory
