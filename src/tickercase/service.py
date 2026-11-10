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
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional, TypeVar

from .analysis import RULES_VERSION, analyze, share_trend
from .calculations import annualized_rate, calculate, deadline_from
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
    CatalystProposal,
)
from .catalysts import fill_reactions
from .extract import extract_claim
from .probability import probability_reference
from .report import build_report
from .providers.base import ProviderError
from .oracle import build_oracle
from .providers.insiders import InsiderProvider
from .providers.market import CHART_URL, MONTHLY_URL, YahooChartProvider, monthly_closes, price_base_rate, price_touch_hits, yahoo_symbol
from .providers.options import YahooOptionsProvider
from .providers.calendar import EarningsCalendarProvider
from .providers.filing_text import FilingTextProvider, recent_8ks
from .providers.news import NewsProvider, company_query_name, google_query
from .providers.peers import YahooPeersProvider
from .providers.structure import MarketStructureProvider
from .providers.sec_frames import SecFramesBaseRateProvider
from .providers.sentiment import FearGreedProvider, PolymarketProvider
from .providers.yahoo_auth import BROWSER_UA, YahooAuthedClient
from .providers.sec import SecFilingProvider, SecFilingQuery
from .providers.sec_facts import SecCompanyFactsProvider
from .storage import CaseStore, CatalystStore
from .structure import build_structure, find_previous, probability_change, reaction, shifted_range
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
from .acquisition import (  # A2 Acquire Dated Evidence (C.2)
    BENCHMARKS, FACTS_PROVIDER_ID, MARKET_PROVIDER_ID, OPTIONS_PROVIDER_ID, SEC_PROVIDER_ID, SOURCES,
    acquire_dated_evidence, build_fetcher, company_name, fetch_benchmarks, fetch_price_base_rate, isolate, mode_enum, mode_of,
    request_peer_base_rate,
    AcquiredEvidence, ContextRecords, EvidenceSet, FilingRecords, MarketRecords, SourceGateway, SourceGuard,
    request_context_signals, request_filings_and_reported_facts, request_market_and_option_data,
)

T = TypeVar("T")
_mode_enum = mode_enum



class CaseService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        fetcher_factory: Optional[Callable[[str, str], JsonFetcher]] = None,
        store: Optional[CaseStore] = None,
        now: Callable[[], datetime] = utcnow,
        today: Optional[Callable[[], date]] = None,
        catalysts: Optional[CatalystStore] = None,
    ):
        self.settings = settings or load_settings()
        self._factory = fetcher_factory or (lambda mode, source: build_fetcher(mode, self.settings, source))
        self._fetchers: dict[tuple[str, str], JsonFetcher] = {}
        self.store = store
        # the catalyst registers live next to the cases (data/cases -> data/catalysts) unless given
        self.catalysts = catalysts or (CatalystStore(store.directory.parent / "catalysts") if store is not None else None)
        self.now = now
        self.today = today or (lambda: self.now().date())

    def _fetcher(self, mode: str, source: str) -> JsonFetcher:
        key = (mode, source)
        if key not in self._fetchers:
            self._fetchers[key] = self._factory(mode, source)
        return self._fetchers[key]

    @staticmethod
    def _guard(provider_id: str, mode: str, errors: list[ProviderErrorRecord], call: Callable[[], T]) -> Optional[T]:
        """Run one provider call; record a failure as a structured error and return None (A2.5, acquisition.isolate)."""
        return isolate(provider_id, mode, errors, call)

    @staticmethod
    def _confirmed(claim, current_fp: str, confirmation: Confirmation) -> ConfirmedClaim:
        """The Confirmed Claim (IO.5): the validated values, their fingerprint and where each value came from."""
        provenance = {k: v for k, v in USER_INPUT_PROVENANCE.items() if getattr(claim, k, None) is not None}
        for name, source in claim.field_sources.items():
            provenance[name] = source if source.startswith((DEFAULT_PREFIX, CLAIM_TEXT_PREFIX)) else f"public_data:{source}"
        if claim.target_shares_derived:
            provenance["target_assumed_shares"] = "derived:current_shares * (1 + share_change_rate) ** horizon_years"
        return ConfirmedClaim(values=claim, fingerprint=current_fp, confirmed_at=confirmation.confirmed_at, value_provenance=provenance)

    # ---------------------------------------------------------------- A2 on its own (contracts/api-v1.yaml, /evidence)

    def acquire(self, draft: ClaimDraft, confirmation: Optional[Confirmation], *, sec_mode: str,
                flow: Optional[str] = None) -> tuple[CaseStatus, Optional[AcquiredEvidence]]:
        """Run A2 Acquire Dated Evidence for a confirmed claim, or one of its internal flows.

        ``flow`` is None for the whole function, or "filings" (A2.1), "market" (A2.2) or "context" (A2.3).
        An unconfirmed, stale or invalid claim sends no request: the status says why and the evidence is None.
        """
        validation = validate_draft(draft, today=self.today())
        current_fp = fingerprint(draft)
        if not validation.ok:
            return CaseStatus.BLOCKED_INVALID_INPUT, None
        if confirmation is None:
            return CaseStatus.BLOCKED_UNCONFIRMED, None
        if confirmation.fingerprint != current_fp:
            return CaseStatus.BLOCKED_CONFIRMATION_STALE, None
        confirmed = self._confirmed(validation.claim, current_fp, confirmation)

        def source_evidence(data_mode, terms, source):
            return self._fetcher(data_mode, source)

        if flow is None:
            acquired = acquire_dated_evidence(confirmed, source_evidence, data_mode_setting=sec_mode,
                                              provider_access_terms=self.settings, today=self.today(), now=self.now())
        else:
            sources = SourceGateway(source_evidence, sec_mode, self.settings)
            guard = SourceGuard(sec_mode)
            filing, quotes, context = FilingRecords(), MarketRecords(), ContextRecords()
            if flow == "filings":
                filing = request_filings_and_reported_facts(confirmed, sources, guard, today=self.today())
            elif flow == "market":
                quotes = request_market_and_option_data(confirmed, sources, guard, today=self.today())
            elif flow == "context":
                context = request_context_signals(confirmed, sources, guard, company=company_name(filing, validation.claim.ticker),
                                                  now=self.now())
            else:
                raise ValueError(f"unknown flow '{flow}'; expected filings, market or context")
            acquired = AcquiredEvidence(evidence_query=sources.evidence_query,
                                        evidence_set=EvidenceSet(filing=filing, market=quotes, context=context),
                                        source_error_records=guard.source_error_records, guard=guard, sources=sources)
        status = CaseStatus.EVALUATED_WITH_PROVIDER_ERRORS if acquired.source_error_records else CaseStatus.EVALUATED
        return status, acquired

    # ---------------------------------------------------------------- evaluate

    def evaluate(self, draft: ClaimDraft, confirmation: Optional[Confirmation], *, sec_mode: str,
                 progress: Optional[Callable[[str, str], None]] = None) -> CaseResult:
        """Run one confirmed case. ``sec_mode`` is the data mode for every external source.

        ``progress(step, state)`` is called with state "running", "ok" or "failed" for each data step.
        """
        mode = sec_mode
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
        confirmed = self._confirmed(claim, current_fp, confirmation)
        calculations = calculate(claim)

        # ---------------------------------------------------------------- A2 Acquire Dated Evidence (C.2)
        acquired = acquire_dated_evidence(
            confirmed, lambda data_mode, terms, source: self._fetcher(data_mode, source),
            data_mode_setting=mode, provider_access_terms=self.settings, today=self.today(), now=self.now(), progress=progress)
        guard, sources, evidence = acquired.guard, acquired.sources, acquired.evidence_set
        errors, steps, step = guard.source_error_records, guard.steps, guard.step
        warnings = base["warnings"]
        filing, quotes, context = evidence.filing, evidence.market, evidence.context
        records, facts, insiders = filing.records, filing.reported_facts, filing.insiders
        market, options, risk_free = quotes.price_history, quotes.options, quotes.risk_free_rate
        price_rate, benchmarks, earnings = quotes.price_base_rate, quotes.benchmarks, quotes.earnings
        prediction, sentiment, news = context.prediction_markets, context.sentiment, context.news

        for w in filing.warnings:
            warn(w, f"SEC 申报：{note_zh(w)}")
        for n in facts.notes if facts else []:
            warn(f"SEC XBRL: {n}", f"SEC XBRL：{note_zh(n)}")

        outcome = analyze(claim, calculations, facts=facts, market=market, filings=records, today=self.today())
        for w in outcome.warnings:
            warn(w.en, w.zh)
        calculations = growth_from_base_end(calculations, claim, outcome.items)
        for en, zh in acquired.notes:
            warn(en, zh)

        probability = probability_reference(claim, market)

        # ---------------------------------------------------------------- oracle layers
        market_fetcher = sources.guarded("market", MARKET_PROVIDER_ID, guard)
        base_rate = None
        e1 = next((i for i in outcome.items if i.id == "E1"), None)
        series = (facts.revenue if claim.valuation_method.metric_name == "annual_revenue" else facts.net_income) if facts else []
        if e1 is not None and "required_cagr_from_reported" in e1.measured and series:
            end_year = self.today().year - (1 if self.today().month >= 4 else 2)
            years = max(1, min(10, round(float(claim.horizon_years))))
            base_rate = request_peer_base_rate(
                sources, guard, metric=claim.valuation_method.metric_name, base_value=series[-1].value,
                required_cagr=Decimal(e1.measured["required_cagr_from_reported"]), end_year=end_year, years=years,
                exclude_cik=int(facts.cik))
        structure = fit = None
        if market_fetcher is not None and market is not None:
            peer_source = claim.field_sources.get("peer_tickers") or ("user input" if claim.peer_tickers else None)
            built = step("market_structure", MarketStructureProvider.provider_id, lambda: build_structure(
                MarketStructureProvider(market_fetcher).inputs(claim.ticker, claim.peer_tickers, peer_source=peer_source), records,
                news=news.items if news else ()))
            structure, fit = built if built is not None else (None, None)
        register = self.catalysts.get(claim.ticker) if self.catalysts is not None else None
        if register is not None and fit is not None:
            fill_reactions(register.catalysts, lambda day: reaction(fit, day), today=self.today())
            self.catalysts.save(register)
        oracle = build_oracle(claim, market=market, options=options, base_rate=base_rate, price_rate=price_rate,
                              risk_free=risk_free, today=self.today())
        change = None
        if structure is not None and oracle.spot:
            monthly_url = MONTHLY_URL.format(symbol=yahoo_symbol(claim.ticker))
            closes = None
            if price_rate is not None:  # M4 is recounted at the shifted price from the same monthly closes
                closes = self._guard(MARKET_PROVIDER_ID, mode, errors, lambda: monthly_closes(market_fetcher.get_json(monthly_url).payload, monthly_url))
            spot_to = oracle.spot * (1 + structure.own_part) / (1 + structure.total)
            structure.shifted = shifted_range(claim, oracle, spot_to, market=market,
                                              options=options, base_rate=base_rate, price_rate=price_rate, closes=closes, risk_free=risk_free,
                                              today=self.today())
        if self.store is not None and oracle.spot and market is not None:
            previous = find_previous(self.store.list_cases(), claim, oracle, market_date=market.last_date, exclude=case_id)
            if previous is not None:
                change = probability_change(previous, oracle=oracle, fit=fit, today=self.today())
        report = build_report(claim, calculations, facts=facts, market=market, items=outcome.items, verdict=outcome.verdict,
                              rechecks=outcome.rechecks, probability=probability, today=self.today(), oracle=oracle, options=options,
                              base_rate=base_rate, price_rate=price_rate, benchmarks=benchmarks, insiders=insiders,
                              prediction=prediction, sentiment=sentiment)
        failed = sorted({e.provider_id for e in errors})
        if failed:
            warn(f"data unavailable for this run from {', '.join(failed)}; affected checks are listed as missing, calculations use only your inputs",
                 f"本次未能从 {', '.join(failed)} 取得数据；相关检查显示为缺失，计算只使用你的输入")

        def group_mode(record) -> str:
            return mode_of(record is not None, {record.data_mode.value} if record is not None else set(), mode)

        data_modes = {
            "claim_inputs": DataMode.USER_INPUT.value,
            **evidence.data_modes,
            "base_rate": group_mode(base_rate),
            "market_structure": group_mode(structure),
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
            coverage=filing.coverage,
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
            structure=structure,
            change=change,
            news=news,
            earnings=earnings,
            catalysts=register,
            data_steps=steps,
            provider_errors=errors,
            data_modes=data_modes,
            mixed_sources=bool(records or facts or market),
            analysis_status="deterministic_rules",
            **base,
        )
        return self._finish(result)

    _price_base_rate = staticmethod(fetch_price_base_rate)
    _benchmarks = staticmethod(fetch_benchmarks)

    @staticmethod
    def _company(facts, records, ticker: str) -> str:
        name = (facts.company_name if facts is not None else None) or next((r.company_name for r in records if r.company_name), None)
        return company_query_name(name, ticker)

    # ---------------------------------------------------------------- catalysts (v0.12)

    def _client(self):
        import anthropic

        return anthropic.Anthropic(api_key=self.settings.anthropic_api_key) if self.settings.anthropic_api_key else anthropic.Anthropic()

    def _fit_for(self, result: CaseResult, mode: str):
        """The factor fit of the case's stock and peers again (cached or replayed), for event reactions; None when it cannot be built."""
        claim = result.confirmed_claim.values
        errors: list[ProviderErrorRecord] = []
        fetcher = self._guard(MARKET_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "market"))
        if fetcher is None:
            return None
        built = self._guard(MarketStructureProvider.provider_id, mode, errors, lambda: build_structure(
            MarketStructureProvider(fetcher).inputs(claim.ticker, claim.peer_tickers)))
        return built[1] if built is not None else None

    def propose_catalysts(self, result: CaseResult, *, mode: str, client=None, progress: Optional[Callable[[str], None]] = None) -> CaseResult:
        """Claude proposes catalysts from 8-K press releases and headlines; code checks them and measures reactions. Two paid calls."""
        from .catalysts import add_usage, build, proposal_error, scout, source_table

        note = progress or (lambda text: None)
        claim = result.confirmed_claim.values
        model, effort = self.settings.narrative_model, self.settings.narrative_effort
        if client is None:
            try:
                client = self._client()
            except Exception as exc:  # no SDK or no credentials
                result.catalyst_proposal = proposal_error("not_configured", model, self.now(), f"Claude API is not configured: {type(exc).__name__}: {exc}")
                return self._finish(result)
        errors: list[ProviderErrorRecord] = []
        today = self.today()
        company = self._company(result.reported_facts, result.evidence_records, claim.ticker)
        register = self.catalysts.get(claim.ticker) if self.catalysts is not None else None
        note("filings")
        sec = self._guard(SEC_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "sec"))
        filings = []
        if sec is not None:
            reader = FilingTextProvider(sec)
            for record in recent_8ks(result.evidence_records, since=today - timedelta(days=365)):
                got = self._guard(FilingTextProvider.provider_id, mode, errors, lambda r=record: reader.sentences(r, company))
                if got and got[0]:
                    filings.append((record, got[0], got[1]))
        note("news")
        market = self._guard(MARKET_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "market"))
        news_provider = NewsProvider(market) if market is not None else None
        broad = list(result.news.items) if result.news is not None else []
        if news_provider is not None:
            got = self._guard(NewsProvider.provider_id, mode, errors, lambda: news_provider.google(google_query(company, days=365)))
            broad += got[0] if got else []
        first = source_table(filings, broad)
        note("themes")
        terms, res1 = scout(client, first, company=company, ticker=claim.ticker, register=register, model=model, effort=effort)
        if res1.status != "ok":
            result.catalyst_proposal = proposal_error(res1.status, res1.model, self.now(), res1.error or res1.status, usage=res1.usage, sources=first)
            return self._finish(result)
        for c in register.catalysts if register is not None else []:
            for term in c.search_terms[:1]:
                if term.lower() not in {t.lower() for t in terms}:
                    terms.append(term)
        targeted: list[list] = []
        if news_provider is not None:
            for term in terms[:8]:
                note(f"search:{term}")
                got = self._guard(NewsProvider.provider_id, mode, errors, lambda t=term: news_provider.google(google_query(company, t, days=730)))
                if got:
                    targeted.append(got[0])
        sources = source_table(filings, broad, themes=targeted)
        note("catalysts")
        catalysts, res2 = build(client, sources, company=company, ticker=claim.ticker, register=register, model=model, effort=effort)
        usage = add_usage(res1.usage, res2.usage)
        if res2.status != "ok":
            result.catalyst_proposal = proposal_error(res2.status, res2.model, self.now(), res2.error or res2.status, usage=usage, sources=sources, themes=terms)
            return self._finish(result)
        note("reactions")
        fit = self._fit_for(result, mode)
        if fit is not None:
            fill_reactions(catalysts, lambda day: reaction(fit, day), today=today)
        result.catalyst_proposal = CatalystProposal(status="ok", model=res2.model, created_at=self.now(), themes=terms, sources=sources,
                                                    catalysts=catalysts, usage=usage,
                                                    error="; ".join(f"{e.provider_id}: {e.code}" for e in errors) or None)
        return self._finish(result)

    def confirm_catalysts(self, result: CaseResult, accepted: list[str]) -> CaseResult:
        """Write the accepted proposals into the stock's register; remember rejected names."""
        from .catalysts import merge

        proposal = result.catalyst_proposal
        if proposal is None or proposal.status != "ok" or proposal.decided_at is not None:
            return result
        claim = result.confirmed_claim.values
        current = self.catalysts.get(claim.ticker) if self.catalysts is not None else result.catalysts
        register = merge(current, proposal, accepted, ticker=claim.ticker,
                         company=self._company(result.reported_facts, result.evidence_records, claim.ticker), now=self.now())
        if self.catalysts is not None:
            self.catalysts.save(register)
        result.catalysts = register
        result.catalyst_proposal = proposal.model_copy(update={"decided_at": self.now(), "accepted": list(accepted)})
        return self._finish(result)

    def remove_catalyst(self, result: CaseResult, catalyst_id: str) -> CaseResult:
        """Take a confirmed catalyst out of the register (it is not added to the rejected names)."""
        claim = result.confirmed_claim.values
        register = (self.catalysts.get(claim.ticker) if self.catalysts is not None else None) or result.catalysts
        if register is None:
            return result
        register = register.model_copy(update={"catalysts": [c for c in register.catalysts if c.id != catalyst_id], "updated_at": self.now()})
        if self.catalysts is not None:
            self.catalysts.save(register)
        result.catalysts = register
        return self._finish(result)

    # ---------------------------------------------------------------- AI narrative

    def narrate(self, result: CaseResult, *, client=None, language: str = "zh", force: bool = False) -> CaseResult:
        """Add a Claude-written, number-checked narrative in one language to a finished case and store it.

        A narrative already written for the same facts, model, effort and language (in this case or any
        stored case) is reused without an API call unless ``force`` is set. Otherwise one paid call.
        """
        from .narrative import write_narrative

        model, effort = self.settings.narrative_model, self.settings.narrative_effort
        narrative = None if force else self._reusable_narrative(result, model=model, effort=effort, language=language)
        if narrative is None and client is None:
            try:
                import anthropic

                client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key) if self.settings.anthropic_api_key else anthropic.Anthropic()
            except Exception as exc:  # no SDK or no credentials
                narrative = Narrative(status="not_configured", model=model, created_at=self.now(), language=language,
                                      error=f"Claude API is not configured: {type(exc).__name__}: {exc}")
        if narrative is None:
            narrative = write_narrative(result, client=client, now=self.now, model=model, effort=effort, language=language)
        result.narratives[language] = narrative
        result.narrative = narrative
        return self._finish(result)

    def _reusable_narrative(self, result: CaseResult, *, model: str, effort: str, language: str) -> Optional[Narrative]:
        from .narrative import build_facts, cache_key, recheck

        facts = build_facts(result)
        if not facts:
            return None
        key = cache_key(facts, model=model, effort=effort, language=language)
        own = result.narratives.get(language)
        if own is not None and own.status == "ok" and own.cache_key == key:
            return recheck(own)
        for other in self.store.list_cases() if self.store is not None else []:
            found = other.narratives.get(language)
            if other.case_id != result.case_id and found is not None and found.status == "ok" and found.cache_key == key:
                return recheck(found).model_copy(update={"reused_from": other.case_id})  # a reused narrative gets today's verdicts
        return None

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
        peers = self._guard(YahooPeersProvider.provider_id, mode, errors, lambda: YahooPeersProvider(self._fetcher(mode, "market")).similar(symbol))
        if peers is not None:
            listed = [p for p in peers[0] if p != symbol][:6]
            if listed:
                snap.assumption_suggestions["peer_tickers"] = ReferenceSuggestion(
                    value=", ".join(listed), source=f"{DEFAULT_PREFIX}Yahoo Finance similar stocks for {symbol}")
        snap.provider_errors = list(errors)
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


# ---------------------------------------------------------------- v0.13.2 helpers

_NOTE_ZH = [
    (re.compile(r"showing the (\d+) most recent of (\d+) matching filings"), "只列出最近的 {0} 份（符合条件的共 {1} 份）"),
    (re.compile(r"(\d+) matching row\(s\) skipped because accession number, form or filing date was missing or malformed"),
     "跳过 {0} 行：申报编号、表格类型或申报日期缺失或格式不对"),
    (re.compile(r"revenue series combines concepts: (.+)"), "营收序列合并了多个 XBRL 概念：{0}"),
    (re.compile(r"no annual revenue found under (.+)"), "在 {0} 下没有找到年度营收"),
    (re.compile(r"no annual net income found under (.+)"), "在 {0} 下没有找到年度净利润"),
    (re.compile(r"no cover-page shares outstanding \(dei:EntityCommonStockSharesOutstanding\) found"), "没有找到封面上的流通股数（dei:EntityCommonStockSharesOutstanding）"),
    (re.compile(r"no (.+) filings found in the parsed range of the SEC response\.(?: Other form types present: (.+)\.)? "
                r"This is a retrieval result for this window, not proof that no such filing exists\."),
     "在 SEC 返回的范围里没有找到 {0} 申报。{1}这只是本次检索窗口的结果，不能证明这类申报不存在。"),
]


def note_zh(text: str) -> str:
    """Chinese for the provider notes the page shows; anything unknown stays as written."""
    for pattern, zh in _NOTE_ZH:
        m = pattern.fullmatch(text.strip())
        if m:
            groups = list(m.groups())
            if len(groups) == 2 and pattern.pattern.startswith("no (.+) filings"):  # the optional list of other forms
                groups[1] = f"返回里有其他类型：{groups[1]}。" if groups[1] else ""
            return zh.format(*groups)
    return text


def growth_from_base_end(calculations: list, claim, items: list) -> list:
    """When the base value is the latest reported fiscal year, the required growth runs from that year's end, as in E1.

    The calculation used to count only the claim's horizon from today, so the same base gave two growth rates
    (for example 61.65% over 3 years here and 45.75% over 3.82 years in E1).
    """
    e1 = next((i for i in items if i.id == "E1"), None)
    if e1 is None or claim.base_annual_metric is None:
        return calculations
    reported = next((v for k, v in e1.measured.items() if k.startswith("latest_reported_")), None)
    years = e1.measured.get("years_from_period_end_to_target")
    if reported is None or years is None or Decimal(reported) != claim.base_annual_metric:
        return calculations
    out = []
    for c in calculations:
        if c.name == "required_metric_cagr" and c.status == "ok":
            required = next(Decimal(v) for k, v in c.inputs.items() if k.startswith("required_"))
            end = e1.measured.get("latest_period_end")
            exact = e1.measured.get("required_cagr_from_reported")
            c = c.model_copy(update={
                # E1's own value: recomputing it from the rounded year count gave 172.63% here and 172.62% in E1
                "value": Decimal(exact) if exact is not None else annualized_rate(required, claim.base_annual_metric, Decimal(years)),
                "formula": c.formula.replace("horizon_years", "years_from_base_period_end"),
                "inputs": {**c.inputs, "years_from_base_period_end": years, "base_period_end": end},
                "assumptions": c.assumptions + [f"the base is the reported fiscal year ending {end}, so the growth runs {years} years from then to the target date"],
            })
        out.append(c)
    return out
