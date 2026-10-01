"""Typed data structures for claims, calculations, evidence and case results.

Numeric user inputs arrive as raw text in ``ClaimDraft`` so that the
validation layer (``tickercase.validation``) can report NaN, Infinity,
non-numeric and non-positive values as structured issues instead of failing
inside a parser. Validated numbers are ``Decimal`` and are serialised as
strings so JSON round-trips keep full precision.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator


DecimalStr = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str)]

RawNumber = Optional[Union[str, int, float, Decimal]]


class ValuationMethod(str, Enum):
    PRICE_TO_SALES = "price_to_sales"
    PRICE_TO_EARNINGS = "price_to_earnings"

    @property
    def metric_name(self) -> str:
        return "annual_revenue" if self is ValuationMethod.PRICE_TO_SALES else "annual_net_income"

    @property
    def multiple_name(self) -> str:
        return "assumed_price_to_sales" if self is ValuationMethod.PRICE_TO_SALES else "assumed_price_to_earnings"


class DataMode(str, Enum):
    """Where a value came from in this run."""

    USER_INPUT = "user_input"  # typed by the user: assumption or manual reference value
    LIVE = "live"  # fetched from the source during this run
    REPLAY = "replay"  # read from a recorded live snapshot
    SYNTHETIC = "synthetic"  # hand-written example data, never real


class ClaimDraft(BaseModel):
    """Editable form state. Everything is optional; validation decides what is usable."""

    model_config = ConfigDict(extra="forbid")

    claim_text: Optional[str] = None
    ticker: Optional[str] = None
    currency: Optional[str] = None
    target_price: RawNumber = None
    reference_price: RawNumber = None
    reference_price_date: Optional[str] = None
    reference_price_source: Optional[str] = None
    horizon_years: RawNumber = None
    target_assumed_shares: RawNumber = None
    current_shares: RawNumber = None
    valuation_method: Optional[str] = None
    valuation_multiple: RawNumber = None
    base_annual_metric: RawNumber = None
    base_metric_currency: Optional[str] = None
    base_metric_period: Optional[str] = None
    filings_since: Optional[str] = None

    @field_validator(
        "target_price",
        "reference_price",
        "horizon_years",
        "target_assumed_shares",
        "current_shares",
        "valuation_multiple",
        "base_annual_metric",
        mode="before",
    )
    @classmethod
    def _keep_raw_number(cls, value: Any) -> Any:
        # floats are converted through repr so 0.1 stays "0.1" rather than a binary expansion
        if isinstance(value, float):
            return repr(value)
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, bool):
            raise ValueError("boolean is not a number")
        return value

    @field_validator("reference_price_date", "filings_since", mode="before")
    @classmethod
    def _date_to_text(cls, value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        return value


class ValidationIssue(BaseModel):
    field: str
    code: str
    message: str


class MissingField(BaseModel):
    field: str
    blocking: bool
    required_for: str
    message: str


class ValidatedClaim(BaseModel):
    """Claim values after validation. Every number here was typed by the user."""

    claim_text: str
    ticker: str
    currency: str
    target_price: DecimalStr
    reference_price: DecimalStr
    reference_price_date: date
    reference_price_source: Optional[str] = None
    horizon_years: DecimalStr
    target_assumed_shares: DecimalStr
    current_shares: Optional[DecimalStr] = None
    valuation_method: ValuationMethod
    valuation_multiple: DecimalStr
    base_annual_metric: Optional[DecimalStr] = None
    base_metric_currency: Optional[str] = None
    base_metric_period: Optional[str] = None
    filings_since: Optional[date] = None


class ValidationResult(BaseModel):
    ok: bool
    claim: Optional[ValidatedClaim] = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    missing_fields: list[MissingField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fingerprint: str


class Confirmation(BaseModel):
    """Proof that the user confirmed one exact version of the inputs."""

    fingerprint: str
    confirmed_at: datetime


class ConfirmedClaim(BaseModel):
    values: ValidatedClaim
    fingerprint: str
    confirmed_at: datetime
    value_provenance: dict[str, str]


class CalculationItem(BaseModel):
    name: str
    status: Literal["ok", "not_computable"]
    value: Optional[DecimalStr] = None
    unit: str
    formula: str
    inputs: dict[str, str]
    assumptions: list[str] = Field(default_factory=list)
    reason: Optional[str] = None


class FilingRecord(BaseModel):
    provider_id: str
    ticker: str
    cik: str
    company_name: Optional[str] = None
    accession_number: str
    form_type: str
    is_amendment: bool
    filing_date: date
    report_date: Optional[date] = None
    primary_document: Optional[str] = None
    primary_document_description: Optional[str] = None
    document_url: Optional[str] = None
    filing_index_url: str
    source_response_url: str
    retrieved_at: datetime
    source_captured_at: Optional[datetime] = None
    data_mode: DataMode
    note: str = "Filing metadata only. The document has not been read and does not by itself support or refute the claim."


class CoverageInfo(BaseModel):
    requested_since: Optional[date] = None
    recent_earliest_filing_date: Optional[date] = None
    recent_latest_filing_date: Optional[date] = None
    recent_row_count: int = 0
    coverage_gap: bool = False
    older_files: list[dict[str, Any]] = Field(default_factory=list)
    message: str


class ProviderErrorRecord(BaseModel):
    provider_id: str
    code: str
    message: str
    url: Optional[str] = None
    http_status: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    data_mode: Optional[DataMode] = None


class CaseStatus(str, Enum):
    EVALUATED = "evaluated"
    EVALUATED_WITH_PROVIDER_ERRORS = "evaluated_with_provider_errors"
    BLOCKED_INVALID_INPUT = "blocked_invalid_input"
    BLOCKED_UNCONFIRMED = "blocked_unconfirmed"
    BLOCKED_CONFIRMATION_STALE = "blocked_confirmation_stale"


class CaseResult(BaseModel):
    case_id: str
    created_at: datetime
    status: CaseStatus
    input_fingerprint: str
    confirmed_claim: Optional[ConfirmedClaim] = None
    calculations: list[CalculationItem] = Field(default_factory=list)
    evidence_records: list[FilingRecord] = Field(default_factory=list)
    coverage: Optional[CoverageInfo] = None
    provider_errors: list[ProviderErrorRecord] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    missing_fields: list[MissingField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_modes: dict[str, str] = Field(default_factory=dict)
    mixed_sources: bool = False
    analysis_status: Literal["not_implemented"] = "not_implemented"
    verdict: None = None
    disclaimer: str = (
        "TickerCase shows what a claim requires under the user's own assumptions. "
        "It does not rate the claim, estimate a probability, or give investment advice."
    )
