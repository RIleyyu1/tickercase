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
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
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
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

CORE_FIELDS: dict[str, str] = {
    "claim_text": "the claim being checked",
    "ticker": "ticker symbol",
    "currency": "currency of prices and amounts",
    "target_price": "target price",
    "reference_price": "reference price",
    "reference_price_date": "date of the reference price",
    "horizon_years": "time horizon in years",
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
SHARE_MODES = ("trend", "flat", "rate", "absolute")
PRICE_CONDITIONS = ("end", "touch")
SHARE_RATE_MIN, SHARE_RATE_MAX = Decimal("-0.5"), Decimal("1")
SHARE_RATE_WARN_LOW, SHARE_RATE_WARN_HIGH = Decimal("-0.10"), Decimal("0.20")


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
        if text is not None and name in ("valuation_method", "price_condition"):
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
    warnings: list[tuple[str, str]] = []  # (English, Chinese)

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

    share_rate = parse_decimal("share_change_rate", norm["share_change_rate"], issues, positive=False)
    if share_rate is not None and not SHARE_RATE_MIN <= share_rate <= SHARE_RATE_MAX:
        issues.append(ValidationIssue(field="share_change_rate", code="out_of_range",
                                      message="share_change_rate is a yearly change between -0.5 and 1 (0.01 = +1% a year)"))
        share_rate = None
    share_mode = norm["share_change_mode"]
    if share_mode is not None and share_mode not in SHARE_MODES:
        issues.append(ValidationIssue(field="share_change_mode", code="unsupported_mode", message=f"share_change_mode must be one of {SHARE_MODES}"))
        share_mode = None
    condition = norm["price_condition"] or "end"
    if condition not in PRICE_CONDITIONS:
        issues.append(ValidationIssue(field="price_condition", code="unsupported_mode", message=f"price_condition must be one of {PRICE_CONDITIONS}"))
        condition = "end"
    target_shares, shares_derived = _target_shares(numbers, share_rate, share_mode, norm, missing, warnings)

    drift = parse_decimal("probability_drift", norm["probability_drift"], issues, positive=False)
    prob_vol = parse_decimal("probability_volatility", norm["probability_volatility"], issues, positive=True)
    if drift is not None and not Decimal("-1") <= drift <= Decimal("1"):
        issues.append(ValidationIssue(field="probability_drift", code="out_of_range", message="probability_drift is a yearly rate between -1 and 1 (0.07 = 7%)"))
        drift = None
    if prob_vol is not None and prob_vol > Decimal("3"):
        issues.append(ValidationIssue(field="probability_volatility", code="out_of_range", message="probability_volatility is a yearly rate at most 3 (0.35 = 35%)"))
        prob_vol = None
    if prob_vol is not None and norm["probability_drift"] is None:
        warnings.append(("probability_volatility is set but probability_drift is empty; the probability reference is only computed when a drift is given",
                         "填了波动率但没有填漂移率；只有填写漂移率才会计算概率参考"))

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
        warnings.append(("base_metric_period is empty; record which fiscal year the base metric covers", "基期期间为空；请注明基期指标对应的财年"))
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
        warnings.append(("reference_price_source is empty; the reference price is a manual value with no recorded source", "参考价来源为空；参考价是没有记录来源的手动值"))
    if currency is not None and currency != "USD" and CURRENCY_RE.match(currency):
        warnings.append(("SEC XBRL amounts are compared in USD only; with another currency the evidence checks report missing data",
                         "SEC XBRL 金额只按美元比较；其他币种下证据检查会显示缺失"))
    warnings.extend(_claim_year_warnings(norm["claim_text"], numbers.get("horizon_years"), today))

    fp = fingerprint(draft)
    blocking_missing = any(m.blocking for m in missing)
    warn_en, warn_zh = [w[0] for w in warnings], [w[1] for w in warnings]
    if issues or blocking_missing:
        return ValidationResult(ok=False, issues=issues, missing_fields=missing, warnings=warn_en, warnings_zh=warn_zh, fingerprint=fp)

    claim = ValidatedClaim(
        claim_text=norm["claim_text"],
        ticker=ticker,
        currency=currency,
        target_price=numbers["target_price"],
        reference_price=numbers["reference_price"],
        reference_price_date=ref_date,
        reference_price_source=norm["reference_price_source"],
        horizon_years=numbers["horizon_years"],
        target_assumed_shares=target_shares,
        current_shares=numbers["current_shares"],
        valuation_method=method,
        valuation_multiple=numbers["valuation_multiple"],
        base_annual_metric=numbers["base_annual_metric"],
        base_metric_currency=base_currency,
        base_metric_period=norm["base_metric_period"],
        filings_since=since,
        probability_drift=drift,
        share_change_rate=share_rate,
        share_change_mode=share_mode,
        target_shares_derived=shares_derived,
        price_condition=condition,
        probability_volatility=prob_vol,
        field_sources={k: v.strip() for k, v in sources.items() if isinstance(v, str) and v.strip() and norm.get(k) is not None},
    )
    return ValidationResult(ok=True, claim=claim, issues=[], missing_fields=missing, warnings=warn_en, warnings_zh=warn_zh, fingerprint=fp)


def _target_shares(numbers, rate, mode, norm, missing, warnings) -> tuple[Optional[Decimal], bool]:
    """Target-date share count: entered directly, or current shares grown at a yearly rate over the horizon.

    Returns (target shares, derived?). Invalid numbers were already reported as issues by the caller.
    """
    direct, current, years = numbers.get("target_assumed_shares"), numbers.get("current_shares"), numbers.get("horizon_years")
    use_rate = rate is not None and mode != "absolute"
    ctx = Context(prec=34, rounding=ROUND_HALF_EVEN)
    if use_rate:
        if current is None or years is None:
            if norm["current_shares"] is None:
                missing.append(MissingField(field="current_shares", blocking=True, required_for="target share count",
                                            message="missing current share count, needed to apply the yearly share change"))
            return None, False
        with localcontext(ctx):
            target = (current * ((1 + rate).ln() * years).exp()).quantize(Decimal(1))
        derived = True
    elif direct is not None:
        target, derived = direct, False
        if current is not None and years is not None:
            with localcontext(ctx):
                rate = ((direct / current).ln() / years).exp() - 1
    else:
        if norm["target_assumed_shares"] is None:
            missing.append(MissingField(field="target_assumed_shares", blocking=True, required_for="all calculations",
                                        message="missing share count at the target date (target shares, or current shares plus a yearly change)"))
        return None, False
    if rate is not None and not SHARE_RATE_WARN_LOW <= rate <= SHARE_RATE_WARN_HIGH:
        warnings.append((
            f"the share count changes {rate * 100:.1f}% a year to reach {target:,.0f} shares; check that the share inputs belong to this company",
            f"股份数每年变化 {rate * 100:.1f}%，才能到 {target:,.0f} 股；请确认股份数输入属于这家公司",
        ))
    return target, derived


def _claim_year_warnings(text: Optional[str], horizon: Optional[Decimal], today: date) -> list[tuple[str, str]]:
    """Flag a year in the claim text that is already past or does not match the horizon."""
    if not text:
        return []
    years = [int(y) for y in YEAR_RE.findall(text)]
    if not years:
        return []
    year = max(years)
    if year <= today.year:
        return [(f"the claim text mentions {year}, which is not in the future; TickerCase checks claims about a future date",
                 f"观点原文提到 {year} 年，这个时间已经不在未来；TickerCase 用来核验关于未来某个时间的观点")]
    ahead = Decimal(year - today.year)
    if horizon is not None and abs(horizon - ahead) > 1:
        return [(f"the claim text mentions {year} (about {ahead} years ahead) but the horizon is {horizon} years",
                 f"观点原文提到 {year} 年（约 {ahead} 年后），但时间范围填的是 {horizon} 年")]
    return []


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
