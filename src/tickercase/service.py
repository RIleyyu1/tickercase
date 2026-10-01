"""Case workflow: validate -> require matching confirmation -> calculate -> SEC filings -> CaseResult.

Calculations are finished before any network call, so a provider failure never
removes them. Provider failures are reported as errors; no other data source
is substituted for a failed one.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Callable, Optional

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
)
from .providers.base import ProviderError
from .providers.sec import SecFilingProvider, SecFilingQuery
from .storage import CaseStore
from .validation import fingerprint, validate_draft

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
}

SEC_PROVIDER_ID = SecFilingProvider.provider_id


def build_fetcher(mode: str, settings: Settings) -> JsonFetcher:
    if mode not in SEC_MODES:
        raise ValueError(f"unknown SEC mode '{mode}'; expected one of {SEC_MODES}")
    if mode == "synthetic":
        return ReplayHttpClient(settings.synthetic_dir)
    if mode == "replay":
        return ReplayHttpClient(settings.snapshot_dir)
    live = LiveHttpClient(
        user_agent=settings.sec_user_agent,
        timeout_seconds=settings.timeout_seconds,
        max_retries=settings.max_retries,
        min_interval_seconds=settings.min_interval_seconds,
        max_retry_after_seconds=settings.max_retry_after_seconds,
        cache_ttl_seconds={
            "https://www.sec.gov/files/company_tickers.json": 24 * 3600,
            "https://data.sec.gov/submissions/": 600,
        },
    )
    if mode == "record":
        return RecordingHttpClient(live, settings.snapshot_dir)
    return live


def _mode_enum(mode: str) -> DataMode:
    return {"live": DataMode.LIVE, "record": DataMode.LIVE, "replay": DataMode.REPLAY, "synthetic": DataMode.SYNTHETIC}[mode]


class CaseService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        fetcher_factory: Optional[Callable[[str], JsonFetcher]] = None,
        store: Optional[CaseStore] = None,
        now: Callable[[], datetime] = utcnow,
        today: Optional[Callable[[], date]] = None,
    ):
        self.settings = settings or load_settings()
        self._factory = fetcher_factory or (lambda mode: build_fetcher(mode, self.settings))
        self._fetchers: dict[str, JsonFetcher] = {}
        self.store = store
        self.now = now
        self.today = today or (lambda: self.now().date())

    def _fetcher(self, mode: str) -> JsonFetcher:
        if mode not in self._fetchers:
            self._fetchers[mode] = self._factory(mode)
        return self._fetchers[mode]

    def evaluate(self, draft: ClaimDraft, confirmation: Optional[Confirmation], *, sec_mode: str) -> CaseResult:
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
        )

        if not validation.ok:
            return self._finish(CaseResult(status=CaseStatus.BLOCKED_INVALID_INPUT, **base))
        if confirmation is None:
            base["warnings"].append("inputs have not been confirmed; evaluation not started")
            return self._finish(CaseResult(status=CaseStatus.BLOCKED_UNCONFIRMED, **base))
        if confirmation.fingerprint != current_fp:
            base["warnings"].append("inputs changed after confirmation; confirm the current inputs before evaluating")
            return self._finish(CaseResult(status=CaseStatus.BLOCKED_CONFIRMATION_STALE, **base))

        claim = validation.claim
        assert claim is not None
        confirmed = ConfirmedClaim(
            values=claim,
            fingerprint=current_fp,
            confirmed_at=confirmation.confirmed_at,
            value_provenance={k: v for k, v in USER_INPUT_PROVENANCE.items() if getattr(claim, k, None) is not None},
        )
        calculations = calculate(claim)

        provider_errors: list[ProviderErrorRecord] = []
        records = []
        coverage = None
        sec_status = "unavailable"
        warnings = base["warnings"]
        try:
            fetcher = self._fetcher(sec_mode)
            provider = SecFilingProvider(fetcher)
            sec = provider.fetch_filings(SecFilingQuery(ticker=claim.ticker, since=claim.filings_since))
            records = sec.records
            coverage = sec.coverage
            warnings.extend(sec.warnings)
            modes = {r.data_mode.value for r in records}
            sec_status = ",".join(sorted(modes)) if modes else _mode_enum(sec_mode).value
        except FetchError as exc:
            provider_errors.append(
                ProviderErrorRecord(
                    provider_id=SEC_PROVIDER_ID, code=exc.code, message=exc.message, url=exc.url,
                    http_status=exc.http_status, retry_after_seconds=exc.retry_after_seconds,
                    data_mode=exc.data_mode or _mode_enum(sec_mode),
                )
            )
        except ProviderError as exc:
            provider_errors.append(
                ProviderErrorRecord(provider_id=SEC_PROVIDER_ID, code=exc.code, message=exc.message, url=exc.url, data_mode=_mode_enum(sec_mode))
            )
        except ValueError as exc:
            provider_errors.append(ProviderErrorRecord(provider_id=SEC_PROVIDER_ID, code="config_error", message=str(exc)))

        if provider_errors:
            warnings.append("SEC filing data is unavailable for this run; calculations above use only your inputs")
        if sec_status == DataMode.SYNTHETIC.value:
            warnings.append("SEC section uses synthetic example data, not real SEC filings")

        result = CaseResult(
            status=CaseStatus.EVALUATED_WITH_PROVIDER_ERRORS if provider_errors else CaseStatus.EVALUATED,
            confirmed_claim=confirmed,
            calculations=calculations,
            evidence_records=records,
            coverage=coverage,
            provider_errors=provider_errors,
            data_modes={"claim_inputs": DataMode.USER_INPUT.value, "sec_filings": sec_status},
            mixed_sources=bool(records),
            **base,
        )
        return self._finish(result)

    def _finish(self, result: CaseResult) -> CaseResult:
        if self.store is not None:
            self.store.save(result)
        return result
