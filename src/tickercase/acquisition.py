"""A2 Acquire Dated Evidence.

Function ID: A2 (first-level function of A0 Evaluate a Stock Claim, Innoslate project 650)
Performed by: C.2 Evidence Acquisition Subsystem
Requirement:  SR.1.2 "TickerCase shall acquire the evidence set for the confirmed claim from the public sources."

The signature of ``acquire_dated_evidence`` is the ICOM of the A2 box on the A0 IDEF0 diagram:

    I  IO.5   Confirmed Claim                          confirmed_claim
    I  IO.7   Source Evidence and Reference Snapshot   source_evidence (the responses come in through it)
    C  C2     Provider Access Terms                    provider_access_terms
    C  C3     Data Mode Setting                        data_mode_setting
    O  IO.6   Evidence Query                           AcquiredEvidence.evidence_query
    O  IO.29  Evidence Set                             AcquiredEvidence.evidence_set
    O  IO.30  Source Error Records                     AcquiredEvidence.source_error_records
    M         C.2 Evidence Acquisition Subsystem       this module

Second-level functions (A2 IDEF0) and the components that perform them:

    A2.1 Request Filings and Reported Facts   C.2.1 SEC EDGAR Client                  request_filings_and_reported_facts
    A2.2 Request Market and Option Data       C.2.2 Market Data Client                request_market_and_option_data
    A2.3 Request Context Signals              C.2.3 Context Signal Client             request_context_signals
    A2.4 Stamp Provenance                     C.2.4 HTTP Fetcher and Snapshot Store   stamp_provenance (+ http_client.py)
    A2.5 Isolate Source Failures              C.2.5 Source Guard                      SourceGuard

Internal flows of the A2 diagram: IO.37 Filing Records (A2.1 -> A2.4), IO.38 Market Records (A2.2 -> A2.4) and
IO.39 Context Records (A2.3 -> A2.4). Each is a model below and an operation in contracts/api-v1.yaml.

Each source fails on its own and is reported as a source error record; no other source is substituted for a failed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional, TypeVar

from pydantic import BaseModel, Field

from .calculations import deadline_from
from .config import SEC_MODES, Settings
from .http_client import FetchError, JsonFetcher, LiveHttpClient, RecordingHttpClient, ReplayHttpClient, utcnow
from .models import (
    BaseRate,
    BenchmarkReturn,
    ConfirmedClaim,
    CoverageInfo,
    DataMode,
    DecimalStr,
    EarningsDate,
    FilingRecord,
    InsiderSummary,
    MarketSnapshot,
    NewsFeed,
    OptionsSnapshot,
    PredictionMarkets,
    PriceBaseRate,
    ProviderErrorRecord,
    ReportedFacts,
    SentimentSnapshot,
)
from .providers.base import ProviderError
from .providers.calendar import EarningsCalendarProvider
from .providers.insiders import InsiderProvider
from .providers.market import CHART_URL, MONTHLY_URL, YahooChartProvider, monthly_closes, price_base_rate, price_touch_hits, yahoo_symbol
from .providers.news import NewsProvider, company_query_name
from .providers.options import YahooOptionsProvider
from .providers.sec import SecFilingProvider, SecFilingQuery
from .providers.sec_facts import SecCompanyFactsProvider
from .providers.sec_frames import SecFramesBaseRateProvider
from .providers.sentiment import FearGreedProvider, PolymarketProvider
from .providers.yahoo_auth import BROWSER_UA, YahooAuthedClient

FUNCTION_ID = "A2"
COMPONENT_ID = "C.2"

SEC_PROVIDER_ID = SecFilingProvider.provider_id
FACTS_PROVIDER_ID = SecCompanyFactsProvider.provider_id
MARKET_PROVIDER_ID = YahooChartProvider.provider_id
OPTIONS_PROVIDER_ID = YahooOptionsProvider.provider_id
SOURCES = ("sec", "market", "options", "web")
BENCHMARKS = (("SPY", "S&P 500 (SPY)"), ("QQQ", "Nasdaq-100 (QQQ)"))

T = TypeVar("T")

#: I IO.7 — called as ``source_evidence(data_mode_setting, provider_access_terms, source)``; returns the fetcher of one source.
SourceEvidence = Callable[[str, Settings, str], JsonFetcher]


# ---------------------------------------------------------------- C.2.4 HTTP Fetcher and Snapshot Store


def build_fetcher(mode: str, settings: Settings, source: str = "sec") -> JsonFetcher:
    """The fetcher of one source under the data mode (C3) and the provider access terms (C2).

    The terms are the declared User-Agent, the request spacing, the retry limits and the cache lifetimes of each source.
    """
    if mode not in SEC_MODES:
        raise ValueError(f"unknown data mode '{mode}'; expected one of {SEC_MODES}")
    if source not in SOURCES:
        raise ValueError(f"unknown source '{source}'; expected one of {SOURCES}")
    if mode == "synthetic":
        return ReplayHttpClient(settings.synthetic_dir)
    if mode == "replay":
        return ReplayHttpClient(settings.snapshot_dir)
    if source == "options":
        authed = YahooAuthedClient(timeout_seconds=max(settings.timeout_seconds, 15.0))
        return RecordingHttpClient(authed, settings.snapshot_dir) if mode == "record" else authed
    if source == "web":
        live = LiveHttpClient(
            user_agent=BROWSER_UA, require_contact_email=False, service_name="CNN",
            timeout_seconds=settings.timeout_seconds, max_retries=settings.max_retries,
            min_interval_seconds=settings.market_min_interval_seconds, max_retry_after_seconds=settings.max_retry_after_seconds,
            extra_headers={"Referer": "https://edition.cnn.com/"}, cache_ttl_seconds={"https://production.dataviz.cnn.io/": 600},
        )
    elif source == "market":
        live = LiveHttpClient(
            user_agent=settings.market_user_agent,
            require_contact_email=False,
            service_name="Yahoo Finance",
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            min_interval_seconds=settings.market_min_interval_seconds,
            max_retry_after_seconds=settings.max_retry_after_seconds,
            cache_ttl_seconds={CHART_URL.split("{")[0]: 300, "https://gamma-api.polymarket.com/": 300},
        )
    else:
        live = LiveHttpClient(
            user_agent=settings.sec_user_agent,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            min_interval_seconds=settings.min_interval_seconds,
            max_retry_after_seconds=settings.max_retry_after_seconds,
            cache_ttl_seconds={
                "https://www.sec.gov/files/company_tickers.json": 24 * 3600,
                "https://data.sec.gov/submissions/": 600,
                "https://data.sec.gov/api/xbrl/companyfacts/": 3600,
                "https://data.sec.gov/api/xbrl/frames/": 24 * 3600,
                "https://www.sec.gov/Archives/": 24 * 3600,
            },
        )
    if mode == "record":
        return RecordingHttpClient(live, settings.snapshot_dir)
    return live


def mode_enum(mode: str) -> Optional[DataMode]:
    return {"live": DataMode.LIVE, "record": DataMode.LIVE, "replay": DataMode.REPLAY, "synthetic": DataMode.SYNTHETIC}.get(mode)


def mode_of(present: bool, modes: set[str], data_mode_setting: str) -> str:
    """The data-mode stamp of one group of records: the modes its records carry, or "unavailable"."""
    if not present:
        return "unavailable"
    if modes:
        return ",".join(sorted(modes))
    setting = mode_enum(data_mode_setting)
    return setting.value if setting else "unavailable"


class _QueryLog:
    """A fetcher that writes every requested URL to the Evidence Query (O IO.6) before it asks the source."""

    def __init__(self, fetcher: JsonFetcher, log: list[str]):
        self._fetcher = fetcher
        self._log = log

    def get_json(self, url: str):
        self._log.append(url)
        return self._fetcher.get_json(url)

    def __getattr__(self, name: str):
        return getattr(self._fetcher, name)


class SourceGateway:
    """The one door to the public sources: every request leaves, and every response enters, through here."""

    def __init__(self, source_evidence: SourceEvidence, data_mode_setting: str, provider_access_terms: Settings):
        self._open = source_evidence
        self.data_mode_setting = data_mode_setting
        self.provider_access_terms = provider_access_terms
        self.evidence_query: list[str] = []
        self._fetchers: dict[str, _QueryLog] = {}
        self._guarded: dict[str, Optional[JsonFetcher]] = {}

    def __call__(self, source: str) -> JsonFetcher:
        if source not in self._fetchers:
            fetcher = self._open(self.data_mode_setting, self.provider_access_terms, source)
            self._fetchers[source] = _QueryLog(fetcher, self.evidence_query)
        return self._fetchers[source]

    def guarded(self, source: str, provider_id: str, guard: "SourceGuard") -> Optional[JsonFetcher]:
        """The fetcher of a source, or None when it cannot be opened; the failure is recorded once."""
        if source not in self._guarded:
            self._guarded[source] = guard.call(provider_id, lambda: self(source))
        return self._guarded[source]


# ---------------------------------------------------------------- A2.5 Isolate Source Failures (C.2.5 Source Guard)


def isolate(provider_id: str, mode: str, errors: list[ProviderErrorRecord], call: Callable[[], T]) -> Optional[T]:
    """Run one source request; a failure becomes a source error record (O IO.30) and None is returned."""
    try:
        return call()
    except FetchError as exc:
        errors.append(ProviderErrorRecord(
            provider_id=provider_id, code=exc.code, message=exc.message, url=exc.url, http_status=exc.http_status,
            retry_after_seconds=exc.retry_after_seconds, data_mode=exc.data_mode or mode_enum(mode),
        ))
    except ProviderError as exc:
        errors.append(ProviderErrorRecord(provider_id=provider_id, code=exc.code, message=exc.message, url=exc.url, data_mode=mode_enum(mode)))
    except ValueError as exc:
        errors.append(ProviderErrorRecord(provider_id=provider_id, code="config_error", message=str(exc)))
    return None


class SourceGuard:
    """A2.5 Isolate Source Failures, performed by C.2.5 Source Guard (SR.1.2.2, SS.2.5, CR.2.5.1).

    Wraps every source request. One failed request costs only itself: the remaining requests still run.
    """

    def __init__(self, data_mode_setting: str, progress: Optional[Callable[[str, str], None]] = None):
        self.data_mode_setting = data_mode_setting
        self.source_error_records: list[ProviderErrorRecord] = []
        self.steps: dict[str, str] = {}
        self._progress = progress or (lambda step, state: None)

    def call(self, provider_id: str, call: Callable[[], T]) -> Optional[T]:
        return isolate(provider_id, self.data_mode_setting, self.source_error_records, call)

    def step(self, name: str, provider_id: str, call: Callable[[], T]) -> Optional[T]:
        """A named request: reported to the page as running, then ok or failed."""
        self._progress(name, "running")
        before = len(self.source_error_records)
        value = self.call(provider_id, call)
        self.steps[name] = "ok" if value is not None and len(self.source_error_records) == before else "failed"
        self._progress(name, self.steps[name])
        return value

    def settle(self, name: str) -> None:
        """Take back the last failure of a step that turned out to be an answer (an empty calendar is not an outage)."""
        self.source_error_records.pop()
        self.steps[name] = "ok"
        self._progress(name, "ok")


# ---------------------------------------------------------------- internal flows of the A2 diagram


class FilingRecords(BaseModel):
    """IO.37 Filing Records: A2.1 -> A2.4."""

    found: bool = False  # the filing service answered
    records: list[FilingRecord] = Field(default_factory=list)
    coverage: Optional[CoverageInfo] = None
    warnings: list[str] = Field(default_factory=list)
    reported_facts: Optional[ReportedFacts] = None
    insiders: Optional[InsiderSummary] = None


class MarketRecords(BaseModel):
    """IO.38 Market Records: A2.2 -> A2.4."""

    price_history: Optional[MarketSnapshot] = None
    options: Optional[OptionsSnapshot] = None
    risk_free_rate: Optional[DecimalStr] = None
    price_base_rate: Optional[PriceBaseRate] = None
    benchmarks: Optional[list[BenchmarkReturn]] = None
    earnings: Optional[EarningsDate] = None


class ContextRecords(BaseModel):
    """IO.39 Context Records: A2.3 -> A2.4. Context only: no verdict rule and no counted method reads them (SR.1.2.5)."""

    label: str = "context_only"
    prediction_markets: Optional[PredictionMarkets] = None
    sentiment: Optional[SentimentSnapshot] = None
    news: Optional[NewsFeed] = None


class EvidenceSet(BaseModel):
    """IO.29 Evidence Set: the output of A2.4, read by A3, A4, A5 and A6."""

    filing: FilingRecords = Field(default_factory=FilingRecords)
    market: MarketRecords = Field(default_factory=MarketRecords)
    context: ContextRecords = Field(default_factory=ContextRecords)
    data_modes: dict[str, str] = Field(default_factory=dict)  # the data-mode stamp of each group of records


@dataclass
class AcquiredEvidence:
    """The three outputs of A2, with the guard and the gateway for the follow-up requests of the same case."""

    evidence_query: list[str]                           # O IO.6
    evidence_set: EvidenceSet                           # O IO.29
    source_error_records: list[ProviderErrorRecord]     # O IO.30
    guard: SourceGuard
    sources: SourceGateway
    notes: list[tuple[str, str]] = field(default_factory=list)  # (English, Chinese) notes for the case


# ---------------------------------------------------------------- A2.1 Request Filings and Reported Facts (C.2.1)


def request_filings_and_reported_facts(confirmed_claim: ConfirmedClaim, sources: SourceGateway, guard: SourceGuard, *,
                                       today: date) -> FilingRecords:
    """A2.1 Request Filings and Reported Facts, performed by C.2.1 SEC EDGAR Client (SR.1.2.3, SS.2.1).

    I: Confirmed Claim; Source Evidence.  C: Provider Access Terms; Data Mode Setting.  O: Evidence Query; Filing Records.
    """
    claim = confirmed_claim.values
    out = FilingRecords()
    sec = sources.guarded("sec", SEC_PROVIDER_ID, guard)
    if sec is None:
        return out
    provider = SecFilingProvider(sec)
    filings = guard.step("sec_filings", SEC_PROVIDER_ID, lambda: provider.fetch_filings(
        SecFilingQuery(ticker=claim.ticker, since=claim.filings_since)))
    if filings is not None:
        out.found, out.records, out.coverage, out.warnings = True, filings.records, filings.coverage, list(filings.warnings)
    out.reported_facts = guard.step("sec_facts", FACTS_PROVIDER_ID, lambda: SecCompanyFactsProvider(sec, provider).fetch_facts(claim.ticker))
    out.insiders = guard.step("insiders", InsiderProvider.provider_id, lambda: InsiderProvider(sec, provider).summary(claim.ticker, today=today))
    return out


def request_peer_base_rate(sources: SourceGateway, guard: SourceGuard, *, metric: str, base_value: Decimal, required_cagr: Decimal,
                           end_year: int, years: int, exclude_cik: int) -> Optional[BaseRate]:
    """A follow-up request of C.2.1: the peer sample of the fundamentals base rate, asked for once A3 has the required growth."""
    sec = sources.guarded("sec", SEC_PROVIDER_ID, guard)
    if sec is None:
        return None
    return guard.step("base_rate", SecFramesBaseRateProvider.provider_id, lambda: SecFramesBaseRateProvider(sec).base_rate(
        metric=metric, base_value=base_value, required_cagr=required_cagr, end_year=end_year, years=years, exclude_cik=exclude_cik))


# ---------------------------------------------------------------- A2.2 Request Market and Option Data (C.2.2)


def fetch_price_base_rate(fetcher: JsonFetcher, claim, months: int, spot: Decimal) -> PriceBaseRate:
    url = MONTHLY_URL.format(symbol=yahoo_symbol(claim.ticker))
    fetched = fetcher.get_json(url)
    closes = monthly_closes(fetched.payload, url)
    required = claim.target_price / spot - 1
    windows, hits, med, best = price_base_rate(closes, months, required)
    touch_hits = price_touch_hits(closes, months, required) if months >= 2 else None
    return PriceBaseRate(
        symbol=yahoo_symbol(claim.ticker), window_months=months, windows=windows, hits=hits, rate=hits / windows if windows else None,
        required_return=required.quantize(Decimal("0.0001")), median_return=Decimal(str(round(med, 4))) if med is not None else None,
        best_return=Decimal(str(round(best, 4))) if best is not None else None, history_start=closes[0][0] if closes else date.today(),
        touch_hits=touch_hits,
        source_url=url, retrieved_at=fetched.retrieved_at, source_captured_at=fetched.source_captured_at, data_mode=fetched.data_mode)


def fetch_benchmarks(fetcher: JsonFetcher, months: int) -> list[BenchmarkReturn]:
    out = []
    for symbol, label in BENCHMARKS:
        url = MONTHLY_URL.format(symbol=symbol)
        fetched = fetcher.get_json(url)
        closes = monthly_closes(fetched.payload, url)
        n = min(months, len(closes) - 1)
        if n < 1:
            continue
        (d0, p0), (d1, p1) = closes[-1 - n], closes[-1]
        # a monthly bar is stamped on its first day but closes on its last: the span starts at that month's end
        start = (date(d0.year + (d0.month == 12), d0.month % 12 + 1, 1) - timedelta(days=1)) if d0.day == 1 else d0
        years = (d1 - start).days / 365.25
        total = Decimal(str(p1 / p0 - 1))
        annual = Decimal(str((p1 / p0) ** (1 / years) - 1)) if years > 0.05 else total
        out.append(BenchmarkReturn(symbol=symbol, label=label, start=start, end=d1, total_return=total.quantize(Decimal("0.0001")),
                                   annual_return=annual.quantize(Decimal("0.0001")), data_mode=fetched.data_mode))
    return out


def request_market_and_option_data(confirmed_claim: ConfirmedClaim, sources: SourceGateway, guard: SourceGuard, *,
                                   today: date, notes: Optional[list[tuple[str, str]]] = None) -> MarketRecords:
    """A2.2 Request Market and Option Data, performed by C.2.2 Market Data Client (SR.1.2.4, SS.2.2).

    I: Confirmed Claim; Source Evidence.  C: Provider Access Terms; Data Mode Setting.  O: Evidence Query; Market Records.
    """
    claim = confirmed_claim.values
    out = MarketRecords()
    out.price_history = guard.step("price_history", MARKET_PROVIDER_ID, lambda: YahooChartProvider(sources("market")).fetch_history(claim.ticker))
    target_date = deadline_from(claim.reference_price_date, claim.horizon_years)
    out.options = guard.step("options", OPTIONS_PROVIDER_ID, lambda: YahooOptionsProvider(sources("options")).fetch_chain(
        claim.ticker, target_date, claim.target_price, today=today))
    market = sources.guarded("market", MARKET_PROVIDER_ID, guard)
    if market is not None:
        tnx = guard.step("risk_free_rate", MARKET_PROVIDER_ID, lambda: YahooChartProvider(market).fetch_history("^TNX"))
        out.risk_free_rate = (tnx.last_close / 100).quantize(Decimal("0.0001")) if tnx is not None else None
        spot = out.price_history.last_close if out.price_history is not None else claim.reference_price
        months = max(1, round(float(claim.horizon_years) * 12))
        out.price_base_rate = guard.step("price_base_rate", MARKET_PROVIDER_ID, lambda: fetch_price_base_rate(market, claim, months, spot))
        out.benchmarks = guard.step("benchmarks", MARKET_PROVIDER_ID, lambda: fetch_benchmarks(market, months))
    out.earnings = guard.step("earnings_date", EarningsCalendarProvider.provider_id, lambda: EarningsCalendarProvider(
        sources("options")).next_earnings(claim.ticker, today=today))
    errors = guard.source_error_records
    if out.earnings is None and errors and errors[-1].code == "no_earnings_date":
        # the calendar answered, it just has no date yet: a note, not a failed source (CR.2.3.2)
        guard.settle("earnings_date")
        if notes is not None:
            notes.append(("Yahoo's calendar lists no upcoming earnings date yet", "Yahoo 日历暂未列出下一次财报日期"))
    if out.options is not None and out.earnings is not None:  # the expiries right around the earnings day (CR.2.2.3)
        try:
            out.options = YahooOptionsProvider(sources("options")).around(out.options, [out.earnings.day])
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- A2.3 Request Context Signals (C.2.3)


def company_name(filing: FilingRecords, ticker: str) -> str:
    facts = filing.reported_facts
    name = (facts.company_name if facts is not None else None) or next((r.company_name for r in filing.records if r.company_name), None)
    return company_query_name(name, ticker)


def request_context_signals(confirmed_claim: ConfirmedClaim, sources: SourceGateway, guard: SourceGuard, *,
                            company: str, now: datetime) -> ContextRecords:
    """A2.3 Request Context Signals, performed by C.2.3 Context Signal Client (SR.1.2.5, SS.2.3).

    I: Confirmed Claim; Source Evidence.  C: Provider Access Terms; Data Mode Setting.  O: Evidence Query; Context Records.
    """
    claim = confirmed_claim.values
    out = ContextRecords()
    market = sources.guarded("market", MARKET_PROVIDER_ID, guard)
    if market is not None:
        out.prediction_markets = guard.step("prediction_markets", PolymarketProvider.provider_id,
                                            lambda: PolymarketProvider(market).search(claim.ticker, now=now))
    out.sentiment = guard.step("fear_greed", FearGreedProvider.provider_id, lambda: FearGreedProvider(sources("web")).snapshot())
    if market is not None:
        out.news = guard.step("news", NewsProvider.provider_id, lambda: NewsProvider(market).recent(claim.ticker, company))
    return out


# ---------------------------------------------------------------- A2.4 Stamp Provenance (C.2.4)


def stamp_provenance(filing_records: FilingRecords, market_records: MarketRecords, context_records: ContextRecords, *,
                     data_mode_setting: str) -> EvidenceSet:
    """A2.4 Stamp Provenance, performed by C.2.4 HTTP Fetcher and Snapshot Store (SR.1.2.1, SS.2.4).

    I: Filing Records; Market Records; Context Records.  C: Data Mode Setting.  O: Evidence Set.

    The fetcher (http_client.py) stamps each response with its URL, capture time and data mode, and each record carries
    that stamp. This step assembles the Evidence Set and states the data mode of every group of records, so a case that
    mixes modes, or misses a source, says so.
    """
    f, m, c = filing_records, market_records, context_records

    def stamp(record) -> str:
        return mode_of(record is not None, {record.data_mode.value} if record is not None else set(), data_mode_setting)

    data_modes = {
        "sec_filings": mode_of(f.found, {r.data_mode.value for r in f.records}, data_mode_setting),
        "sec_facts": stamp(f.reported_facts),
        "market_prices": stamp(m.price_history),
        "options": stamp(m.options),
        "insiders": stamp(f.insiders),
        "fear_greed": stamp(c.sentiment),
        "news": stamp(c.news),
        "earnings_date": stamp(m.earnings),
    }
    return EvidenceSet(filing=f, market=m, context=c, data_modes=data_modes)


# ---------------------------------------------------------------- A2 Acquire Dated Evidence (C.2)


def acquire_dated_evidence(
    confirmed_claim: ConfirmedClaim,                    # I  IO.5  Confirmed Claim
    source_evidence: SourceEvidence = build_fetcher,    # I  IO.7  Source Evidence and Reference Snapshot
    *,
    data_mode_setting: str,                             # C  C3    Data Mode Setting
    provider_access_terms: Settings,                    # C  C2    Provider Access Terms
    today: Optional[date] = None,
    now: Optional[datetime] = None,
    progress: Optional[Callable[[str, str], None]] = None,
) -> AcquiredEvidence:                                  # O  IO.6 Evidence Query, IO.29 Evidence Set, IO.30 Source Error Records
    """A2 Acquire Dated Evidence, performed by C.2 Evidence Acquisition Subsystem (SR.1.2).

    Runs A2.1 to A2.5 for one confirmed claim. ``progress(step, state)`` is called with "running", then "ok" or "failed".
    """
    now = now or utcnow()
    today = today or now.date()
    sources = SourceGateway(source_evidence, data_mode_setting, provider_access_terms)
    guard = SourceGuard(data_mode_setting, progress)                                                       # A2.5
    notes: list[tuple[str, str]] = []
    filing_records = request_filings_and_reported_facts(confirmed_claim, sources, guard, today=today)      # A2.1
    market_records = request_market_and_option_data(confirmed_claim, sources, guard, today=today, notes=notes)  # A2.2
    context_records = request_context_signals(confirmed_claim, sources, guard,                             # A2.3
                                              company=company_name(filing_records, confirmed_claim.values.ticker), now=now)
    evidence_set = stamp_provenance(filing_records, market_records, context_records, data_mode_setting=data_mode_setting)  # A2.4
    return AcquiredEvidence(evidence_query=sources.evidence_query, evidence_set=evidence_set,
                            source_error_records=guard.source_error_records, guard=guard, sources=sources, notes=notes)
