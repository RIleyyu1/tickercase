"""Case workflow (UC.1):

validate -> require matching confirmation -> calculate (OA.8)
-> SEC filings, SEC XBRL facts, market prices (OA.6-OA.7, OA.9)
-> deterministic evidence checks, verdict, recheck conditions (OA.12-OA.14)
-> optional probability reference -> CaseResult (OA.15)

Calculations are finished before any network call, so a provider failure never
removes them. Each provider fails on its own and is reported as an error; no
other data source is substituted for a failed one.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional, TypeVar

from .analysis import RULES_VERSION, analyze, share_trend
from .calculations import calculate
from .config import SEC_MODES, Settings, load_settings
from .http_client import FetchError, JsonFetcher, LiveHttpClient, RecordingHttpClient, ReplayHttpClient, utcnow
from .models import (
    CaseResult,
    CaseStatus,
    ClaimDraft,
    Confirmation,
    ConfirmedClaim,
    DataMode,
    ProviderErrorRecord,
    BenchmarkReturn,
    ClaimExtraction,
    Narrative,
    PriceBaseRate,
    ReferenceSnapshot,
    ReferenceSuggestion,
)
from .extract import extract_claim
from .probability import probability_reference
from .report import build_report
from .providers.base import ProviderError
from .oracle import build_oracle
from .providers.insiders import InsiderProvider
from .providers.market import CHART_URL, MONTHLY_URL, YahooChartProvider, monthly_closes, price_base_rate, price_touch_hits
from .providers.options import YahooOptionsProvider
from .providers.sec_frames import SecFramesBaseRateProvider
from .providers.sentiment import FearGreedProvider, PolymarketProvider
from .providers.yahoo_auth import BROWSER_UA, YahooAuthedClient
from .providers.sec import SecFilingProvider, SecFilingQuery
from .providers.sec_facts import SecCompanyFactsProvider
from .storage import CaseStore
from .validation import TICKER_RE, fingerprint, validate_draft

USER_INPUT_PROVENANCE = {
    "claim_text": "user_input",
    "ticker": "user_input",
    "currency": "user_input",
    "target_price": "user_input:claim",
    "reference_price": "user_input:manual_reference_value",
    "reference_price_date": "user_input:manual_reference_value",
    "reference_price_source": "user_input",
    "horizon_years": "user_input:assumption",
    "target_assumed_shares": "user_input:assumption",
    "current_shares": "user_input:manual_reference_value",
    "valuation_method": "user_input:assumption",
    "valuation_multiple": "user_input:assumption",
    "base_annual_metric": "user_input:manual_reference_value",
    "base_metric_currency": "user_input",
    "base_metric_period": "user_input",
    "filings_since": "user_input",
    "share_change_rate": "user_input:assumption",
    "share_change_mode": "user_input",
    "probability_drift": "user_input:assumption",
    "probability_volatility": "user_input:assumption",
}

DEFAULT_PREFIX = "default_assumption:"
CLAIM_TEXT_PREFIX = "claim_text:"
SEC_PROVIDER_ID = SecFilingProvider.provider_id
FACTS_PROVIDER_ID = SecCompanyFactsProvider.provider_id
MARKET_PROVIDER_ID = YahooChartProvider.provider_id
SOURCES = ("sec", "market", "options", "web")
OPTIONS_PROVIDER_ID = YahooOptionsProvider.provider_id
BENCHMARKS = (("SPY", "S&P 500 (SPY)"), ("QQQ", "Nasdaq-100 (QQQ)"))

T = TypeVar("T")


def build_fetcher(mode: str, settings: Settings, source: str = "sec") -> JsonFetcher:
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


def _mode_enum(mode: str) -> Optional[DataMode]:
    return {"live": DataMode.LIVE, "record": DataMode.LIVE, "replay": DataMode.REPLAY, "synthetic": DataMode.SYNTHETIC}.get(mode)


class CaseService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        fetcher_factory: Optional[Callable[[str, str], JsonFetcher]] = None,
        store: Optional[CaseStore] = None,
        now: Callable[[], datetime] = utcnow,
        today: Optional[Callable[[], date]] = None,
    ):
        self.settings = settings or load_settings()
        self._factory = fetcher_factory or (lambda mode, source: build_fetcher(mode, self.settings, source))
        self._fetchers: dict[tuple[str, str], JsonFetcher] = {}
        self.store = store
        self.now = now
        self.today = today or (lambda: self.now().date())

    def _fetcher(self, mode: str, source: str) -> JsonFetcher:
        key = (mode, source)
        if key not in self._fetchers:
            self._fetchers[key] = self._factory(mode, source)
        return self._fetchers[key]

    @staticmethod
    def _guard(provider_id: str, mode: str, errors: list[ProviderErrorRecord], call: Callable[[], T]) -> Optional[T]:
        """Run one provider call; record a failure as a structured error and return None."""
        try:
            return call()
        except FetchError as exc:
            errors.append(ProviderErrorRecord(
                provider_id=provider_id, code=exc.code, message=exc.message, url=exc.url, http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds, data_mode=exc.data_mode or _mode_enum(mode),
            ))
        except ProviderError as exc:
            errors.append(ProviderErrorRecord(provider_id=provider_id, code=exc.code, message=exc.message, url=exc.url, data_mode=_mode_enum(mode)))
        except ValueError as exc:
            errors.append(ProviderErrorRecord(provider_id=provider_id, code="config_error", message=str(exc)))
        return None

    # ---------------------------------------------------------------- evaluate

    def evaluate(self, draft: ClaimDraft, confirmation: Optional[Confirmation], *, sec_mode: str,
                 progress: Optional[Callable[[str, str], None]] = None) -> CaseResult:
        """Run one confirmed case. ``sec_mode`` is the data mode for every external source.

        ``progress(step, state)`` is called with state "running", "ok" or "failed" for each data step.
        """
        mode = sec_mode
        report_step = progress or (lambda step, state: None)
        case_id = uuid.uuid4().hex
        created = self.now()
        validation = validate_draft(draft, today=self.today())
        current_fp = fingerprint(draft)
        base = dict(
            case_id=case_id,
            created_at=created,
            input_fingerprint=current_fp,
            validation_issues=validation.issues,
            missing_fields=validation.missing_fields,
            warnings=list(validation.warnings),
            warnings_zh=list(validation.warnings_zh),
        )

        def warn(en: str, zh: str) -> None:
            base["warnings"].append(en)
            base["warnings_zh"].append(zh)

        if not validation.ok:
            return self._finish(CaseResult(status=CaseStatus.BLOCKED_INVALID_INPUT, **base))
        if confirmation is None:
            warn("inputs have not been confirmed; evaluation not started", "输入尚未确认，未开始评估")
            return self._finish(CaseResult(status=CaseStatus.BLOCKED_UNCONFIRMED, **base))
        if confirmation.fingerprint != current_fp:
            warn("inputs changed after confirmation; confirm the current inputs before evaluating", "确认后输入已修改，请先确认当前输入")
            return self._finish(CaseResult(status=CaseStatus.BLOCKED_CONFIRMATION_STALE, **base))

        claim = validation.claim
        assert claim is not None
        provenance = {k: v for k, v in USER_INPUT_PROVENANCE.items() if getattr(claim, k, None) is not None}
        for name, source in claim.field_sources.items():
            provenance[name] = source if source.startswith((DEFAULT_PREFIX, CLAIM_TEXT_PREFIX)) else f"public_data:{source}"
        if claim.target_shares_derived:
            provenance["target_assumed_shares"] = "derived:current_shares * (1 + share_change_rate) ** horizon_years"
        confirmed = ConfirmedClaim(values=claim, fingerprint=current_fp, confirmed_at=confirmation.confirmed_at, value_provenance=provenance)
        calculations = calculate(claim)

        errors: list[ProviderErrorRecord] = []
        warnings = base["warnings"]

        steps: dict[str, str] = {}

        def step(name: str, provider_id: str, call):
            report_step(name, "running")
            before = len(errors)
            value = self._guard(provider_id, mode, errors, call)
            steps[name] = "ok" if value is not None and len(errors) == before else "failed"
            report_step(name, steps[name])
            return value

        sec_fetcher = self._guard(SEC_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "sec"))
        filings = facts = market = None
        filing_provider = SecFilingProvider(sec_fetcher) if sec_fetcher is not None else None
        if sec_fetcher is not None:
            filings = step("sec_filings", SEC_PROVIDER_ID, lambda: filing_provider.fetch_filings(
                SecFilingQuery(ticker=claim.ticker, since=claim.filings_since)))
            facts = step("sec_facts", FACTS_PROVIDER_ID, lambda: SecCompanyFactsProvider(sec_fetcher, filing_provider).fetch_facts(claim.ticker))
        market = step("price_history", MARKET_PROVIDER_ID, lambda: YahooChartProvider(self._fetcher(mode, "market")).fetch_history(claim.ticker))

        records = filings.records if filings else []
        for w in filings.warnings if filings else []:
            warn(w, f"SEC 申报：{w}")
        for n in facts.notes if facts else []:
            warn(f"SEC XBRL: {n}", f"SEC XBRL：{n}")

        outcome = analyze(claim, calculations, facts=facts, market=market, filings=records, today=self.today())
        for w in outcome.warnings:
            warn(w.en, w.zh)

        probability = probability_reference(claim, market)

        # ---------------------------------------------------------------- oracle layers
        target_date = claim.reference_price_date + timedelta(days=round(float(claim.horizon_years) * 365.25))
        market_fetcher = self._guard(MARKET_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "market"))
        options = step("options", OPTIONS_PROVIDER_ID, lambda: YahooOptionsProvider(self._fetcher(mode, "options")).fetch_chain(
            claim.ticker, target_date, claim.target_price, today=self.today()))
        risk_free = None
        if market_fetcher is not None:
            tnx = step("risk_free_rate", MARKET_PROVIDER_ID, lambda: YahooChartProvider(market_fetcher).fetch_history("^TNX"))
            risk_free = (tnx.last_close / 100).quantize(Decimal("0.0001")) if tnx is not None else None
        spot = market.last_close if market is not None else claim.reference_price
        months = max(1, round(float(claim.horizon_years) * 12))
        price_rate = benchmarks = None
        if market_fetcher is not None:
            price_rate = step("price_base_rate", MARKET_PROVIDER_ID, lambda: self._price_base_rate(market_fetcher, claim, months, spot))
            benchmarks = step("benchmarks", MARKET_PROVIDER_ID, lambda: self._benchmarks(market_fetcher, months))
        base_rate = None
        e1 = next((i for i in outcome.items if i.id == "E1"), None)
        series = (facts.revenue if claim.valuation_method.metric_name == "annual_revenue" else facts.net_income) if facts else []
        if sec_fetcher is not None and e1 is not None and "required_cagr_from_reported" in e1.measured and series:
            end_year = self.today().year - (1 if self.today().month >= 4 else 2)
            years = max(1, min(10, round(float(claim.horizon_years))))
            base_rate = step("base_rate", SecFramesBaseRateProvider.provider_id, lambda: SecFramesBaseRateProvider(sec_fetcher).base_rate(
                metric=claim.valuation_method.metric_name, base_value=series[-1].value,
                required_cagr=Decimal(e1.measured["required_cagr_from_reported"]).quantize(Decimal("0.0001")), end_year=end_year, years=years,
                exclude_cik=int(facts.cik)))
        insiders = None
        if sec_fetcher is not None:
            insiders = step("insiders", InsiderProvider.provider_id, lambda: InsiderProvider(sec_fetcher, filing_provider).summary(claim.ticker, today=self.today()))
        prediction = None
        if market_fetcher is not None:
            prediction = step("prediction_markets", PolymarketProvider.provider_id, lambda: PolymarketProvider(market_fetcher).search(claim.ticker, now=self.now()))
        sentiment = step("fear_greed", FearGreedProvider.provider_id, lambda: FearGreedProvider(self._fetcher(mode, "web")).snapshot())
        oracle = build_oracle(claim, market=market, options=options, base_rate=base_rate, price_rate=price_rate,
                              risk_free=risk_free, today=self.today())
        report = build_report(claim, calculations, facts=facts, market=market, items=outcome.items, verdict=outcome.verdict,
                              rechecks=outcome.rechecks, probability=probability, today=self.today(), oracle=oracle, options=options,
                              base_rate=base_rate, price_rate=price_rate, benchmarks=benchmarks, insiders=insiders,
                              prediction=prediction, sentiment=sentiment)
        failed = sorted({e.provider_id for e in errors})
        if failed:
            warn(f"data unavailable for this run from {', '.join(failed)}; affected checks are listed as missing, calculations use only your inputs",
                 f"本次未能从 {', '.join(failed)} 取得数据；相关检查显示为缺失，计算只使用你的输入")

        def mode_of(present: bool, modes: set[str]) -> str:
            if not present:
                return "unavailable"
            return ",".join(sorted(modes)) if modes else (_mode_enum(mode).value if _mode_enum(mode) else "unavailable")

        data_modes = {
            "claim_inputs": DataMode.USER_INPUT.value,
            "sec_filings": mode_of(filings is not None, {r.data_mode.value for r in records}),
            "sec_facts": mode_of(facts is not None, {facts.data_mode.value} if facts else set()),
            "market_prices": mode_of(market is not None, {market.data_mode.value} if market else set()),
            "options": mode_of(options is not None, {options.data_mode.value} if options else set()),
            "base_rate": mode_of(base_rate is not None, {base_rate.data_mode.value} if base_rate else set()),
            "insiders": mode_of(insiders is not None, {insiders.data_mode.value} if insiders else set()),
            "fear_greed": mode_of(sentiment is not None, {sentiment.data_mode.value} if sentiment else set()),
            "analysis": f"deterministic {RULES_VERSION}",
        }
        if DataMode.SYNTHETIC.value in data_modes.values():
            warn("synthetic example data is used for public sources in this run, not real SEC or market data",
                 "本次公开数据使用合成示例数据，不是真实的 SEC 或市场数据")

        result = CaseResult(
            status=CaseStatus.EVALUATED_WITH_PROVIDER_ERRORS if errors else CaseStatus.EVALUATED,
            confirmed_claim=confirmed,
            calculations=calculations,
            evidence_records=records,
            coverage=filings.coverage if filings else None,
            reported_facts=facts,
            market=market,
            evidence_items=outcome.items,
            verdict=outcome.verdict,
            recheck_conditions=outcome.rechecks,
            probability=probability,
            sensitivity=outcome.sensitivity,
            report=report,
            options=options,
            base_rate=base_rate,
            price_base_rate=price_rate,
            benchmarks=benchmarks or [],
            insiders=insiders,
            prediction_markets=prediction,
            sentiment=sentiment,
            oracle=oracle,
            data_steps=steps,
            provider_errors=errors,
            data_modes=data_modes,
            mixed_sources=bool(records or facts or market),
            analysis_status="deterministic_rules",
            **base,
        )
        return self._finish(result)

    @staticmethod
    def _price_base_rate(fetcher: JsonFetcher, claim, months: int, spot: Decimal) -> PriceBaseRate:
        from .providers.market import yahoo_symbol

        url = MONTHLY_URL.format(symbol=yahoo_symbol(claim.ticker))
        fetched = fetcher.get_json(url)
        closes = monthly_closes(fetched.payload, url)
        required = claim.target_price / spot - 1
        windows, hits, med, best = price_base_rate(closes, months, required)
        touch_hits = price_touch_hits(closes, months, required)
        return PriceBaseRate(
            symbol=yahoo_symbol(claim.ticker), window_months=months, windows=windows, hits=hits, rate=hits / windows if windows else None,
            required_return=required.quantize(Decimal("0.0001")), median_return=Decimal(str(round(med, 4))) if med is not None else None,
            best_return=Decimal(str(round(best, 4))) if best is not None else None, history_start=closes[0][0] if closes else date.today(),
            touch_hits=touch_hits,
            source_url=url, retrieved_at=fetched.retrieved_at, source_captured_at=fetched.source_captured_at, data_mode=fetched.data_mode)

    @staticmethod
    def _benchmarks(fetcher: JsonFetcher, months: int) -> list[BenchmarkReturn]:
        out = []
        for symbol, label in BENCHMARKS:
            url = MONTHLY_URL.format(symbol=symbol)
            fetched = fetcher.get_json(url)
            closes = monthly_closes(fetched.payload, url)
            n = min(months, len(closes) - 1)
            if n < 1:
                continue
            (d0, p0), (d1, p1) = closes[-1 - n], closes[-1]
            total = Decimal(str(p1 / p0 - 1))
            annual = Decimal(str((p1 / p0) ** (12 / n) - 1))
            out.append(BenchmarkReturn(symbol=symbol, label=label, start=d0, end=d1, total_return=total.quantize(Decimal("0.0001")),
                                       annual_return=annual.quantize(Decimal("0.0001")), data_mode=fetched.data_mode))
        return out

    # ---------------------------------------------------------------- AI narrative

    def narrate(self, result: CaseResult, *, client=None) -> CaseResult:
        """Add a Claude-written, number-checked narrative to a finished case (one paid API call) and store it."""
        from .narrative import MODEL, write_narrative

        if client is None:
            try:
                import anthropic

                client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key) if self.settings.anthropic_api_key else anthropic.Anthropic()
            except Exception as exc:  # no SDK or no credentials
                result.narrative = Narrative(status="not_configured", model=MODEL, created_at=self.now(),
                                             error=f"Claude API is not configured: {type(exc).__name__}: {exc}")
                return self._finish(result)
        result.narrative = write_narrative(result, client=client, now=self.now)
        return self._finish(result)

    # ---------------------------------------------------------------- extraction

    def extract(self, text: str, *, mode: str) -> ClaimExtraction:
        """Read ticker, target and horizon from the claim sentence; tickers are checked against SEC's list when it is reachable."""
        is_known = None
        errors: list[ProviderErrorRecord] = []
        fetcher = self._guard(SEC_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "sec"))
        if fetcher is not None:
            provider = SecFilingProvider(fetcher)
            mapping = self._guard(SEC_PROVIDER_ID, mode, errors, provider._load_ticker_map)
            if mapping:
                is_known = lambda sym: sym in mapping or sym.replace(".", "-") in mapping  # noqa: E731
        return extract_claim(text, today=self.today(), is_known_ticker=is_known)

    # ---------------------------------------------------------------- prefill

    def reference_snapshot(self, ticker: str, *, mode: str) -> ReferenceSnapshot:
        """Public reference values the page can offer before confirmation. Nothing here is confirmed."""
        symbol = (ticker or "").strip().upper()
        if not TICKER_RE.match(symbol):
            raise ValueError(f"ticker '{ticker}' has an unexpected format")
        errors: list[ProviderErrorRecord] = []
        sec_fetcher = self._guard(SEC_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "sec"))
        facts = None
        if sec_fetcher is not None:
            facts = self._guard(FACTS_PROVIDER_ID, mode, errors, lambda: SecCompanyFactsProvider(sec_fetcher).fetch_facts(symbol))
        market = self._guard(MARKET_PROVIDER_ID, mode, errors, lambda: YahooChartProvider(self._fetcher(mode, "market")).fetch_history(symbol))

        snap = ReferenceSnapshot(ticker=symbol, provider_errors=errors, company_name=facts.company_name if facts else None)
        if market is not None:
            label = f"{market.provider_id} close {market.last_date} ({market.data_mode.value})"
            close = market.last_close.quantize(Decimal("0.01")).normalize()
            snap.suggestions["reference_price"] = ReferenceSuggestion(value=format(close, "f"), source=label)
            snap.suggestions["reference_price_date"] = ReferenceSuggestion(value=market.last_date.isoformat(), source=label)
            snap.suggestions["reference_price_source"] = ReferenceSuggestion(value=label, source=label)
            if market.currency:
                snap.suggestions["currency"] = ReferenceSuggestion(value=market.currency, source=label)
            snap.data_modes["market_prices"] = market.data_mode.value
        if facts is not None:
            snap.data_modes["sec_facts"] = facts.data_mode.value
            if facts.shares_outstanding:
                s = facts.shares_outstanding[-1]
                snap.suggestions["current_shares"] = ReferenceSuggestion(
                    value=format(s.value, "f"),
                    source=f"SEC XBRL {s.concept} as of {s.period_end}, {s.form} filed {s.filed} ({facts.data_mode.value})")
            for attr, series in (("revenue_suggestion", facts.revenue), ("net_income_suggestion", facts.net_income)):
                if series:
                    p = series[-1]
                    setattr(snap, attr, ReferenceSuggestion(
                        value=format(p.value, "f"),
                        source=f"SEC XBRL {p.concept} FY ending {p.period_end}, {p.form} filed {p.filed} ({facts.data_mode.value})"))
            latest = (facts.revenue or facts.net_income or [None])[-1]
            if latest is not None:
                snap.period_suggestion = ReferenceSuggestion(value=f"FY ending {latest.period_end}", source="SEC XBRL")
                snap.suggestions["base_metric_currency"] = ReferenceSuggestion(value="USD", source="SEC XBRL unit")
            self._default_assumptions(snap, facts, market)
        snap.assumption_suggestions.setdefault("filings_since", ReferenceSuggestion(
            value=date(self.today().year - 2, 1, 1).isoformat(), source=f"{DEFAULT_PREFIX}two calendar years of filings"))
        return snap

    @staticmethod
    def _default_assumptions(snap: ReferenceSnapshot, facts, market) -> None:
        """Neutral defaults: today's share count and today's multiple. Visible, labelled, confirmed by the user."""
        if not facts.shares_outstanding:
            return
        shares = facts.shares_outstanding[-1]
        snap.assumption_suggestions["target_assumed_shares"] = ReferenceSuggestion(
            value=format(shares.value, "f"),
            source=f"{DEFAULT_PREFIX}no change from reported shares as of {shares.period_end}")
        trend = share_trend(facts.shares_outstanding)
        if trend is not None:
            rate, start, latest = trend
            snap.assumption_suggestions["share_change_rate"] = ReferenceSuggestion(
                value=format(rate.quantize(Decimal("0.0001")), "f"),
                source=f"{DEFAULT_PREFIX}reported share trend {start.period_end} to {latest.period_end} (after any split)")
        else:
            snap.assumption_suggestions["share_change_rate"] = ReferenceSuggestion(
                value="0", source=f"{DEFAULT_PREFIX}no change (not enough share history after the last split)")
        if market is None or (market.currency and market.currency != "USD"):
            return
        cap = market.last_close * shares.value
        for attr, series, label in (("current_ps", facts.revenue, "P/S"), ("current_pe", facts.net_income, "P/E")):
            if series and series[-1].value > 0:
                current = (cap / series[-1].value).quantize(Decimal("0.01"))
                setattr(snap, attr, current)
                key = "valuation_multiple_ps" if label == "P/S" else "valuation_multiple_pe"
                snap.assumption_suggestions[key] = ReferenceSuggestion(
                    value=format(current, "f"),
                    source=f"{DEFAULT_PREFIX}today's {label} (close {market.last_date} x shares / FY ending {series[-1].period_end})")

    def _finish(self, result: CaseResult) -> CaseResult:
        if self.store is not None:
            self.store.save(result)
        return result
