"""Input validation, missing-field detection and confirmation fingerprints.

Rules:
- Core fields must be present and valid before a case can be confirmed.
- Assumptions (horizon, multiple, target-period share count) are never filled in.
- Optional base-period metric only affects the growth-rate calculation.
- Any edit to the inputs changes the fingerprint, which invalidates an
  earlier confirmation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from .models import (
    ClaimDraft,
    Confirmation,
    MissingField,
    ValidatedClaim,
    ValidationIssue,
    ValidationResult,
    ValuationMethod,
)

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

CORE_FIELDS: dict[str, str] = {
    "claim_text": "the claim being checked",
    "ticker": "ticker symbol",
    "currency": "currency of prices and amounts",
    "target_price": "target price",
    "reference_price": "reference price",
    "reference_price_date": "date of the reference price",
    "horizon_years": "time horizon in years",
    "target_assumed_shares": "assumed share count at the target date",
    "valuation_method": "valuation method (price_to_sales or price_to_earnings)",
    "valuation_multiple": "assumed valuation multiple",
}

POSITIVE_NUMBERS = (
    "target_price",
    "reference_price",
    "horizon_years",
    "target_assumed_shares",
    "valuation_multiple",
)


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalized_draft(draft: ClaimDraft) -> dict[str, Optional[str]]:
    """Canonical text form of the draft used for fingerprints."""
    out: dict[str, Optional[str]] = {}
    for name, value in draft.model_dump().items():
        if name == "field_sources":
            cleaned = {k: v.strip() for k, v in (value or {}).items() if isinstance(v, str) and v.strip()}
            out[name] = json.dumps(cleaned, sort_keys=True, ensure_ascii=False) if cleaned else None
            continue
        text = _clean_text(None if value is None else str(value))
        if text is not None and name in ("ticker", "currency", "base_metric_currency"):
            text = text.upper()
        if text is not None and name == "valuation_method":
            text = text.lower()
        out[name] = text
    return out


def fingerprint(draft: ClaimDraft) -> str:
    payload = json.dumps(normalized_draft(draft), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_decimal(field: str, raw: Optional[str], issues: list[ValidationIssue], *, positive: bool) -> Optional[Decimal]:
    text = _clean_text(raw)
    if text is None:
        return None
    cleaned = text.replace(",", "").replace("_", "").replace(" ", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        issues.append(ValidationIssue(field=field, code="not_a_number", message=f"{field}: '{text}' is not a number"))
        return None
    if value.is_nan():
        issues.append(ValidationIssue(field=field, code="nan_not_allowed", message=f"{field}: NaN is not allowed"))
        return None
    if value.is_infinite():
        issues.append(ValidationIssue(field=field, code="infinity_not_allowed", message=f"{field}: Infinity is not allowed"))
        return None
    if positive and value <= 0:
        issues.append(ValidationIssue(field=field, code="must_be_positive", message=f"{field}: must be greater than 0 (got {text})"))
        return None
    return value


def _parse_date(field: str, raw: Optional[str], issues: list[ValidationIssue], today: date) -> Optional[date]:
    text = _clean_text(raw)
    if text is None:
        return None
    try:
        value = date.fromisoformat(text)
    except ValueError:
        issues.append(ValidationIssue(field=field, code="invalid_date", message=f"{field}: '{text}' is not an ISO date (YYYY-MM-DD)"))
        return None
    if value > today:
        issues.append(ValidationIssue(field=field, code="date_in_future", message=f"{field}: {value} is after today ({today})"))
        return None
    return value


def validate_draft(draft: ClaimDraft, *, today: Optional[date] = None) -> ValidationResult:
    today = today or datetime.now(timezone.utc).date()
    norm = normalized_draft(draft)
    issues: list[ValidationIssue] = []
    missing: list[MissingField] = []
    warnings: list[str] = []

    for name, label in CORE_FIELDS.items():
        if norm[name] is None:
            missing.append(MissingField(field=name, blocking=True, required_for="all calculations", message=f"missing {label}"))

    ticker = norm["ticker"]
    if ticker is not None and not TICKER_RE.match(ticker):
        issues.append(ValidationIssue(field="ticker", code="invalid_ticker", message=f"ticker '{ticker}' has an unexpected format"))

    currency = norm["currency"]
    if currency is not None and not CURRENCY_RE.match(currency):
        issues.append(ValidationIssue(field="currency", code="invalid_currency", message=f"currency '{currency}' must be a 3-letter ISO code"))

    method: Optional[ValuationMethod] = None
    if norm["valuation_method"] is not None:
        try:
            method = ValuationMethod(norm["valuation_method"])
        except ValueError:
            issues.append(
                ValidationIssue(
                    field="valuation_method",
                    code="unsupported_method",
                    message="valuation_method must be price_to_sales or price_to_earnings",
                )
            )

    numbers: dict[str, Optional[Decimal]] = {}
    for name in POSITIVE_NUMBERS:
        numbers[name] = parse_decimal(name, norm[name], issues, positive=True)
    numbers["current_shares"] = parse_decimal("current_shares", norm["current_shares"], issues, positive=True)
    # base metric may be zero or negative (e.g. a net loss); growth rate handles that case
    numbers["base_annual_metric"] = parse_decimal("base_annual_metric", norm["base_annual_metric"], issues, positive=False)

    drift = parse_decimal("probability_drift", norm["probability_drift"], issues, positive=False)
    prob_vol = parse_decimal("probability_volatility", norm["probability_volatility"], issues, positive=True)
    if drift is not None and not Decimal("-1") <= drift <= Decimal("1"):
        issues.append(ValidationIssue(field="probability_drift", code="out_of_range", message="probability_drift is a yearly rate between -1 and 1 (0.07 = 7%)"))
        drift = None
    if prob_vol is not None and prob_vol > Decimal("3"):
        issues.append(ValidationIssue(field="probability_volatility", code="out_of_range", message="probability_volatility is a yearly rate at most 3 (0.35 = 35%)"))
        prob_vol = None
    if prob_vol is not None and norm["probability_drift"] is None:
        warnings.append("probability_volatility is set but probability_drift is empty; the probability reference is only computed when a drift is given")

    sources = draft.field_sources or {}
    unknown = sorted(k for k in sources if k not in ClaimDraft.model_fields or k == "field_sources")
    if unknown:
        issues.append(ValidationIssue(field="field_sources", code="unknown_field", message=f"field_sources names unknown fields: {', '.join(unknown)}"))

    ref_date = _parse_date("reference_price_date", norm["reference_price_date"], issues, today)
    since = _parse_date("filings_since", norm["filings_since"], issues, today)

    base_currency = norm["base_metric_currency"]
    if base_currency is not None and not CURRENCY_RE.match(base_currency):
        issues.append(ValidationIssue(field="base_metric_currency", code="invalid_currency", message=f"base_metric_currency '{base_currency}' must be a 3-letter ISO code"))
    elif base_currency is not None and currency is not None and base_currency != currency:
        issues.append(
            ValidationIssue(
                field="base_metric_currency",
                code="currency_mismatch",
                message=f"base metric currency {base_currency} differs from price currency {currency}; convert it first, no FX conversion is applied",
            )
        )

    metric_label = method.metric_name if method else "base annual metric"
    if norm["base_annual_metric"] is None:
        missing.append(
            MissingField(
                field="base_annual_metric",
                blocking=False,
                required_for="required_metric_cagr",
                message=f"no base-period {metric_label}; growth rate will not be calculated",
            )
        )
    elif base_currency is None:
        missing.append(
            MissingField(
                field="base_metric_currency",
                blocking=False,
                required_for="required_metric_cagr",
                message="base metric currency not stated; growth rate will not be calculated until it is",
            )
        )
    if norm["base_annual_metric"] is not None and norm["base_metric_period"] is None:
        warnings.append("base_metric_period is empty; record which fiscal year the base metric covers")
    if norm["filings_since"] is None:
        missing.append(
            MissingField(
                field="filings_since",
                blocking=False,
                required_for="filing coverage check",
                message="no start date for the filing window; coverage gap cannot be checked",
            )
        )
    if norm["reference_price_source"] is None and norm["reference_price"] is not None:
        warnings.append("reference_price_source is empty; the reference price is a manual value with no recorded source")
    if currency is not None and currency != "USD" and CURRENCY_RE.match(currency):
        warnings.append("SEC XBRL amounts are compared in USD only; with another currency the evidence checks report missing data")

    fp = fingerprint(draft)
    blocking_missing = any(m.blocking for m in missing)
    if issues or blocking_missing:
        return ValidationResult(ok=False, issues=issues, missing_fields=missing, warnings=warnings, fingerprint=fp)

    claim = ValidatedClaim(
        claim_text=norm["claim_text"],
        ticker=ticker,
        currency=currency,
        target_price=numbers["target_price"],
        reference_price=numbers["reference_price"],
        reference_price_date=ref_date,
        reference_price_source=norm["reference_price_source"],
        horizon_years=numbers["horizon_years"],
        target_assumed_shares=numbers["target_assumed_shares"],
        current_shares=numbers["current_shares"],
        valuation_method=method,
        valuation_multiple=numbers["valuation_multiple"],
        base_annual_metric=numbers["base_annual_metric"],
        base_metric_currency=base_currency,
        base_metric_period=norm["base_metric_period"],
        filings_since=since,
        probability_drift=drift,
        probability_volatility=prob_vol,
        field_sources={k: v.strip() for k, v in sources.items() if isinstance(v, str) and v.strip() and norm.get(k) is not None},
    )
    return ValidationResult(ok=True, claim=claim, issues=[], missing_fields=missing, warnings=warnings, fingerprint=fp)


class ConfirmationError(ValueError):
    def __init__(self, result: ValidationResult):
        super().__init__("inputs are not valid; fix the listed issues before confirming")
        self.result = result


def confirm(draft: ClaimDraft, *, now: Optional[Callable[[], datetime]] = None, today: Optional[date] = None) -> Confirmation:
    """Create a confirmation for exactly this draft. Raises if the draft is not valid."""
    result = validate_draft(draft, today=today)
    if not result.ok:
        raise ConfirmationError(result)
    clock = now or (lambda: datetime.now(timezone.utc))
    return Confirmation(fingerprint=result.fingerprint, confirmed_at=clock())


def confirmation_state(draft: ClaimDraft, confirmation: Optional[Confirmation]) -> str:
    """Return 'unconfirmed', 'stale' or 'confirmed' for the current draft."""
    if confirmation is None:
        return "unconfirmed"
    if confirmation.fingerprint != fingerprint(draft):
        return "stale"
    return "confirmed"


def draft_from_mapping(data: dict[str, Any]) -> ClaimDraft:
    return ClaimDraft.model_validate({k: v for k, v in data.items() if k in ClaimDraft.model_fields})
