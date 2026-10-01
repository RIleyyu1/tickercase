"""SEC EDGAR filing-metadata adapter: ticker -> CIK -> submissions -> filing records.

Scope of this adapter
- Reads filing *metadata* (form, dates, accession number, primary document)
  from the SEC submissions API and builds official Archive links.
- Does not read filing documents, does not extract XBRL financials and does
  not judge whether a filing supports a claim.
- Only the ``filings.recent`` block is parsed. When the requested window starts
  before that block, the result reports a coverage gap and lists the older
  submission files SEC points to; those files are not fetched in this version.

Endpoints (https://www.sec.gov/search-filings/edgar-application-programming-interfaces):
- https://www.sec.gov/files/company_tickers.json
- https://data.sec.gov/submissions/CIK##########.json
Archive links follow https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/<file>.

Design reference: typed query/result objects, ticker->CIK resolution and an
injectable client follow komako-workshop/digital-oracle
digital_oracle/providers/edgar.py (commit a63e4c19a2f3313d54914c44666febaf5ffb9d6f, MIT).
New code; the reference's Form 4 listing is not used here.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from ..http_client import FetchedJson, JsonFetcher
from ..models import CoverageInfo, FilingRecord
from .base import Provider, ProviderError, ProviderParseError

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_FILE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

DEFAULT_FORMS = ("10-K", "10-Q", "8-K")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
RECENT_COLUMNS = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "primaryDocDescription")


@dataclass(frozen=True)
class SecFilingQuery:
    ticker: str
    forms: Sequence[str] = DEFAULT_FORMS
    include_amendments: bool = True
    since: Optional[date] = None
    max_records: int = 40


@dataclass
class SecFilingResult:
    ticker: str
    cik: str
    company_name: Optional[str]
    records: list[FilingRecord]
    coverage: CoverageInfo
    warnings: list[str] = field(default_factory=list)
    other_form_counts: dict[str, int] = field(default_factory=dict)
    submissions_url: str = ""


def archive_urls(cik: str, accession: str, primary_document: Optional[str]) -> tuple[Optional[str], str]:
    cik_int = str(int(cik))
    folder = accession.replace("-", "")
    base = f"{ARCHIVE_BASE}/{cik_int}/{folder}"
    doc = f"{base}/{primary_document}" if primary_document else None
    return doc, f"{base}/{accession}-index.htm"


def _parse_iso(value: Any) -> Optional[date]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


class SecFilingProvider(Provider):
    provider_id = "sec_edgar_submissions"
    display_name = "SEC EDGAR submissions"
    capabilities = ("ticker_to_cik", "filing_metadata")
    limitations = (
        "metadata only; documents are not read",
        "recent block only; older submission files are listed, not fetched",
        "no XBRL financial extraction",
    )

    def __init__(self, client: JsonFetcher):
        self.client = client
        self._ticker_map: Optional[dict[str, dict[str, Any]]] = None

    # ---------------------------------------------------------- ticker -> CIK

    def _load_ticker_map(self) -> dict[str, dict[str, Any]]:
        if self._ticker_map is not None:
            return self._ticker_map
        fetched = self.client.get_json(TICKERS_URL)
        data = fetched.payload
        if not isinstance(data, Mapping):
            raise ProviderParseError("parse_error", "company_tickers.json: expected a JSON object", url=TICKERS_URL)
        mapping: dict[str, dict[str, Any]] = {}
        for entry in data.values():
            if not isinstance(entry, Mapping):
                continue
            ticker = entry.get("ticker")
            cik = entry.get("cik_str")
            if not isinstance(ticker, str) or not ticker.strip():
                continue
            if isinstance(cik, bool) or not isinstance(cik, (int, str)) or not str(cik).strip().isdigit():
                continue
            mapping[ticker.strip().upper()] = {"cik": str(int(str(cik).strip())).zfill(10), "title": entry.get("title")}
        if not mapping:
            raise ProviderParseError("parse_error", "company_tickers.json contained no usable ticker entries", url=TICKERS_URL)
        self._ticker_map = mapping
        return mapping

    def resolve_cik(self, ticker: str) -> tuple[str, Optional[str]]:
        mapping = self._load_ticker_map()
        key = ticker.strip().upper()
        entry = mapping.get(key) or mapping.get(key.replace(".", "-"))
        if entry is None:
            raise ProviderError(
                "unknown_ticker",
                f"ticker '{ticker}' was not found in SEC company_tickers.json "
                "(the file lists current exchange tickers of SEC registrants; delisted or foreign-only symbols may be absent)",
                url=TICKERS_URL,
            )
        title = entry.get("title")
        return entry["cik"], title if isinstance(title, str) else None

    # ---------------------------------------------------------- submissions

    def fetch_filings(self, query: SecFilingQuery) -> SecFilingResult:
        cik, title = self.resolve_cik(query.ticker)
        url = SUBMISSIONS_URL.format(cik=cik)
        fetched = self.client.get_json(url)
        return self._parse_submissions(query, cik, title, fetched)

    def _parse_submissions(self, query: SecFilingQuery, cik: str, title: Optional[str], fetched: FetchedJson) -> SecFilingResult:
        url = fetched.url
        payload = fetched.payload
        if not isinstance(payload, Mapping):
            raise ProviderParseError("parse_error", "submissions: expected a JSON object", url=url)
        filings = payload.get("filings")
        if not isinstance(filings, Mapping):
            raise ProviderParseError("parse_error", "submissions.filings: expected an object", url=url)
        recent = filings.get("recent")
        if not isinstance(recent, Mapping):
            raise ProviderParseError("parse_error", "submissions.filings.recent: expected an object", url=url)
        columns: dict[str, list[Any]] = {}
        for name in RECENT_COLUMNS:
            value = recent.get(name, [])
            if not isinstance(value, list):
                raise ProviderParseError("parse_error", f"submissions.filings.recent.{name}: expected an array", url=url)
            columns[name] = value
        older_files_raw = filings.get("files", [])
        if not isinstance(older_files_raw, list):
            raise ProviderParseError("parse_error", "submissions.filings.files: expected an array", url=url)

        company_name = payload.get("name") if isinstance(payload.get("name"), str) else title
        warnings: list[str] = []

        n_rows = len(columns["form"])
        required_cols = ("accessionNumber", "filingDate")
        short = [name for name in RECENT_COLUMNS if len(columns[name]) < n_rows]
        if short:
            warnings.append(
                f"submissions arrays have unequal lengths ({', '.join(short)} shorter than form[{n_rows}]); "
                "rows without accession number or filing date were skipped, other missing cells left empty"
            )

        wanted: set[str] = set()
        for form in query.forms:
            base = form.strip().upper()
            wanted.add(base)
            if query.include_amendments:
                wanted.add(f"{base}/A")

        def cell(name: str, i: int) -> Any:
            col = columns[name]
            return col[i] if i < len(col) else None

        all_dates: list[date] = []
        other_forms: Counter[str] = Counter()
        records: list[FilingRecord] = []
        skipped = 0
        for i in range(n_rows):
            form = cell("form", i)
            filing_date = _parse_iso(cell("filingDate", i))
            if filing_date is not None:
                all_dates.append(filing_date)
            if not isinstance(form, str):
                skipped += 1
                continue
            form_u = form.strip().upper()
            if form_u not in wanted:
                other_forms[form_u] += 1
                continue
            accession = cell("accessionNumber", i)
            if any(i >= len(columns[c]) for c in required_cols) or not isinstance(accession, str) or not ACCESSION_RE.match(accession) or filing_date is None:
                skipped += 1
                continue
            if query.since is not None and filing_date < query.since:
                continue
            primary = cell("primaryDocument", i)
            primary = primary.strip() if isinstance(primary, str) and primary.strip() else None
            desc = cell("primaryDocDescription", i)
            doc_url, index_url = archive_urls(cik, accession, primary)
            records.append(
                FilingRecord(
                    provider_id=self.provider_id,
                    ticker=query.ticker.strip().upper(),
                    cik=cik,
                    company_name=company_name,
                    accession_number=accession,
                    form_type=form_u,
                    is_amendment=form_u.endswith("/A"),
                    filing_date=filing_date,
                    report_date=_parse_iso(cell("reportDate", i)),
                    primary_document=primary,
                    primary_document_description=desc.strip() if isinstance(desc, str) and desc.strip() else None,
                    document_url=doc_url,
                    filing_index_url=index_url,
                    source_response_url=url,
                    retrieved_at=fetched.retrieved_at,
                    source_captured_at=fetched.source_captured_at,
                    data_mode=fetched.data_mode,
                )
            )
        if skipped:
            warnings.append(f"{skipped} matching row(s) skipped because accession number, form or filing date was missing or malformed")

        records.sort(key=lambda r: (r.filing_date, r.accession_number), reverse=True)
        if len(records) > query.max_records:
            warnings.append(f"showing the {query.max_records} most recent of {len(records)} matching filings")
            records = records[: query.max_records]

        if not records:
            hint = ""
            if other_forms:
                top = ", ".join(f"{f} ({c})" for f, c in other_forms.most_common(5))
                hint = f" Other form types present: {top}."
            warnings.append(
                f"no {', '.join(sorted(wanted))} filings found in the parsed range of the SEC response.{hint} "
                "This is a retrieval result for this window, not proof that no such filing exists."
            )

        coverage = self._coverage(query.since, all_dates, older_files_raw)
        return SecFilingResult(
            ticker=query.ticker.strip().upper(),
            cik=cik,
            company_name=company_name,
            records=records,
            coverage=coverage,
            warnings=warnings,
            other_form_counts=dict(other_forms),
            submissions_url=url,
        )

    @staticmethod
    def _coverage(since: Optional[date], dates: list[date], older_files_raw: list[Any]) -> CoverageInfo:
        earliest = min(dates) if dates else None
        latest = max(dates) if dates else None
        older: list[dict[str, Any]] = []
        for item in older_files_raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                continue
            older.append(
                {
                    "name": item["name"],
                    "filing_from": item.get("filingFrom"),
                    "filing_to": item.get("filingTo"),
                    "filing_count": item.get("filingCount"),
                    "url": SUBMISSIONS_FILE_URL.format(name=item["name"]),
                }
            )
        span = f"{earliest} to {latest}" if earliest else "no dated rows"
        span_zh = f"{earliest} 至 {latest}" if earliest else "没有带日期的记录"
        if since is None:
            return CoverageInfo(
                requested_since=None, recent_earliest_filing_date=earliest, recent_latest_filing_date=latest,
                recent_row_count=len(dates), coverage_gap=False, older_files=older,
                message=f"No requested start date, so coverage was not checked. Parsed recent block: {span}.",
                message_zh=f"未填写检索起始日，未检查覆盖范围。已解析的近期记录：{span_zh}。",
            )
        before_recent = earliest is None or since < earliest
        if before_recent and older:
            relevant = [f for f in older if not isinstance(f.get("filing_to"), str) or f["filing_to"] >= since.isoformat()]
            return CoverageInfo(
                requested_since=since, recent_earliest_filing_date=earliest, recent_latest_filing_date=latest,
                recent_row_count=len(dates), coverage_gap=True, older_files=relevant or older,
                message=(
                    f"Coverage gap: the requested window starts {since}, but the parsed recent block covers {span}. "
                    f"SEC lists {len(older)} older submission file(s) that were not fetched, so filings before "
                    f"{earliest or 'the recent block'} were not checked."
                ),
                message_zh=(
                    f"覆盖缺口：检索起始日为 {since}，但已解析的近期记录只覆盖 {span_zh}。SEC 另列有 {len(older)} 个更早的申报文件未读取，"
                    f"因此 {earliest or '近期记录'} 之前的申报没有检查。"
                ),
            )
        if before_recent:
            return CoverageInfo(
                requested_since=since, recent_earliest_filing_date=earliest, recent_latest_filing_date=latest,
                recent_row_count=len(dates), coverage_gap=False, older_files=[],
                message=(
                    f"The requested window starts {since}; the SEC response covers {span} and lists no older "
                    f"submission files. Nothing earlier was returned by this source."
                ),
                message_zh=f"检索起始日为 {since}；SEC 返回的记录覆盖 {span_zh}，且没有列出更早的申报文件。",
            )
        return CoverageInfo(
            requested_since=since, recent_earliest_filing_date=earliest, recent_latest_filing_date=latest,
            recent_row_count=len(dates), coverage_gap=False, older_files=older,
            message=f"The requested window from {since} lies inside the parsed recent block ({span}).",
            message_zh=f"从 {since} 开始的检索范围在已解析的近期记录内（{span_zh}）。",
        )
