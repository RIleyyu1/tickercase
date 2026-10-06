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
    # share count at the target date: give target_assumed_shares directly, or current_shares plus a yearly change
    share_change_rate: RawNumber = None  # yearly change, 0.01 = +1% a year
    share_change_mode: Optional[str] = None  # trend | flat | rate | absolute (how the page chose the value)
    # optional extra: price-probability reference (model output, never the verdict)
    probability_drift: RawNumber = None
    probability_volatility: RawNumber = None
    # what counts as the claim coming true: "end" = at or above the target on the date, "touch" = reaches it at any time before
    price_condition: Optional[str] = None
    # field name -> public source the current value was filled from (set by the page's prefill step)
    field_sources: Optional[dict[str, str]] = None

    @field_validator(
        "target_price",
        "reference_price",
        "horizon_years",
        "target_assumed_shares",
        "current_shares",
        "valuation_multiple",
        "base_annual_metric",
        "share_change_rate",
        "probability_drift",
        "probability_volatility",
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
    probability_drift: Optional[DecimalStr] = None
    probability_volatility: Optional[DecimalStr] = None
    share_change_rate: Optional[DecimalStr] = None
    share_change_mode: Optional[str] = None
    target_shares_derived: bool = False  # True when target_assumed_shares = current_shares x (1 + rate) ** horizon
    price_condition: Literal["end", "touch"] = "end"
    field_sources: dict[str, str] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    ok: bool
    claim: Optional[ValidatedClaim] = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    missing_fields: list[MissingField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fingerprint: str
    warnings_zh: list[str] = Field(default_factory=list)


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
    message_zh: str = ""


class ProviderErrorRecord(BaseModel):
    provider_id: str
    code: str
    message: str
    url: Optional[str] = None
    http_status: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    data_mode: Optional[DataMode] = None


class MetricPoint(BaseModel):
    """One reported value from SEC XBRL company facts."""

    period_start: Optional[date] = None  # None for point-in-time values such as shares outstanding
    period_end: date
    value: DecimalStr
    unit: str
    concept: str
    form: str
    accession: str
    filed: date
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None


class ReportedFacts(BaseModel):
    """Annual revenue, annual net income and shares outstanding as reported to the SEC."""

    provider_id: str
    ticker: str
    cik: str
    company_name: Optional[str] = None
    revenue: list[MetricPoint] = Field(default_factory=list)
    net_income: list[MetricPoint] = Field(default_factory=list)
    shares_outstanding: list[MetricPoint] = Field(default_factory=list)
    source_url: str
    retrieved_at: datetime
    source_captured_at: Optional[datetime] = None
    data_mode: DataMode
    notes: list[str] = Field(default_factory=list)


class PricePoint(BaseModel):
    day: date
    close: DecimalStr


class MarketSnapshot(BaseModel):
    """Daily closing prices for the ticker from a public quote source."""

    provider_id: str
    symbol: str
    currency: Optional[str] = None
    last_close: DecimalStr
    last_date: date
    history_start: date
    observations: int
    annualized_volatility: Optional[DecimalStr] = None
    volatility_window: str
    price_series: list[PricePoint] = Field(default_factory=list)  # weekly sample for charts
    source_url: str
    retrieved_at: datetime
    source_captured_at: Optional[datetime] = None
    data_mode: DataMode
    note: str = "Unofficial public quote endpoint; no service guarantee. Prices are as published by the source."


class SourceRef(BaseModel):
    label: str
    url: Optional[str] = None
    filed: Optional[date] = None
    accession: Optional[str] = None
    data_mode: Optional[DataMode] = None


Stance = Literal["supporting", "contrary", "missing", "neutral"]


class EvidenceItem(BaseModel):
    """One deterministic check that compares a claim requirement with dated public data."""

    id: str
    check: str
    stance: Stance
    title: str
    detail: str
    title_zh: str = ""
    detail_zh: str = ""
    measured: dict[str, str] = Field(default_factory=dict)
    rule: Optional[str] = None
    rule_zh: Optional[str] = None
    sources: list[SourceRef] = Field(default_factory=list)
    as_of: Optional[date] = None


VerdictLabel = Literal["supported_today", "partially_supported", "not_supported_today", "insufficiently_specified"]

VERDICT_DISPLAY = {
    "supported_today": "Supported Today",
    "partially_supported": "Partially Supported",
    "not_supported_today": "Not Supported Today",
    "insufficiently_specified": "Insufficiently Specified",
}
VERDICT_DISPLAY_ZH = {
    "supported_today": "目前证据支持",
    "partially_supported": "部分支持",
    "not_supported_today": "目前证据不支持",
    "insufficiently_specified": "信息不足，无法判断",
}


class Verdict(BaseModel):
    """Evidence-as-of classification (OA.13). Describes the evidence, not the future price."""

    label: VerdictLabel
    display: str
    as_of: date
    rationale: list[str]
    limitations: list[str]
    display_zh: str = ""
    rationale_zh: list[str] = Field(default_factory=list)
    limitations_zh: list[str] = Field(default_factory=list)
    basis: list[str]  # evidence item ids
    rules_version: str


class RecheckCondition(BaseModel):
    """Observable event that justifies a new review of the case (OA.14)."""

    id: str
    trigger: str
    watch: str
    trigger_zh: str = ""
    watch_zh: str = ""
    threshold: Optional[str] = None
    linked_to: list[str] = Field(default_factory=list)  # evidence ids or input field names


class ProbabilityPoint(BaseModel):
    price: DecimalStr
    probability_at_or_above: float


class ProbabilityReference(BaseModel):
    """Optional extra: probability of the price being at or above levels under a lognormal model.

    A model output under stated assumptions. It is not part of the verdict.
    """

    status: Literal["ok", "not_computable"]
    model: str = "lognormal (geometric Brownian motion), constant drift and volatility"
    spot: Optional[DecimalStr] = None
    drift: Optional[DecimalStr] = None
    volatility: Optional[DecimalStr] = None
    volatility_source: Optional[str] = None
    horizon_years: Optional[DecimalStr] = None
    target_price: Optional[DecimalStr] = None
    target_probability: Optional[float] = None
    median_price: Optional[DecimalStr] = None
    p10_price: Optional[DecimalStr] = None
    p90_price: Optional[DecimalStr] = None
    points: list[ProbabilityPoint] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reason: Optional[str] = None
    assumptions_zh: list[str] = Field(default_factory=list)
    limitations_zh: list[str] = Field(default_factory=list)
    reason_zh: Optional[str] = None


class Sensitivity(BaseModel):
    """Required yearly growth of the valuation metric for alternative multiples (rows) and horizons (columns)."""

    metric: str
    base_value: DecimalStr
    base_label: str
    multiples: list[str]
    horizons: list[str]
    required_cagr: list[list[Optional[str]]]
    assumed_multiple: str
    assumed_horizon: str
    reported_cagr: Optional[str] = None


class ClaimExtraction(BaseModel):
    """Candidate fields read from the claim sentence by rules (OA.2-OA.3). Reviewed and confirmed by the user."""

    text: str
    ticker: Optional[str] = None
    ticker_text: Optional[str] = None
    target_price: Optional[DecimalStr] = None
    target_multiple: Optional[DecimalStr] = None  # "doubles", "10x": resolved against the reference price
    target_text: Optional[str] = None
    horizon_years: Optional[DecimalStr] = None
    target_date: Optional[date] = None
    horizon_text: Optional[str] = None
    currency: Optional[str] = None
    condition: Optional[Literal["end", "touch"]] = None  # "冲到 / 触及 / within 3 years" -> touch; "3 年后 / by 2030" -> end
    condition_text: Optional[str] = None
    notes_en: list[str] = Field(default_factory=list)
    notes_zh: list[str] = Field(default_factory=list)


class Text(BaseModel):
    en: str
    zh: str


class ReportRow(BaseModel):
    signal: Text
    data: Text
    meaning: Text
    tone: Literal["good", "bad", "neutral", "missing"] = "neutral"


class ReportLayer(BaseModel):
    title: Text
    rows: list[ReportRow] = Field(default_factory=list)


class ScenarioRow(BaseModel):
    name: Text
    assumptions: Text
    price: Optional[str] = None
    vs_target: Optional[str] = None
    meaning: Text


class MonitorRow(BaseModel):
    signal: Text
    current: Text
    threshold: Text
    meaning: Text


class PlainReport(BaseModel):
    """Layered plain-language summary of the case, built only from the deterministic results."""

    headline: Text
    verdict_meaning: Text
    layers: list[ReportLayer] = Field(default_factory=list)
    agreements: list[Text] = Field(default_factory=list)
    divergences: list[Text] = Field(default_factory=list)
    time_view: list[ReportRow] = Field(default_factory=list)
    scenarios: list[ScenarioRow] = Field(default_factory=list)
    scenario_note: Optional[Text] = None
    upside: list[Text] = Field(default_factory=list)
    downside: list[Text] = Field(default_factory=list)
    monitor: list[MonitorRow] = Field(default_factory=list)


# ---------------------------------------------------------------- oracle layers (v0.5)


class _Sourced(BaseModel):
    source_url: str
    retrieved_at: datetime
    source_captured_at: Optional[datetime] = None
    data_mode: DataMode


class OptionQuote(BaseModel):
    strike: DecimalStr
    implied_volatility: Optional[float] = None
    open_interest: int = 0
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None


class TermPoint(BaseModel):
    """At-the-money implied volatility of one expiry (the option market's volatility term structure)."""

    expiry: date
    atm_iv: float


class OptionsSnapshot(_Sourced):
    """Call chain of the expiry closest to (and preferably after) the claim's target date."""

    provider_id: str = "yahoo_finance_options"
    symbol: str
    underlying_price: Optional[DecimalStr] = None
    expiry: date
    days_to_expiry: int
    expirations: list[date] = Field(default_factory=list)
    atm_iv: Optional[float] = None
    target_iv: Optional[float] = None
    target_iv_extrapolated: bool = False
    max_strike: Optional[DecimalStr] = None
    oi_at_or_above_target: int = 0
    total_call_oi: int = 0
    calls: list[OptionQuote] = Field(default_factory=list)
    term: list[TermPoint] = Field(default_factory=list)  # ATM IV by expiry, for event-move estimates


class BaseRate(BaseModel):
    """How many US-listed companies of a similar size reached the required growth (SEC XBRL frames)."""

    metric: str
    start_year: int
    end_year: int
    size_low: DecimalStr
    size_high: DecimalStr
    companies: int
    achieved: int
    rate: Optional[float] = None
    required_cagr: DecimalStr
    percentile_of_required: Optional[float] = None
    median_cagr: Optional[DecimalStr] = None
    p90_cagr: Optional[DecimalStr] = None
    examples: list[dict[str, str]] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    data_mode: DataMode
    note: str = "Survivors only: companies that reported in both years. Acquired or delisted companies are missing, which makes the rate optimistic."


class PriceBaseRate(_Sourced):
    """Share of past windows of the same length in which this stock rose at least the required amount."""

    symbol: str
    window_months: int
    windows: int
    hits: int
    rate: Optional[float] = None
    required_return: DecimalStr
    median_return: Optional[DecimalStr] = None
    best_return: Optional[DecimalStr] = None
    history_start: date
    touch_hits: Optional[int] = None  # windows whose highest month-end close reached the target


class BenchmarkReturn(BaseModel):
    symbol: str
    label: str
    start: date
    end: date
    total_return: DecimalStr
    annual_return: DecimalStr
    data_mode: DataMode


class InsiderTransaction(BaseModel):
    filed: date
    owner: str
    role: str
    code: str
    shares: DecimalStr
    price: Optional[DecimalStr] = None
    value: Optional[DecimalStr] = None
    acquired: bool
    plan_10b5_1: bool = False
    url: str


class InsiderSummary(BaseModel):
    """Form 4 transactions by type. Only codes P (open-market purchase) and S (open-market sale) are trades by choice."""

    window_start: date
    filings_listed: int
    filings_read: int
    filings_failed: int = 0
    purchases: int = 0
    purchase_value: DecimalStr = Decimal(0)
    sales: int = 0
    sale_value: DecimalStr = Decimal(0)
    plan_sale_value: DecimalStr = Decimal(0)
    buyers: int = 0
    sellers: int = 0
    grants: int = 0
    exercises: int = 0
    tax_withholding: int = 0
    other: int = 0
    net_open_market_value: DecimalStr = Decimal(0)
    transactions: list[InsiderTransaction] = Field(default_factory=list)
    data_mode: DataMode


class PredictionMarket(BaseModel):
    question: str
    probability_yes: Optional[float] = None
    end_date: Optional[datetime] = None
    volume: Optional[float] = None
    url: str


class PredictionMarkets(_Sourced):
    query: str
    markets: list[PredictionMarket] = Field(default_factory=list)


class SentimentSnapshot(_Sourced):
    score: float
    rating: str
    previous_week: Optional[float] = None
    previous_month: Optional[float] = None
    as_of: Optional[datetime] = None


class ProbabilityMethod(BaseModel):
    id: str
    name: "Text"
    status: Literal["ok", "not_computable"]
    probability: Optional[float] = None  # P(price at the target date >= target)
    touch_probability: Optional[float] = None  # P(price reaches the target at any time before the date)
    measures: "Text"
    detail: "Text"
    inputs: dict[str, str] = Field(default_factory=dict)
    limitations: list["Text"] = Field(default_factory=list)


class LadderRow(BaseModel):
    level: DecimalStr
    label: "Text"
    options_p: Optional[float] = None
    model_p: Optional[float] = None
    options_touch: Optional[float] = None
    model_touch: Optional[float] = None


class OracleSummary(BaseModel):
    """Probability that the claim comes true, from several independent methods compared side by side."""

    target_price: DecimalStr
    target_date: date
    low: Optional[float] = None
    high: Optional[float] = None
    tier: Literal["lottery", "low", "possible", "likely", "unknown"]
    tier_label: "Text"
    methods: list[ProbabilityMethod] = Field(default_factory=list)
    ladder: list[LadderRow] = Field(default_factory=list)
    agreement: "Text"
    risk_free_rate: Optional[DecimalStr] = None
    condition: Literal["end", "touch"] = "end"  # which probability the range uses
    spot: Optional[float] = None
    base_volatility: Optional[float] = None  # historical volatility, used by the scenario panel
    implied_volatility: Optional[float] = None


class EventMove(BaseModel):
    """Extra move the option market prices around one dated event, from the jump in ATM implied variance."""

    event_date: date
    before_expiry: Optional[date] = None
    after_expiry: Optional[date] = None
    status: Literal["ok", "not_priced", "not_computable"]
    move: Optional[float] = None  # one-standard-deviation move attributed to the event (0.25 = about ±25%)
    gap_days: Optional[int] = None
    note: Optional["Text"] = None


class ScenarioResult(BaseModel):
    """Probability under the user's event scenario: one dated jump on top of ordinary volatility."""

    probability: float
    p_if_success: float
    p_if_failure: float
    break_even: Optional[float] = None  # success probability needed for 50%; None if out of reach
    market_implied: Optional[float] = None  # success probability that reproduces the option-implied value


class Fact(BaseModel):
    """One numbered fact handed to the narrative writer; every value comes from a fetch or a computation."""

    id: str
    label_en: str
    label_zh: str
    value: str
    unit: str = ""
    source: str = ""


class NarrativeSentence(BaseModel):
    zh: str = ""  # since v0.8 a narrative is written in one language; the other field stays empty
    en: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    status: Literal["verified", "qualitative", "cited_elsewhere", "unsupported"] = "qualitative"
    problems: list[str] = Field(default_factory=list)


class Narrative(BaseModel):
    """AI-written narrative, checked number by number against the fact table."""

    status: Literal["ok", "refused", "error", "not_configured"]
    model: str
    created_at: datetime
    sections: dict[str, list[NarrativeSentence]] = Field(default_factory=dict)
    facts: list[Fact] = Field(default_factory=list)
    total: int = 0
    verified: int = 0
    unsupported: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    error: Optional[str] = None
    language: Literal["zh", "en", "both"] = "both"  # "both": written before v0.8 with zh and en together
    effort: Optional[str] = None
    cache_key: Optional[str] = None  # hash of prompt version, model, effort, language and fact table
    reused_from: Optional[str] = None  # case id whose narrative was reused instead of calling the API


class ReferenceSuggestion(BaseModel):
    value: str
    source: str


class ReferenceSnapshot(BaseModel):
    """Public reference values offered to the user before confirmation (page prefill)."""

    ticker: str
    company_name: Optional[str] = None
    suggestions: dict[str, ReferenceSuggestion] = Field(default_factory=dict)
    revenue_suggestion: Optional[ReferenceSuggestion] = None
    net_income_suggestion: Optional[ReferenceSuggestion] = None
    period_suggestion: Optional[ReferenceSuggestion] = None
    # default assumptions offered when the user leaves them empty; shown and confirmed like any input
    assumption_suggestions: dict[str, ReferenceSuggestion] = Field(default_factory=dict)
    current_ps: Optional[DecimalStr] = None
    current_pe: Optional[DecimalStr] = None
    provider_errors: list[ProviderErrorRecord] = Field(default_factory=list)
    data_modes: dict[str, str] = Field(default_factory=dict)


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
    reported_facts: Optional[ReportedFacts] = None
    market: Optional[MarketSnapshot] = None
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    verdict: Optional[Verdict] = None
    recheck_conditions: list[RecheckCondition] = Field(default_factory=list)
    probability: Optional[ProbabilityReference] = None
    sensitivity: Optional[Sensitivity] = None
    report: Optional[PlainReport] = None
    options: Optional[OptionsSnapshot] = None
    base_rate: Optional[BaseRate] = None
    price_base_rate: Optional[PriceBaseRate] = None
    benchmarks: list[BenchmarkReturn] = Field(default_factory=list)
    insiders: Optional[InsiderSummary] = None
    prediction_markets: Optional[PredictionMarkets] = None
    sentiment: Optional[SentimentSnapshot] = None
    oracle: Optional[OracleSummary] = None
    data_steps: dict[str, str] = Field(default_factory=dict)  # step -> "ok" | "failed"
    narrative: Optional[Narrative] = None  # the latest narrative written, any language
    narratives: dict[str, Narrative] = Field(default_factory=dict)  # by language ("zh", "en")
    provider_errors: list[ProviderErrorRecord] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    missing_fields: list[MissingField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    warnings_zh: list[str] = Field(default_factory=list)
    data_modes: dict[str, str] = Field(default_factory=dict)
    mixed_sources: bool = False
    # "not_implemented" is kept so cases saved by v0.1 still load
    analysis_status: Literal["not_run", "deterministic_rules", "not_implemented"] = "not_run"
    disclaimer: str = (
        "TickerCase checks what a claim requires under the user's assumptions against dated public data. "
        "The verdict describes the evidence as of a date; it is not a price prediction, a guarantee or investment advice. "
        "The optional probability section is a model output under stated assumptions and is not part of the verdict."
    )
