"""Opt-in live SEC check. Skipped unless TICKERCASE_RUN_LIVE=1 and SEC_USER_AGENT is set.

For a recorded run with an output file use scripts/live_smoke.py instead.
"""

import os

import pytest

from tickercase.http_client import LiveHttpClient
from tickercase.models import DataMode
from tickercase.providers.sec import SecFilingProvider, SecFilingQuery

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("TICKERCASE_RUN_LIVE") != "1" or not os.environ.get("SEC_USER_AGENT"),
    reason="live SEC test disabled (set TICKERCASE_RUN_LIVE=1 and SEC_USER_AGENT)",
)
def test_live_sec_filings():
    client = LiveHttpClient(user_agent=os.environ["SEC_USER_AGENT"], min_interval_seconds=0.2)
    result = SecFilingProvider(client).fetch_filings(SecFilingQuery(ticker=os.environ.get("TICKERCASE_LIVE_TICKER", "AAPL")))
    assert result.cik.isdigit() and len(result.cik) == 10
    assert result.records, result.warnings
    assert all(r.data_mode is DataMode.LIVE for r in result.records)
