from datetime import date

import pytest

from tickercase.http_client import FakeHttpClient, FetchError, ReplayHttpClient
from tickercase.models import DataMode
from tickercase.providers.base import ProviderError, ProviderParseError
from tickercase.providers.sec import SUBMISSIONS_URL, TICKERS_URL, SecFilingProvider, SecFilingQuery, archive_urls

from conftest import SYNTHETIC_DIR, TICKERS_PAYLOAD, submissions_payload

SUB_URL = SUBMISSIONS_URL.format(cik="0001234567")
ROWS = [
    ("0001234567-26-000010", "2026-08-01", "2026-06-30", "10-Q", "q2.htm", "10-Q"),
    ("0001234567-26-000009", "2026-05-01", "2026-03-31", "10-Q/A", "q1a.htm", "10-Q/A"),
    ("0001234567-26-000008", "2026-04-20", "2026-04-18", "8-K/A", "ek.htm", ""),
    ("0001234567-26-000007", "2026-03-01", "2025-12-31", "10-K", "k.htm", "10-K"),
    ("0001234567-26-000006", "2026-02-01", "", "4", "f4.xml", ""),
    ("0001234567-25-000005", "2025-06-01", "2025-05-30", "8-K", "", ""),
]


def provider(sub_payload, tickers=TICKERS_PAYLOAD):
    fake = FakeHttpClient({TICKERS_URL: tickers, SUB_URL: sub_payload})
    return SecFilingProvider(fake), fake


def test_happy_path_forms_amendments_and_links():
    p, fake = provider(submissions_payload(ROWS))
    r = p.fetch_filings(SecFilingQuery(ticker="test"))
    forms = [x.form_type for x in r.records]
    assert forms == ["10-Q", "10-Q/A", "8-K/A", "10-K", "8-K"]  # newest first, Form 4 excluded
    assert r.other_form_counts == {"4": 1}
    k = next(x for x in r.records if x.form_type == "10-K")
    assert k.cik == "0001234567" and k.accession_number == "0001234567-26-000007"
    assert k.document_url == "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000007/k.htm"
    assert k.filing_index_url == "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000007/0001234567-26-000007-index.htm"
    assert k.source_response_url == SUB_URL and k.report_date == date(2025, 12, 31)
    assert k.data_mode is DataMode.SYNTHETIC and k.provider_id == "sec_edgar_submissions"
    amend = next(x for x in r.records if x.form_type == "10-Q/A")
    assert amend.is_amendment
    eightk = next(x for x in r.records if x.form_type == "8-K")
    assert eightk.primary_document is None and eightk.document_url is None
    assert fake.calls == [TICKERS_URL, SUB_URL]


def test_amendments_can_be_excluded():
    p, _ = provider(submissions_payload(ROWS))
    r = p.fetch_filings(SecFilingQuery(ticker="TEST", include_amendments=False))
    assert all(not x.is_amendment for x in r.records)


def test_ticker_with_dot_maps_to_sec_dash():
    fake = FakeHttpClient({TICKERS_URL: TICKERS_PAYLOAD})
    assert SecFilingProvider(fake).resolve_cik("brk.b")[0] == "0007654321"


def test_unknown_ticker():
    p, _ = provider(submissions_payload(ROWS))
    with pytest.raises(ProviderError) as e:
        p.fetch_filings(SecFilingQuery(ticker="NOPE"))
    assert e.value.code == "unknown_ticker"


def test_empty_recent_block_is_not_reported_as_nonexistent():
    p, _ = provider(submissions_payload([]))
    r = p.fetch_filings(SecFilingQuery(ticker="TEST", since=date(2025, 1, 1)))
    assert r.records == []
    assert any("not proof that no such filing exists" in w for w in r.warnings)
    assert r.coverage.recent_row_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"filings": []},
        {"filings": {"recent": "x"}},
        {"filings": {"recent": {"form": "10-K"}}},
        {"filings": {"recent": {"form": []}, "files": {}}},
    ],
)
def test_wrong_types_raise_parse_error(payload):
    p, _ = provider(payload)
    with pytest.raises(ProviderParseError):
        p.fetch_filings(SecFilingQuery(ticker="TEST"))


@pytest.mark.parametrize("tickers", [[], {"0": "x"}, {"0": {"ticker": "TEST", "cik_str": "abc"}}])
def test_bad_ticker_file(tickers):
    p, _ = provider(submissions_payload(ROWS), tickers=tickers)
    with pytest.raises(ProviderParseError):
        p.fetch_filings(SecFilingQuery(ticker="TEST"))


def test_short_arrays_skip_incomplete_rows_without_inventing_values():
    payload = submissions_payload(ROWS)
    recent = payload["filings"]["recent"]
    recent["accessionNumber"] = recent["accessionNumber"][:2]
    recent["primaryDocument"] = recent["primaryDocument"][:1]
    p, _ = provider(payload)
    r = p.fetch_filings(SecFilingQuery(ticker="TEST"))
    assert [x.accession_number for x in r.records] == ["0001234567-26-000010", "0001234567-26-000009"]
    assert r.records[1].primary_document is None
    assert any("unequal lengths" in w for w in r.warnings)
    assert any("skipped" in w for w in r.warnings)


def test_malformed_rows_skipped():
    rows = [("bad-accession", "2026-01-01", "", "10-K", "a.htm", ""), ("0001234567-26-000001", "not-a-date", "", "10-K", "b.htm", "")]
    p, _ = provider(submissions_payload(rows))
    r = p.fetch_filings(SecFilingQuery(ticker="TEST"))
    assert r.records == [] and any("2 matching row(s) skipped" in w for w in r.warnings)


def test_coverage_gap_when_window_predates_recent_block():
    files = [{"name": "CIK0001234567-submissions-001.json", "filingFrom": "2010-01-01", "filingTo": "2025-05-31", "filingCount": 99}]
    p, _ = provider(submissions_payload(ROWS, files=files))
    r = p.fetch_filings(SecFilingQuery(ticker="TEST", since=date(2024, 1, 1)))
    assert r.coverage.coverage_gap is True
    assert r.coverage.recent_earliest_filing_date == date(2025, 6, 1)
    assert r.coverage.older_files[0]["url"] == "https://data.sec.gov/submissions/CIK0001234567-submissions-001.json"
    assert "were not checked" in r.coverage.message


def test_no_gap_inside_recent_block_and_filtering_by_since():
    files = [{"name": "old.json", "filingFrom": "2010-01-01", "filingTo": "2025-05-31"}]
    p, _ = provider(submissions_payload(ROWS, files=files))
    r = p.fetch_filings(SecFilingQuery(ticker="TEST", since=date(2026, 3, 1)))
    assert r.coverage.coverage_gap is False
    assert min(x.filing_date for x in r.records) >= date(2026, 3, 1)


def test_no_older_files_means_no_gap_but_honest_message():
    p, _ = provider(submissions_payload(ROWS))
    r = p.fetch_filings(SecFilingQuery(ticker="TEST", since=date(2020, 1, 1)))
    assert r.coverage.coverage_gap is False
    assert "Nothing earlier was returned by this source" in r.coverage.message


def test_max_records_limit():
    p, _ = provider(submissions_payload(ROWS))
    r = p.fetch_filings(SecFilingQuery(ticker="TEST", max_records=2))
    assert len(r.records) == 2 and any("most recent of 5" in w for w in r.warnings)


@pytest.mark.parametrize(
    "err",
    [
        FetchError("timeout", "t", url=SUB_URL),
        FetchError("forbidden", "f", url=SUB_URL, http_status=403),
        FetchError("rate_limited", "r", url=SUB_URL, http_status=429, retry_after_seconds=10),
        FetchError("server_error", "s", url=SUB_URL, http_status=503),
    ],
)
def test_network_errors_propagate_typed(err):
    fake = FakeHttpClient({TICKERS_URL: TICKERS_PAYLOAD, SUB_URL: err})
    with pytest.raises(FetchError) as e:
        SecFilingProvider(fake).fetch_filings(SecFilingQuery(ticker="TEST"))
    assert e.value is err


def test_bundled_synthetic_snapshots_replay_offline():
    p = SecFilingProvider(ReplayHttpClient(SYNTHETIC_DIR))
    r = p.fetch_filings(SecFilingQuery(ticker="SYNT", since=date(2024, 1, 1)))
    assert r.records and all(x.data_mode is DataMode.SYNTHETIC for x in r.records)
    assert all(x.source_captured_at is not None for x in r.records)
    assert r.coverage.coverage_gap is True
    empty = p.fetch_filings(SecFilingQuery(ticker="SYNX"))
    assert empty.records == []


def test_archive_urls_strip_leading_zeros():
    doc, idx = archive_urls("0000320193", "0000320193-24-000123", "aapl.htm")
    assert doc == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl.htm"
    assert idx.endswith("/0000320193-24-000123-index.htm")
