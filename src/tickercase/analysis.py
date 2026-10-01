"""Deterministic evidence checks, verdict (OA.13) and recheck conditions (OA.14).

Each check compares one requirement of the confirmed claim with dated public
data and is classified as supporting, contrary, missing or neutral (context).
The verdict is assigned by fixed rules over those checks. No AI is involved;
the same inputs and data always give the same result.

Rules (rules-v1, provisional; the team has not yet validated thresholds):

E1 growth (core)   required CAGR of the valuation metric from the latest reported
                   fiscal year vs the reported CAGR over up to 3 years.
                   supporting: required <= reported; neutral: gap <= 5 pp;
                   contrary: gap > 5 pp, or the latest reported value is <= 0.
E2 multiple        assumed multiple vs today's multiple (last close x reported
                   shares / latest reported annual metric).
                   supporting: ratio <= 1; neutral: <= 1.25; contrary: > 1.25.
E3 share count     implied annual share change (target shares vs reported shares)
                   vs reported change over up to 3 years.
                   supporting: implied >= reported - 1 pp; neutral: >= reported - 3 pp;
                   contrary: assumes a faster reduction than that.
E4 profitability   context only.
E5 freshness       latest 10-K/10-Q older than 135 days -> missing.
E6 price history   context only.

Verdict
- Insufficiently Specified: E1 could not be checked.
- Not Supported Today: E1 contrary and (another check contrary, or the growth gap
  exceeds 15 pp, or the reported base is <= 0).
- Supported Today: E1 supporting and no check contrary.
- Partially Supported: every other case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Optional

from .calculations import _ctx, annualized_rate
from .models import (
    VERDICT_DISPLAY,
    CalculationItem,
    DataMode,
    EvidenceItem,
    FilingRecord,
    MarketSnapshot,
    MetricPoint,
    RecheckCondition,
    ReportedFacts,
    SourceRef,
    ValidatedClaim,
    ValuationMethod,
    Verdict,
)
from .providers.sec import archive_urls

RULES_VERSION = "rules-v1 (provisional)"
HISTORY_YEARS = 3
MIN_HISTORY_DAYS = 540
GROWTH_NEUTRAL_GAP = Decimal("0.05")
GROWTH_SEVERE_GAP = Decimal("0.15")
MULTIPLE_NEUTRAL_RATIO = Decimal("1.25")
SHARES_SUPPORT_DIFF = Decimal("-0.01")
SHARES_NEUTRAL_DIFF = Decimal("-0.03")
FRESHNESS_DAYS = 135
NEXT_REPORT_DAYS = 91
INPUT_MISMATCH = Decimal("0.05")
PRICE_MISMATCH = Decimal("0.10")
PERIODIC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}


@dataclass
class AnalysisOutcome:
    items: list[EvidenceItem]
    verdict: Verdict
    rechecks: list[RecheckCondition]
    warnings: list[str] = field(default_factory=list)


def _s(value: Decimal) -> str:
    return format(value, "f")


def _pct(value: Decimal) -> str:
    return f"{value * 100:.2f}%"


def _amount(value: Decimal) -> str:
    v = abs(value)
    sign = "-" if value < 0 else ""
    for size, suffix in ((Decimal("1e12"), "T"), (Decimal("1e9"), "B"), (Decimal("1e6"), "M")):
        if v >= size:
            return f"{sign}{v / size:,.2f}{suffix}"
    return f"{sign}{v:,.0f}"


def _years(start: date, end: date) -> Decimal:
    with localcontext(_ctx()):
        return Decimal((end - start).days) / Decimal("365.25")


def _rel_diff(a: Decimal, b: Decimal) -> Decimal:
    with localcontext(_ctx()):
        return abs(a / b - 1)


def _fact_source(facts: ReportedFacts, point: MetricPoint, what: str) -> SourceRef:
    _, index_url = archive_urls(facts.cik, point.accession, None)
    period = f"FY ending {point.period_end}" if point.period_start else f"as of {point.period_end}"
    return SourceRef(
        label=f"{what}: {point.concept}, {period}, {point.form} filed {point.filed}",
        url=index_url, filed=point.filed, accession=point.accession, data_mode=facts.data_mode,
    )


def _history_start(series: list[MetricPoint]) -> Optional[MetricPoint]:
    """Earliest point at least ~1.5 years and at most ~3 years before the latest one."""
    latest = series[-1]
    window = [
        p for p in series[:-1]
        if MIN_HISTORY_DAYS <= (latest.period_end - p.period_end).days <= HISTORY_YEARS * 366 + 30
    ]
    return window[0] if window else None


def _calc(calculations: list[CalculationItem], name: str) -> Optional[Decimal]:
    for c in calculations:
        if c.name == name and c.status == "ok":
            return c.value
    return None


class _Checks:
    def __init__(self, claim, calculations, facts, market, filings, today):
        self.claim: ValidatedClaim = claim
        self.calculations: list[CalculationItem] = calculations
        self.facts: Optional[ReportedFacts] = facts
        self.market: Optional[MarketSnapshot] = market
        self.filings: list[FilingRecord] = filings
        self.today: date = today
        self.method: ValuationMethod = claim.valuation_method
        self.metric_label = "revenue" if self.method is ValuationMethod.PRICE_TO_SALES else "net income"
        self.multiple_label = "P/S" if self.method is ValuationMethod.PRICE_TO_SALES else "P/E"
        self.series: list[MetricPoint] = []
        if facts is not None:
            self.series = facts.revenue if self.method is ValuationMethod.PRICE_TO_SALES else facts.net_income
        self.required = _calc(calculations, f"required_{self.method.metric_name}")
        self.warnings: list[str] = []
        # values reused by the verdict and recheck conditions
        self.req_cagr: Optional[Decimal] = None
        self.growth_gap: Optional[Decimal] = None
        self.nonpositive_base = False
        self.current_multiple: Optional[Decimal] = None
        self.implied_share_rate: Optional[Decimal] = None
        self.latest_shares: Optional[MetricPoint] = None
        self.latest_periodic_filed: Optional[date] = None

    def usd_ok(self) -> bool:
        return self.claim.currency == "USD"

    # ------------------------------------------------------------- E1 growth

    def growth(self) -> EvidenceItem:
        base = dict(id="E1", check="metric_growth", title=f"Required {self.metric_label} growth vs reported history",
                    rule=f"supporting if required CAGR <= reported CAGR; neutral if gap <= {_pct(GROWTH_NEUTRAL_GAP)}; contrary otherwise")
        if not self.usd_ok():
            return EvidenceItem(stance="missing", detail=f"claim currency {self.claim.currency}; reported SEC amounts are in USD and no conversion is applied", **base)
        if self.facts is None:
            return EvidenceItem(stance="missing", detail="SEC XBRL company facts were not available in this run", **base)
        if not self.series:
            return EvidenceItem(stance="missing", detail=f"no annual {self.metric_label} found in the company's XBRL facts", **base)
        if self.required is None:
            return EvidenceItem(stance="missing", detail="required metric was not calculated", **base)
        h = self.claim.horizon_years
        latest = self.series[-1]
        sources = [_fact_source(self.facts, latest, f"latest annual {self.metric_label}")]
        measured = {
            f"required_{self.method.metric_name}": _s(self.required),
            f"latest_reported_{self.method.metric_name}": _s(latest.value),
            "latest_period_end": latest.period_end.isoformat(),
            "horizon_years": _s(h),
        }
        if latest.value <= 0:
            self.nonpositive_base = True
            return EvidenceItem(
                stance="contrary", as_of=latest.filed, sources=sources, measured=measured,
                detail=(f"latest reported annual {self.metric_label} is {_amount(latest.value)} (FY ending {latest.period_end}); "
                        f"the claim requires {_amount(self.required)} per year, which needs a turnaround that a growth rate cannot express"),
                **base,
            )
        self.req_cagr = annualized_rate(self.required, latest.value, h)
        measured["required_cagr_from_reported"] = _s(self.req_cagr)
        start = _history_start(self.series)
        if start is None or start.value <= 0:
            why = "fewer than two annual periods about 1.5–3 years apart" if start is None else "the earlier reported value is not positive"
            return EvidenceItem(
                stance="missing", as_of=latest.filed, sources=sources, measured=measured,
                detail=f"required {self.metric_label} CAGR is {_pct(self.req_cagr)}; reported growth could not be computed ({why})",
                **base,
            )
        years = _years(start.period_end, latest.period_end)
        hist = annualized_rate(latest.value, start.value, years)
        with localcontext(_ctx()):
            gap = self.req_cagr - hist
        self.growth_gap = gap
        sources.append(_fact_source(self.facts, start, f"earlier annual {self.metric_label}"))
        measured.update(reported_cagr=_s(hist), reported_window=f"{start.period_end} to {latest.period_end}", gap=_s(gap))
        stance = "supporting" if gap <= 0 else "neutral" if gap <= GROWTH_NEUTRAL_GAP else "contrary"
        detail = (f"the claim needs {self.metric_label} to grow {_pct(self.req_cagr)} per year from {_amount(latest.value)} to "
                  f"{_amount(self.required)}; reported growth was {_pct(hist)} per year ({start.period_end.year}–{latest.period_end.year})")
        return EvidenceItem(stance=stance, detail=detail, measured=measured, sources=sources, as_of=latest.filed, **base)

    # ------------------------------------------------------------- E2 multiple

    def multiple(self) -> EvidenceItem:
        base = dict(id="E2", check="valuation_multiple", title=f"Assumed {self.multiple_label} vs today's {self.multiple_label}",
                    rule=f"supporting if assumed <= current; neutral if assumed <= {MULTIPLE_NEUTRAL_RATIO}x current; contrary otherwise")
        if self.market is None:
            return EvidenceItem(stance="missing", detail="no market price was available in this run", **base)
        if not self.usd_ok() or (self.market.currency and self.market.currency != self.claim.currency):
            return EvidenceItem(stance="missing", detail=f"price currency {self.market.currency} / claim currency {self.claim.currency} do not match USD SEC amounts", **base)
        if not self.series:
            return EvidenceItem(stance="missing", detail=f"no reported annual {self.metric_label} to compute today's {self.multiple_label}", **base)
        shares_point = self.facts.shares_outstanding[-1] if self.facts and self.facts.shares_outstanding else None
        shares = shares_point.value if shares_point else self.claim.current_shares
        if shares is None:
            return EvidenceItem(stance="missing", detail="no share count (reported or entered) to compute today's market value", **base)
        latest = self.series[-1]
        sources = [
            SourceRef(label=f"close {self.market.last_close} on {self.market.last_date} ({self.market.provider_id})",
                      url=self.market.source_url, data_mode=self.market.data_mode),
            _fact_source(self.facts, latest, f"latest annual {self.metric_label}"),
        ]
        if shares_point:
            sources.append(_fact_source(self.facts, shares_point, "shares outstanding"))
        with localcontext(_ctx()):
            cap = self.market.last_close * shares
        measured = {"last_close": _s(self.market.last_close), "shares": _s(shares), "current_market_cap": _s(cap),
                    f"latest_reported_{self.method.metric_name}": _s(latest.value), "assumed_multiple": _s(self.claim.valuation_multiple)}
        if latest.value <= 0:
            return EvidenceItem(stance="neutral", detail=f"today's {self.multiple_label} is not meaningful because reported {self.metric_label} is not positive",
                                measured=measured, sources=sources, as_of=self.market.last_date, **base)
        with localcontext(_ctx()):
            current = cap / latest.value
            ratio = self.claim.valuation_multiple / current
        self.current_multiple = current
        measured.update(current_multiple=_s(current), assumed_over_current=_s(ratio))
        stance = "supporting" if ratio <= 1 else "neutral" if ratio <= MULTIPLE_NEUTRAL_RATIO else "contrary"
        verb = "at or below" if ratio <= 1 else f"{ratio:.2f}x"
        detail = (f"the claim assumes {self.multiple_label} {self.claim.valuation_multiple} at the target date; today's {self.multiple_label} is "
                  f"{current:.2f} (market value {_amount(cap)} / {self.metric_label} {_amount(latest.value)}), so the assumption is {verb} today's level")
        return EvidenceItem(stance=stance, detail=detail, measured=measured, sources=sources, as_of=self.market.last_date, **base)

    # ------------------------------------------------------------- E3 shares

    def shares(self) -> EvidenceItem:
        base = dict(id="E3", check="share_count", title="Assumed target share count vs reported share trend",
                    rule=(f"supporting if implied annual change >= reported change {_pct(SHARES_SUPPORT_DIFF)}; "
                          f"neutral if >= reported change {_pct(SHARES_NEUTRAL_DIFF)}; contrary otherwise"))
        if self.facts is None or not self.facts.shares_outstanding:
            return EvidenceItem(stance="missing", detail="no reported shares outstanding in this run", **base)
        series = self.facts.shares_outstanding
        latest = series[-1]
        self.latest_shares = latest
        self.implied_share_rate = annualized_rate(self.claim.target_assumed_shares, latest.value, self.claim.horizon_years)
        sources = [_fact_source(self.facts, latest, "shares outstanding")]
        measured = {"reported_shares": _s(latest.value), "reported_as_of": latest.period_end.isoformat(),
                    "target_assumed_shares": _s(self.claim.target_assumed_shares), "implied_annual_change": _s(self.implied_share_rate)}
        start = _history_start(series)
        if start is None:
            return EvidenceItem(stance="missing", detail=f"implied share change is {_pct(self.implied_share_rate)} per year; no earlier share count about 1.5–3 years back to compare",
                                measured=measured, sources=sources, as_of=latest.filed, **base)
        hist = annualized_rate(latest.value, start.value, _years(start.period_end, latest.period_end))
        with localcontext(_ctx()):
            diff = self.implied_share_rate - hist
        sources.append(_fact_source(self.facts, start, "earlier shares outstanding"))
        measured.update(reported_annual_change=_s(hist), reported_window=f"{start.period_end} to {latest.period_end}")
        stance = "supporting" if diff >= SHARES_SUPPORT_DIFF else "neutral" if diff >= SHARES_NEUTRAL_DIFF else "contrary"
        detail = (f"going from {_amount(latest.value)} reported shares to the assumed {_amount(self.claim.target_assumed_shares)} means "
                  f"{_pct(self.implied_share_rate)} per year; the reported count changed {_pct(hist)} per year ({start.period_end} to {latest.period_end})")
        return EvidenceItem(stance=stance, detail=detail, measured=measured, sources=sources, as_of=latest.filed, **base)

    # ------------------------------------------------------------- E4 profitability

    def profitability(self) -> Optional[EvidenceItem]:
        if self.method is not ValuationMethod.PRICE_TO_SALES or self.facts is None or not self.facts.net_income:
            return None
        latest = self.facts.net_income[-1]
        state = "a net loss" if latest.value < 0 else "a profit"
        return EvidenceItem(
            id="E4", check="profitability", stance="neutral", title="Reported profitability (context)",
            detail=(f"latest annual net income is {_amount(latest.value)} (FY ending {latest.period_end}), i.e. {state}. "
                    "A P/S target does not require profit, but losses can lead to financing and dilution."),
            measured={"latest_net_income": _s(latest.value)}, sources=[_fact_source(self.facts, latest, "latest annual net income")],
            as_of=latest.filed,
        )

    # ------------------------------------------------------------- E5 freshness

    def freshness(self) -> EvidenceItem:
        base = dict(id="E5", check="evidence_freshness", title="Freshness of reported data",
                    rule=f"missing if the latest 10-K/10-Q is older than {FRESHNESS_DAYS} days")
        periodic = [r for r in self.filings if r.form_type in PERIODIC_FORMS]
        source: Optional[SourceRef] = None
        if periodic:
            latest = max(periodic, key=lambda r: r.filing_date)
            filed = latest.filing_date
            source = SourceRef(label=f"{latest.form_type} filed {filed}", url=latest.filing_index_url, filed=filed,
                               accession=latest.accession_number, data_mode=latest.data_mode)
        elif self.facts is not None and (self.series or self.facts.shares_outstanding):
            filed = max(p.filed for p in [*self.series, *self.facts.shares_outstanding])
        else:
            return EvidenceItem(stance="missing", detail="no periodic filing date available in this run", **base)
        self.latest_periodic_filed = filed
        age = (self.today - filed).days
        measured = {"latest_periodic_filing": filed.isoformat(), "age_days": str(age)}
        if age > FRESHNESS_DAYS:
            return EvidenceItem(stance="missing", detail=f"latest periodic report was filed {age} days ago ({filed}); newer results may exist that this run did not see",
                                measured=measured, sources=[source] if source else [], as_of=filed, **base)
        return EvidenceItem(stance="neutral", detail=f"latest periodic report filed {filed} ({age} days before this run)",
                            measured=measured, sources=[source] if source else [], as_of=filed, **base)

    # ------------------------------------------------------------- E6 price history

    def price_history(self) -> Optional[EvidenceItem]:
        if self.market is None or len(self.market.price_series) < 2:
            return None
        first, last = self.market.price_series[0], self.market.price_series[-1]
        years = _years(first.day, last.day)
        if years <= 0:
            return None
        past = annualized_rate(last.close, first.close, years)
        needed = _calc(self.calculations, "annualized_price_return")
        detail = f"the closing price moved {_pct(past)} per year from {first.day} to {last.day} (not dividend-adjusted)"
        if needed is not None:
            detail += f"; the claim requires {_pct(needed)} per year from the reference price"
        return EvidenceItem(
            id="E6", check="price_history", stance="neutral", title="Past price trend (context)", detail=detail,
            measured={"past_annualized_price_change": _s(past), "window": f"{first.day} to {last.day}"},
            sources=[SourceRef(label=f"daily closes ({self.market.provider_id})", url=self.market.source_url, data_mode=self.market.data_mode)],
            as_of=last.day,
        )

    # ------------------------------------------------------------- input cross-checks

    def input_checks(self) -> None:
        c = self.claim
        if self.market is not None and (not self.market.currency or self.market.currency == c.currency):
            if _rel_diff(c.reference_price, self.market.last_close) > PRICE_MISMATCH:
                self.warnings.append(
                    f"your reference price {c.reference_price} differs by more than {PRICE_MISMATCH * 100:.0f}% from the "
                    f"close {self.market.last_close} on {self.market.last_date} ({self.market.data_mode.value}); check the value and date")
        if self.series and c.base_annual_metric is not None and self.usd_ok() and self.series[-1].value != 0:
            latest = self.series[-1]
            if _rel_diff(c.base_annual_metric, latest.value) > INPUT_MISMATCH:
                self.warnings.append(
                    f"your base {self.metric_label} {_amount(c.base_annual_metric)} differs from the reported "
                    f"{_amount(latest.value)} (FY ending {latest.period_end}); evidence checks use the reported value")
        if self.facts and self.facts.shares_outstanding and c.current_shares is not None:
            latest = self.facts.shares_outstanding[-1]
            if _rel_diff(c.current_shares, latest.value) > INPUT_MISMATCH:
                self.warnings.append(
                    f"your current share count {_amount(c.current_shares)} differs from the reported {_amount(latest.value)} "
                    f"as of {latest.period_end}")


def _verdict(checks: _Checks, items: list[EvidenceItem], today: date, synthetic: bool) -> Verdict:
    growth = next(i for i in items if i.id == "E1")
    contrary = [i for i in items if i.stance == "contrary"]
    supporting = [i for i in items if i.stance == "supporting"]
    if growth.stance == "missing":
        label = "insufficiently_specified"
    elif growth.stance == "contrary" and (len(contrary) >= 2 or checks.nonpositive_base
                                          or (checks.growth_gap is not None and checks.growth_gap > GROWTH_SEVERE_GAP)):
        label = "not_supported_today"
    elif growth.stance == "supporting" and not contrary:
        label = "supported_today"
    else:
        label = "partially_supported"

    rationale = [f"Core check E1 is {growth.stance}: {growth.detail}."]
    if label == "insufficiently_specified":
        rationale.append("Without E1 the central requirement of the claim cannot be compared with reported data.")
    for item in contrary:
        if item.id != "E1":
            rationale.append(f"{item.id} is contrary: {item.detail}.")
    for item in supporting:
        if item.id != "E1":
            rationale.append(f"{item.id} is supporting: {item.detail}.")
    if checks.growth_gap is not None and checks.growth_gap > GROWTH_SEVERE_GAP:
        rationale.append(f"The growth gap of {_pct(checks.growth_gap)} per year exceeds the {_pct(GROWTH_SEVERE_GAP)} limit of rules-v1.")
    missing = [i.id for i in items if i.stance == "missing" and i.id != "E1"]
    if missing:
        rationale.append(f"Not checked for lack of data: {', '.join(missing)}.")

    display = VERDICT_DISPLAY[label]
    limitations = [
        f"Evidence as of {today}. '{display}' describes the state of the evidence on that date. "
        "Not Supported Today does not mean the target price is impossible; Supported Today is not a guarantee.",
        "Rules and thresholds are provisional (rules-v1); team validation of verdict thresholds is still open.",
        "Checks use reported financial history and market prices only. Filing text, guidance and news are not read; "
        "AI-assisted analysis (OA.10–OA.12) is not implemented.",
        "Required growth is measured from the latest reported fiscal year over the confirmed horizon; "
        "the time between that fiscal year end and the reference date is not adjusted.",
    ]
    if synthetic:
        limitations.insert(0, "Synthetic example data was used. This verdict demonstrates the rules and says nothing about a real company.")
    return Verdict(label=label, display=display, as_of=today, rationale=rationale, limitations=limitations,
                   basis=[i.id for i in items], rules_version=RULES_VERSION)


def _rechecks(checks: _Checks, items: list[EvidenceItem]) -> list[RecheckCondition]:
    c = checks.claim
    out: list[RecheckCondition] = []
    if checks.req_cagr is not None and checks.series:
        latest = checks.series[-1]
        with localcontext(_ctx()):
            path = latest.value * (1 + checks.req_cagr)
        out.append(RecheckCondition(
            id="R1", trigger=f"Next annual {checks.metric_label} is reported below {_amount(path)}",
            watch=f"10-K / XBRL company facts for the fiscal year after {latest.period_end}",
            threshold=f"{_s(path.quantize(Decimal(1)))} ({_pct(checks.req_cagr)} above {_amount(latest.value)})",
            linked_to=["E1", "valuation_multiple", "horizon_years"],
        ))
    if checks.latest_shares is not None and checks.implied_share_rate is not None:
        s = checks.latest_shares
        with localcontext(_ctx()):
            path = s.value * (1 + checks.implied_share_rate)
        out.append(RecheckCondition(
            id="R2", trigger=f"Shares outstanding rise above {_amount(path)} within a year of {s.period_end}, or above {_amount(c.target_assumed_shares)} at any time",
            watch="cover page of the next 10-Q / 10-K (dei:EntityCommonStockSharesOutstanding); S-1/S-3/424B offerings",
            threshold=_s(path.quantize(Decimal(1))), linked_to=["E3", "target_assumed_shares"],
        ))
    if checks.current_multiple is not None:
        with localcontext(_ctx()):
            low = c.valuation_multiple / MULTIPLE_NEUTRAL_RATIO
        out.append(RecheckCondition(
            id="R3", trigger=f"Today's {checks.multiple_label} falls below {low:.2f} (the assumed {c.valuation_multiple} would then need more than {MULTIPLE_NEUTRAL_RATIO}x expansion)",
            watch=f"price x shares / latest annual {checks.metric_label}; now {checks.current_multiple:.2f}",
            threshold=_s(low.quantize(Decimal("0.01"))), linked_to=["E2", "valuation_multiple"],
        ))
    if checks.latest_periodic_filed is not None:
        expected = checks.latest_periodic_filed + timedelta(days=NEXT_REPORT_DAYS)
        out.append(RecheckCondition(
            id="R4", trigger=f"Next 10-Q or 10-K is filed (expected around {expected})",
            watch="SEC EDGAR filings for the ticker", linked_to=["E5"],
        ))
    for item in items:
        if item.stance == "missing":
            out.append(RecheckCondition(
                id=f"R-{item.id}", trigger=f"Data for '{item.title}' becomes available", watch=item.detail, linked_to=[item.id],
            ))
    out.append(RecheckCondition(
        id="R9", trigger="Any confirmed assumption changes (target price, horizon, multiple, target share count)",
        watch="re-confirm the inputs and run the case again",
        linked_to=["target_price", "horizon_years", "valuation_multiple", "target_assumed_shares"],
    ))
    return out


def analyze(
    claim: ValidatedClaim,
    calculations: list[CalculationItem],
    *,
    facts: Optional[ReportedFacts],
    market: Optional[MarketSnapshot],
    filings: list[FilingRecord],
    today: date,
) -> AnalysisOutcome:
    checks = _Checks(claim, calculations, facts, market, filings, today)
    items = [checks.growth(), checks.multiple(), checks.shares()]
    for extra in (checks.profitability(), checks.freshness(), checks.price_history()):
        if extra is not None:
            items.append(extra)
    checks.input_checks()
    modes = {f.data_mode for f in filings} | {x.data_mode for x in (facts, market) if x is not None}
    synthetic = DataMode.SYNTHETIC in modes
    return AnalysisOutcome(
        items=items,
        verdict=_verdict(checks, items, today, synthetic),
        rechecks=_rechecks(checks, items),
        warnings=checks.warnings,
    )
