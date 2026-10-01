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
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Optional, TypeVar

from .analysis import RULES_VERSION, analyze
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
    ReferenceSnapshot,
    ReferenceSuggestion,
)
from .probability import probability_reference
from .providers.base import ProviderError
from .providers.market import CHART_URL, YahooChartProvider
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
    "probability_drift": "user_input:assumption",
    "probability_volatility": "user_input:assumption",
}

DEFAULT_PREFIX = "default_assumption:"
SEC_PROVIDER_ID = SecFilingProvider.provider_id
FACTS_PROVIDER_ID = SecCompanyFactsProvider.provider_id
MARKET_PROVIDER_ID = YahooChartProvider.provider_id
SOURCES = ("sec", "market")

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
    if source == "market":
        live = LiveHttpClient(
            user_agent=settings.market_user_agent,
            require_contact_email=False,
            service_name="Yahoo Finance",
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            min_interval_seconds=settings.market_min_interval_seconds,
            max_retry_after_seconds=settings.max_retry_after_seconds,
            cache_ttl_seconds={CHART_URL.split("{")[0]: 300},
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

    def evaluate(self, draft: ClaimDraft, confirmation: Optional[Confirmation], *, sec_mode: str) -> CaseResult:
        """Run one confirmed case. ``sec_mode`` is the data mode for every external source."""
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
        provenance = {k: v for k, v in USER_INPUT_PROVENANCE.items() if getattr(claim, k, None) is not None}
        for name, source in claim.field_sources.items():
            provenance[name] = source if source.startswith(DEFAULT_PREFIX) else f"public_data:{source}"
        confirmed = ConfirmedClaim(values=claim, fingerprint=current_fp, confirmed_at=confirmation.confirmed_at, value_provenance=provenance)
        calculations = calculate(claim)

        errors: list[ProviderErrorRecord] = []
        warnings = base["warnings"]
        sec_fetcher = self._guard(SEC_PROVIDER_ID, mode, errors, lambda: self._fetcher(mode, "sec"))
        filings = facts = market = None
        if sec_fetcher is not None:
            filing_provider = SecFilingProvider(sec_fetcher)
            filings = self._guard(SEC_PROVIDER_ID, mode, errors, lambda: filing_provider.fetch_filings(
                SecFilingQuery(ticker=claim.ticker, since=claim.filings_since)))
            facts = self._guard(FACTS_PROVIDER_ID, mode, errors, lambda: SecCompanyFactsProvider(sec_fetcher, filing_provider).fetch_facts(claim.ticker))
        market = self._guard(MARKET_PROVIDER_ID, mode, errors, lambda: YahooChartProvider(self._fetcher(mode, "market")).fetch_history(claim.ticker))

        records = filings.records if filings else []
        for w in filings.warnings if filings else []:
            warn(w, f"SEC 申报：{w}")
        for n in facts.notes if facts else []:
            warn(f"SEC XBRL: {n}", f"SEC XBRL：{n}")

        outcome = analyze(claim, calculations, facts=facts, market=market, filings=records, today=self.today())
        for w in outcome.warnings:
            warn(w.en, w.zh)

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
            probability=probability_reference(claim, market),
            sensitivity=outcome.sensitivity,
            provider_errors=errors,
            data_modes=data_modes,
            mixed_sources=bool(records or facts or market),
            analysis_status="deterministic_rules",
            **base,
        )
        return self._finish(result)

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
            snap.suggestions["reference_price"] = ReferenceSuggestion(value=format(market.last_close, "f"), source=label)
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
