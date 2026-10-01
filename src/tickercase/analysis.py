"""Deterministic evidence checks, verdict (OA.13), recheck conditions (OA.14) and sensitivity.

Each check compares one requirement of the confirmed claim with dated public
data and is classified as supporting, contrary, missing or neutral (context).
The verdict is assigned by fixed rules over those checks. No AI is involved;
the same inputs and data always give the same result. Every text is produced
in English and Chinese.

Time base: required growth runs from the end of the latest reported fiscal
year to the target date (reference date + horizon), so the time already passed
since that fiscal year end is counted. The same applies to share counts.

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

from .calculations import _ctx, annualized_rate, market_cap, required_metric
from .models import (
    VERDICT_DISPLAY,
    VERDICT_DISPLAY_ZH,
    CalculationItem,
    DataMode,
    EvidenceItem,
    FilingRecord,
    MarketSnapshot,
    MetricPoint,
    RecheckCondition,
    ReportedFacts,
    Sensitivity,
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
SENSITIVITY_MULTIPLES = (Decimal("0.5"), Decimal("0.75"), Decimal("1"), Decimal("1.25"), Decimal("1.5"), Decimal("2"))

STANCE_ZH = {"supporting": "支持", "contrary": "反对", "missing": "缺失", "neutral": "背景"}


@dataclass
class Bilingual:
    en: str
    zh: str


@dataclass
class AnalysisOutcome:
    items: list[EvidenceItem]
    verdict: Verdict
    rechecks: list[RecheckCondition]
    sensitivity: Optional[Sensitivity] = None
    warnings: list[Bilingual] = field(default_factory=list)


def _s(value: Decimal) -> str:
    return format(value, "f")


def _pct(value: Decimal) -> str:
    return f"{value * 100:.2f}%"


def _pp(value: Decimal) -> str:
    return f"{value * 100:.2f} pp"


def _amount(value: Decimal) -> str:
    v = abs(value)
    sign = "-" if value < 0 else ""
    for size, suffix in ((Decimal("1e12"), "T"), (Decimal("1e9"), "B"), (Decimal("1e6"), "M")):
        if v >= size:
            return f"{sign}{v / size:,.2f}{suffix}"
    return f"{sign}{v:,.0f}"


def _amount_zh(value: Decimal) -> str:
    """Chinese units: 3,649.82 亿, 1.40 万亿, 2,800 万."""
    v = abs(value)
    sign = "-" if value < 0 else ""
    for size, unit, places in ((Decimal("1e12"), "万亿", 2), (Decimal("1e8"), "亿", 2), (Decimal("1e4"), "万", 0)):
        if v >= size:
            return f"{sign}{v / size:,.{places}f} {unit}"
    return f"{sign}{v:,.0f}"


def _years(start: date, end: date) -> Decimal:
    with localcontext(_ctx()):
        return Decimal((end - start).days) / Decimal("365.25")


def _rel_diff(a: Decimal, b: Decimal) -> Decimal:
    with localcontext(_ctx()):
        return abs(a / b - 1)


def years_to_target(claim: ValidatedClaim, since: date) -> Decimal:
    """Years from ``since`` (a fiscal year end or share-count date) to the claim's target date."""
    with localcontext(_ctx()):
        total = claim.horizon_years + _years(since, claim.reference_price_date)
    return total if total > 0 else claim.horizon_years


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


SPLIT_JUMP = Decimal("1.4")


def after_last_split(series: list[MetricPoint]) -> list[MetricPoint]:
    """Share counts after the last jump of more than 40% between neighbouring reports (a split or reverse split)."""
    start = 0
    for i in range(1, len(series)):
        ratio = series[i].value / series[i - 1].value
        if ratio > SPLIT_JUMP or ratio < 1 / SPLIT_JUMP:
            start = i
    return series[start:]


def share_trend(series: list[MetricPoint]) -> Optional[tuple[Decimal, MetricPoint, MetricPoint]]:
    """Yearly change of reported shares over up to 3 years, ignoring history before the last split."""
    clean = after_last_split(series)
    if len(clean) < 2:
        return None
    start = _history_start(clean)
    if start is None:
        return None
    latest = clean[-1]
    return annualized_rate(latest.value, start.value, _years(start.period_end, latest.period_end)), start, latest


def _calc(calculations: list[CalculationItem], name: str) -> Optional[Decimal]:
    for c in calculations:
        if c.name == name and c.status == "ok":
            return c.value
    return None


def _item(id_, check, stance, title: Bilingual, detail: Bilingual, rule: Optional[Bilingual] = None, **kw) -> EvidenceItem:
    return EvidenceItem(
        id=id_, check=check, stance=stance, title=title.en, title_zh=title.zh, detail=detail.en, detail_zh=detail.zh,
        rule=rule.en if rule else None, rule_zh=rule.zh if rule else None, **kw,
    )


class _Checks:
    def __init__(self, claim, calculations, facts, market, filings, today):
        self.claim: ValidatedClaim = claim
        self.calculations: list[CalculationItem] = calculations
        self.facts: Optional[ReportedFacts] = facts
        self.market: Optional[MarketSnapshot] = market
        self.filings: list[FilingRecord] = filings
        self.today: date = today
        self.method: ValuationMethod = claim.valuation_method
        is_ps = self.method is ValuationMethod.PRICE_TO_SALES
        self.metric = Bilingual("revenue", "营收") if is_ps else Bilingual("net income", "净利润")
        self.multiple_label = "P/S" if is_ps else "P/E"
        self.series: list[MetricPoint] = []
        if facts is not None:
            self.series = facts.revenue if is_ps else facts.net_income
        self.required = _calc(calculations, f"required_{self.method.metric_name}")
        self.warnings: list[Bilingual] = []
        # values reused by the verdict, recheck conditions and sensitivity
        self.req_cagr: Optional[Decimal] = None
        self.hist_cagr: Optional[Decimal] = None
        self.growth_gap: Optional[Decimal] = None
        self.growth_years: Optional[Decimal] = None
        self.nonpositive_base = False
        self.current_multiple: Optional[Decimal] = None
        self.implied_share_rate: Optional[Decimal] = None
        self.latest_shares: Optional[MetricPoint] = None
        self.latest_periodic_filed: Optional[date] = None

    def usd_ok(self) -> bool:
        return self.claim.currency == "USD"

    # ------------------------------------------------------------- E1 growth

    def growth(self) -> EvidenceItem:
        m = self.metric
        title = Bilingual(f"Required {m.en} growth vs reported history", f"所需{m.zh}增速 vs 已披露增速")
        rule = Bilingual(
            f"supporting if required CAGR <= reported CAGR; neutral if gap <= {_pp(GROWTH_NEUTRAL_GAP)}; contrary otherwise",
            f"所需年增速不高于已披露增速为支持；差距不超过 {_pp(GROWTH_NEUTRAL_GAP)} 为背景；否则为反对",
        )

        def missing(en: str, zh: str, **kw) -> EvidenceItem:
            return _item("E1", "metric_growth", "missing", title, Bilingual(en, zh), rule, **kw)

        if not self.usd_ok():
            return missing(f"claim currency {self.claim.currency}; reported SEC amounts are in USD and no conversion is applied",
                           f"观点币种为 {self.claim.currency}；SEC 金额为美元，本版本不做汇率换算")
        if self.facts is None:
            return missing("SEC XBRL company facts were not available in this run", "本次未取得 SEC XBRL 财务数据")
        if not self.series:
            return missing(f"no annual {m.en} found in the company's XBRL facts", f"公司 XBRL 数据中没有年度{m.zh}")
        if self.required is None:
            return missing("required metric was not calculated", "所需指标未计算")
        latest = self.series[-1]
        years = years_to_target(self.claim, latest.period_end)
        self.growth_years = years
        sources = [_fact_source(self.facts, latest, f"latest annual {m.en}")]
        measured = {
            f"required_{self.method.metric_name}": _s(self.required),
            f"latest_reported_{self.method.metric_name}": _s(latest.value),
            "latest_period_end": latest.period_end.isoformat(),
            "years_from_period_end_to_target": _s(years.quantize(Decimal("0.0001"))),
        }
        if latest.value <= 0:
            self.nonpositive_base = True
            return _item("E1", "metric_growth", "contrary", title, Bilingual(
                f"latest reported annual {m.en} is {_amount(latest.value)} (FY ending {latest.period_end}); the claim requires "
                f"{_amount(self.required)} per year, which needs a turnaround that a growth rate cannot express",
                f"最近披露的年度{m.zh}为 {_amount_zh(latest.value)}（截至 {latest.period_end} 的财年）；观点需要每年 {_amount_zh(self.required)}，"
                "需要先扭亏，无法用增长率表示",
            ), rule, measured=measured, sources=sources, as_of=latest.filed)
        self.req_cagr = annualized_rate(self.required, latest.value, years)
        measured["required_cagr_from_reported"] = _s(self.req_cagr)
        start = _history_start(self.series)
        if start is None or start.value <= 0:
            why = Bilingual("fewer than two annual periods about 1.5–3 years apart", "缺少相隔约 1.5–3 年的两个年度数据") if start is None \
                else Bilingual("the earlier reported value is not positive", "较早的披露值不为正")
            return missing(f"required {m.en} CAGR is {_pct(self.req_cagr)}; reported growth could not be computed ({why.en})",
                           f"所需{m.zh}年增速为 {_pct(self.req_cagr)}；无法计算已披露增速（{why.zh}）",
                           measured=measured, sources=sources, as_of=latest.filed)
        hist = annualized_rate(latest.value, start.value, _years(start.period_end, latest.period_end))
        with localcontext(_ctx()):
            gap = self.req_cagr - hist
        self.hist_cagr, self.growth_gap = hist, gap
        sources.append(_fact_source(self.facts, start, f"earlier annual {m.en}"))
        measured.update(reported_cagr=_s(hist), reported_window=f"{start.period_end} to {latest.period_end}", gap=_s(gap))
        stance = "supporting" if gap <= 0 else "neutral" if gap <= GROWTH_NEUTRAL_GAP else "contrary"
        target_date = self.claim.reference_price_date + timedelta(days=round(float(self.claim.horizon_years) * 365.25))
        detail = Bilingual(
            f"the claim needs {m.en} to grow {_pct(self.req_cagr)} per year from {_amount(latest.value)} (FY ending {latest.period_end}) "
            f"to {_amount(self.required)} by about {target_date} ({years:.2f} years); reported growth was {_pct(hist)} per year "
            f"({start.period_end.year}–{latest.period_end.year})",
            f"观点要求{m.zh}从 {_amount_zh(latest.value)}（截至 {latest.period_end} 的财年）在约 {years:.2f} 年内（到 {target_date} 前后）"
            f"增长到 {_amount_zh(self.required)}，即每年 {_pct(self.req_cagr)}；已披露增速为每年 {_pct(hist)}"
            f"（{start.period_end.year}–{latest.period_end.year}）",
        )
        return _item("E1", "metric_growth", stance, title, detail, rule, measured=measured, sources=sources, as_of=latest.filed)

    # ------------------------------------------------------------- E2 multiple

    def multiple(self) -> EvidenceItem:
        ml, m = self.multiple_label, self.metric
        title = Bilingual(f"Assumed {ml} vs today's {ml}", f"假设 {ml} vs 当前 {ml}")
        rule = Bilingual(
            f"supporting if assumed <= current; neutral if assumed <= {MULTIPLE_NEUTRAL_RATIO}x current; contrary otherwise",
            f"假设倍数不高于当前为支持；不超过当前的 {MULTIPLE_NEUTRAL_RATIO} 倍为背景；否则为反对",
        )

        def missing(en, zh):
            return _item("E2", "valuation_multiple", "missing", title, Bilingual(en, zh), rule)

        if self.market is None:
            return missing("no market price was available in this run", "本次未取得市场价格")
        if not self.usd_ok() or (self.market.currency and self.market.currency != self.claim.currency):
            return missing(f"price currency {self.market.currency} / claim currency {self.claim.currency} do not match USD SEC amounts",
                           f"价格币种 {self.market.currency} / 观点币种 {self.claim.currency} 与 SEC 美元金额不一致")
        if not self.series:
            return missing(f"no reported annual {m.en} to compute today's {ml}", f"没有已披露的年度{m.zh}，无法计算当前 {ml}")
        shares_point = self.facts.shares_outstanding[-1] if self.facts and self.facts.shares_outstanding else None
        shares = shares_point.value if shares_point else self.claim.current_shares
        if shares is None:
            return missing("no share count (reported or entered) to compute today's market value", "没有股份数（披露或填写），无法计算当前市值")
        latest = self.series[-1]
        sources = [
            SourceRef(label=f"close {_s(self.market.last_close)} on {self.market.last_date} ({self.market.provider_id})",
                      url=self.market.source_url, data_mode=self.market.data_mode),
            _fact_source(self.facts, latest, f"latest annual {m.en}"),
        ]
        if shares_point:
            sources.append(_fact_source(self.facts, shares_point, "shares outstanding"))
        cap = market_cap(self.market.last_close, shares)
        measured = {"last_close": _s(self.market.last_close), "shares": _s(shares), "current_market_cap": _s(cap),
                    f"latest_reported_{self.method.metric_name}": _s(latest.value), "assumed_multiple": _s(self.claim.valuation_multiple)}
        if latest.value <= 0:
            return _item("E2", "valuation_multiple", "neutral", title, Bilingual(
                f"today's {ml} is not meaningful because reported {m.en} is not positive",
                f"已披露{m.zh}不为正，当前 {ml} 没有意义"), rule, measured=measured, sources=sources, as_of=self.market.last_date)
        with localcontext(_ctx()):
            current = cap / latest.value
            ratio = self.claim.valuation_multiple / current
        self.current_multiple = current
        measured.update(current_multiple=_s(current), assumed_over_current=_s(ratio))
        stance = "supporting" if ratio <= 1 else "neutral" if ratio <= MULTIPLE_NEUTRAL_RATIO else "contrary"
        rel_en = "at or below" if ratio <= 1 else f"{ratio:.2f}x"
        rel_zh = "不高于当前水平" if ratio <= 1 else f"是当前的 {ratio:.2f} 倍"
        detail = Bilingual(
            f"the claim assumes {ml} {self.claim.valuation_multiple} at the target date; today's {ml} is {current:.2f} "
            f"(market value {_amount(cap)} / {m.en} {_amount(latest.value)}), so the assumption is {rel_en} today's level",
            f"观点假设目标日 {ml} 为 {self.claim.valuation_multiple}；当前 {ml} 为 {current:.2f}"
            f"（市值 {_amount_zh(cap)} / {m.zh} {_amount_zh(latest.value)}），假设{rel_zh}",
        )
        return _item("E2", "valuation_multiple", stance, title, detail, rule, measured=measured, sources=sources, as_of=self.market.last_date)

    # ------------------------------------------------------------- E3 shares

    def shares(self) -> EvidenceItem:
        title = Bilingual("Assumed target share count vs reported share trend", "假设目标股份数 vs 已披露股份变化")
        rule = Bilingual(
            f"supporting if implied annual change >= reported change {_pp(SHARES_SUPPORT_DIFF)}; "
            f"neutral if >= reported change {_pp(SHARES_NEUTRAL_DIFF)}; contrary otherwise",
            f"隐含年变化不低于已披露变化 {_pp(SHARES_SUPPORT_DIFF)} 为支持；不低于 {_pp(SHARES_NEUTRAL_DIFF)} 为背景；否则为反对",
        )
        if self.facts is None or not self.facts.shares_outstanding:
            return _item("E3", "share_count", "missing", title, Bilingual("no reported shares outstanding in this run", "本次没有已披露的股份数"), rule)
        series = self.facts.shares_outstanding
        latest = series[-1]
        self.latest_shares = latest
        years = years_to_target(self.claim, latest.period_end)
        self.implied_share_rate = annualized_rate(self.claim.target_assumed_shares, latest.value, years)
        sources = [_fact_source(self.facts, latest, "shares outstanding")]
        measured = {"reported_shares": _s(latest.value), "reported_as_of": latest.period_end.isoformat(),
                    "target_assumed_shares": _s(self.claim.target_assumed_shares), "implied_annual_change": _s(self.implied_share_rate),
                    "years_to_target": _s(years.quantize(Decimal("0.0001")))}
        trend = share_trend(series)
        if trend is None:
            return _item("E3", "share_count", "missing", title, Bilingual(
                f"implied share change is {_pct(self.implied_share_rate)} per year; no earlier share count about 1.5–3 years back "
                "(after any stock split) to compare",
                f"隐含股份年变化为 {_pct(self.implied_share_rate)}；缺少约 1.5–3 年前（拆股之后）的股份数用于比较"),
                rule, measured=measured, sources=sources, as_of=latest.filed)
        hist, start, _ = trend
        with localcontext(_ctx()):
            diff = self.implied_share_rate - hist
        sources.append(_fact_source(self.facts, start, "earlier shares outstanding"))
        measured.update(reported_annual_change=_s(hist), reported_window=f"{start.period_end} to {latest.period_end}")
        stance = "supporting" if diff >= SHARES_SUPPORT_DIFF else "neutral" if diff >= SHARES_NEUTRAL_DIFF else "contrary"
        detail = Bilingual(
            f"going from {_amount(latest.value)} reported shares to the assumed {_amount(self.claim.target_assumed_shares)} means "
            f"{_pct(self.implied_share_rate)} per year; the reported count changed {_pct(hist)} per year ({start.period_end} to {latest.period_end})",
            f"从已披露的 {_amount_zh(latest.value)} 股到假设的 {_amount_zh(self.claim.target_assumed_shares)} 股，即每年 {_pct(self.implied_share_rate)}；"
            f"已披露股份数每年变化 {_pct(hist)}（{start.period_end} 至 {latest.period_end}）",
        )
        return _item("E3", "share_count", stance, title, detail, rule, measured=measured, sources=sources, as_of=latest.filed)

    # ------------------------------------------------------------- E4 profitability

    def profitability(self) -> Optional[EvidenceItem]:
        if self.method is not ValuationMethod.PRICE_TO_SALES or self.facts is None or not self.facts.net_income:
            return None
        latest = self.facts.net_income[-1]
        loss = latest.value < 0
        return _item("E4", "profitability", "neutral", Bilingual("Reported profitability (context)", "已披露盈利情况（背景）"), Bilingual(
            f"latest annual net income is {_amount(latest.value)} (FY ending {latest.period_end}), i.e. {'a net loss' if loss else 'a profit'}. "
            "A P/S target does not require profit, but losses can lead to financing and dilution.",
            f"最近年度净利润为 {_amount_zh(latest.value)}（截至 {latest.period_end} 的财年），{'处于亏损' if loss else '处于盈利'}。"
            "P/S 目标不要求盈利，但持续亏损可能带来融资和股份稀释。"),
            measured={"latest_net_income": _s(latest.value)}, sources=[_fact_source(self.facts, latest, "latest annual net income")],
            as_of=latest.filed)

    # ------------------------------------------------------------- E5 freshness

    def freshness(self) -> EvidenceItem:
        title = Bilingual("Freshness of reported data", "已披露数据的新旧")
        rule = Bilingual(f"missing if the latest 10-K/10-Q is older than {FRESHNESS_DAYS} days",
                         f"最近一份 10-K/10-Q 超过 {FRESHNESS_DAYS} 天视为缺失")
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
            return _item("E5", "evidence_freshness", "missing", title,
                         Bilingual("no periodic filing date available in this run", "本次没有定期报告的申报日期"), rule)
        self.latest_periodic_filed = filed
        age = (self.today - filed).days
        measured = {"latest_periodic_filing": filed.isoformat(), "age_days": str(age)}
        sources = [source] if source else []
        if age > FRESHNESS_DAYS:
            return _item("E5", "evidence_freshness", "missing", title, Bilingual(
                f"latest periodic report was filed {age} days ago ({filed}); newer results may exist that this run did not see",
                f"最近一份定期报告在 {age} 天前（{filed}）申报；可能已有更新的结果未被本次读取"),
                rule, measured=measured, sources=sources, as_of=filed)
        return _item("E5", "evidence_freshness", "neutral", title, Bilingual(
            f"latest periodic report filed {filed} ({age} days before this run)", f"最近一份定期报告申报于 {filed}（本次运行前 {age} 天）"),
            rule, measured=measured, sources=sources, as_of=filed)

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
        en = f"the closing price moved {_pct(past)} per year from {first.day} to {last.day} (not dividend-adjusted)"
        zh = f"收盘价从 {first.day} 到 {last.day} 每年变化 {_pct(past)}（未计股息）"
        if needed is not None:
            en += f"; the claim requires {_pct(needed)} per year from the reference price"
            zh += f"；观点要求从参考价起每年 {_pct(needed)}"
        return _item("E6", "price_history", "neutral", Bilingual("Past price trend (context)", "过去股价走势（背景）"), Bilingual(en, zh),
                     measured={"past_annualized_price_change": _s(past), "window": f"{first.day} to {last.day}"},
                     sources=[SourceRef(label=f"daily closes ({self.market.provider_id})", url=self.market.source_url, data_mode=self.market.data_mode)],
                     as_of=last.day)

    # ------------------------------------------------------------- input cross-checks

    def input_checks(self) -> None:
        c = self.claim
        if self.market is not None and (not self.market.currency or self.market.currency == c.currency):
            if _rel_diff(c.reference_price, self.market.last_close) > PRICE_MISMATCH:
                close = _s(self.market.last_close)
                self.warnings.append(Bilingual(
                    f"your reference price {c.reference_price} differs by more than {PRICE_MISMATCH * 100:.0f}% from the close {close} "
                    f"on {self.market.last_date} ({self.market.data_mode.value}); check the value and date",
                    f"你的参考价 {c.reference_price} 与 {self.market.last_date} 的收盘价 {close}（{self.market.data_mode.value}）相差超过 "
                    f"{PRICE_MISMATCH * 100:.0f}%，请核对数值和日期"))
        if self.series and c.base_annual_metric is not None and self.usd_ok() and self.series[-1].value != 0:
            latest = self.series[-1]
            if _rel_diff(c.base_annual_metric, latest.value) > INPUT_MISMATCH:
                self.warnings.append(Bilingual(
                    f"your base {self.metric.en} {_amount(c.base_annual_metric)} differs from the reported {_amount(latest.value)} "
                    f"(FY ending {latest.period_end}); evidence checks use the reported value",
                    f"你填写的基期{self.metric.zh} {_amount_zh(c.base_annual_metric)} 与披露值 {_amount_zh(latest.value)}（截至 {latest.period_end}）"
                    "不一致；证据检查使用披露值"))
        if self.facts and self.facts.shares_outstanding and c.current_shares is not None:
            latest = self.facts.shares_outstanding[-1]
            if _rel_diff(c.current_shares, latest.value) > INPUT_MISMATCH:
                self.warnings.append(Bilingual(
                    f"your current share count {_amount(c.current_shares)} differs from the reported {_amount(latest.value)} as of {latest.period_end}",
                    f"你填写的当前股份数 {_amount_zh(c.current_shares)} 与 {latest.period_end} 披露的 {_amount_zh(latest.value)} 不一致"))

    # ------------------------------------------------------------- sensitivity

    def sensitivity(self) -> Optional[Sensitivity]:
        """Required yearly growth of the metric for alternative multiples and horizons."""
        c = self.claim
        if self.series and self.usd_ok() and self.series[-1].value > 0:
            base, base_end = self.series[-1].value, self.series[-1].period_end
            base_label = f"reported FY ending {base_end}"
        elif c.base_annual_metric is not None and c.base_annual_metric > 0 and c.base_metric_currency == c.currency:
            base, base_end = c.base_annual_metric, None
            base_label = f"your base value ({c.base_metric_period or 'period not stated'})"
        else:
            return None
        cap = market_cap(c.target_price, c.target_assumed_shares)
        h = c.horizon_years
        horizons = sorted({x for x in (h - 2, h - 1, h, h + 1, h + 2, h + 5) if x > 0})
        multiples = sorted({(c.valuation_multiple * k).quantize(Decimal("0.01")) for k in SENSITIVITY_MULTIPLES})
        offset = _years(base_end, c.reference_price_date) if base_end else Decimal(0)
        cells: list[list[Optional[str]]] = []
        for mult in multiples:
            req = required_metric(cap, mult)
            row = []
            for years in horizons:
                total = years + offset
                row.append(_s(annualized_rate(req, base, total)) if total > 0 else None)
            cells.append(row)
        return Sensitivity(
            metric=self.method.metric_name, base_value=base, base_label=base_label,
            multiples=[_s(x.normalize()) for x in multiples], horizons=[_s(x.normalize()) for x in horizons], required_cagr=cells,
            assumed_multiple=_s(c.valuation_multiple), assumed_horizon=_s(h),
            reported_cagr=_s(self.hist_cagr) if self.hist_cagr is not None else None,
        )


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

    rationale = [Bilingual(f"Core check E1 is {growth.stance}: {growth.detail}.", f"核心检查 E1 为{STANCE_ZH[growth.stance]}：{growth.detail_zh}。")]
    if label == "insufficiently_specified":
        rationale.append(Bilingual("Without E1 the central requirement of the claim cannot be compared with reported data.",
                                   "缺少 E1，就无法把观点的核心要求与已披露数据比较。"))
    for item in contrary:
        if item.id != "E1":
            rationale.append(Bilingual(f"{item.id} is contrary: {item.detail}.", f"{item.id} 为反对：{item.detail_zh}。"))
    for item in supporting:
        if item.id != "E1":
            rationale.append(Bilingual(f"{item.id} is supporting: {item.detail}.", f"{item.id} 为支持：{item.detail_zh}。"))
    if checks.growth_gap is not None and checks.growth_gap > GROWTH_SEVERE_GAP:
        rationale.append(Bilingual(
            f"The growth gap of {_pp(checks.growth_gap)} per year exceeds the {_pp(GROWTH_SEVERE_GAP)} limit of rules-v1.",
            f"增速差距每年 {_pp(checks.growth_gap)}，超过 rules-v1 的 {_pp(GROWTH_SEVERE_GAP)} 上限。"))
    missing = [i.id for i in items if i.stance == "missing" and i.id != "E1"]
    if missing:
        rationale.append(Bilingual(f"Not checked for lack of data: {', '.join(missing)}.", f"因缺数据未检查：{'、'.join(missing)}。"))

    display, display_zh = VERDICT_DISPLAY[label], VERDICT_DISPLAY_ZH[label]
    limitations = [
        Bilingual(f"Evidence as of {today}. '{display}' describes the state of the evidence on that date. "
                  "Not Supported Today does not mean the target price is impossible; Supported Today is not a guarantee.",
                  f"证据截至 {today}。「{display_zh}」描述的是当日的证据状态。「目前证据不支持」不表示目标价不可能达到，「目前证据支持」也不是保证。"),
        Bilingual("Rules and thresholds are provisional (rules-v1); team validation of verdict thresholds is still open.",
                  "规则和阈值是临时的（rules-v1），判断阈值尚待团队验证。"),
        Bilingual("Checks use reported financial history and market prices only. Filing text, guidance and news are not read; "
                  "AI-assisted analysis (OA.10–OA.12) is not implemented.",
                  "检查只使用已披露的财务历史和市场价格，不读取申报正文、管理层指引和新闻；AI 辅助分析（OA.10–OA.12）尚未实现。"),
    ]
    if synthetic:
        limitations.insert(0, Bilingual("Synthetic example data was used. This verdict demonstrates the rules and says nothing about a real company.",
                                        "本次使用合成示例数据。这个结论只用来演示规则，与任何真实公司无关。"))
    return Verdict(label=label, display=display, display_zh=display_zh, as_of=today,
                   rationale=[r.en for r in rationale], rationale_zh=[r.zh for r in rationale],
                   limitations=[x.en for x in limitations], limitations_zh=[x.zh for x in limitations],
                   basis=[i.id for i in items], rules_version=RULES_VERSION)


def _recheck(id_, trigger: Bilingual, watch: Bilingual, linked_to, threshold=None) -> RecheckCondition:
    return RecheckCondition(id=id_, trigger=trigger.en, trigger_zh=trigger.zh, watch=watch.en, watch_zh=watch.zh,
                            threshold=threshold, linked_to=linked_to)


def _rechecks(checks: _Checks, items: list[EvidenceItem]) -> list[RecheckCondition]:
    c, m, ml = checks.claim, checks.metric, checks.multiple_label
    out: list[RecheckCondition] = []
    if checks.req_cagr is not None and checks.series:
        latest = checks.series[-1]
        with localcontext(_ctx()):
            path = latest.value * (1 + checks.req_cagr)
        out.append(_recheck(
            "R1",
            Bilingual(f"Next annual {m.en} is reported below {_amount(path)}", f"下一个年度{m.zh}低于 {_amount_zh(path)}"),
            Bilingual(f"10-K / XBRL company facts for the fiscal year after {latest.period_end}",
                      f"{latest.period_end} 之后财年的 10-K / XBRL 财务数据"),
            ["E1", "valuation_multiple", "horizon_years"],
            threshold=f"{_s(path.quantize(Decimal(1)))} ({_pct(checks.req_cagr)} above {_amount(latest.value)})",
        ))
    if checks.latest_shares is not None and checks.implied_share_rate is not None:
        s = checks.latest_shares
        with localcontext(_ctx()):
            path = s.value * (1 + checks.implied_share_rate)
        out.append(_recheck(
            "R2",
            Bilingual(f"Shares outstanding rise above {_amount(path)} within a year of {s.period_end}, or above {_amount(c.target_assumed_shares)} at any time",
                      f"{s.period_end} 后一年内股份数超过 {_amount_zh(path)}，或任何时候超过 {_amount_zh(c.target_assumed_shares)}"),
            Bilingual("cover page of the next 10-Q / 10-K (dei:EntityCommonStockSharesOutstanding); S-1/S-3/424B offerings",
                      "下一份 10-Q / 10-K 封面的股份数；S-1/S-3/424B 增发文件"),
            ["E3", "target_assumed_shares"], threshold=_s(path.quantize(Decimal(1))),
        ))
    if checks.current_multiple is not None:
        with localcontext(_ctx()):
            low = c.valuation_multiple / MULTIPLE_NEUTRAL_RATIO
        out.append(_recheck(
            "R3",
            Bilingual(f"Today's {ml} falls below {low:.2f} (the assumed {c.valuation_multiple} would then need more than {MULTIPLE_NEUTRAL_RATIO}x expansion)",
                      f"当前 {ml} 跌破 {low:.2f}（届时假设的 {c.valuation_multiple} 需要超过 {MULTIPLE_NEUTRAL_RATIO} 倍的估值扩张）"),
            Bilingual(f"price x shares / latest annual {m.en}; now {checks.current_multiple:.2f}",
                      f"股价 × 股份数 / 最近年度{m.zh}；当前 {checks.current_multiple:.2f}"),
            ["E2", "valuation_multiple"], threshold=_s(low.quantize(Decimal("0.01"))),
        ))
    if checks.latest_periodic_filed is not None:
        expected = checks.latest_periodic_filed + timedelta(days=NEXT_REPORT_DAYS)
        out.append(_recheck(
            "R4", Bilingual(f"Next 10-Q or 10-K is filed (expected around {expected})", f"下一份 10-Q 或 10-K 发布（预计 {expected} 前后）"),
            Bilingual("SEC EDGAR filings for the ticker", "该代码在 SEC EDGAR 的申报"), ["E5"],
        ))
    for item in items:
        if item.stance == "missing":
            out.append(_recheck(
                f"R-{item.id}", Bilingual(f"Data for '{item.title}' becomes available", f"「{item.title_zh}」所需数据可以获得"),
                Bilingual(item.detail, item.detail_zh), [item.id],
            ))
    out.append(_recheck(
        "R9", Bilingual("Any confirmed assumption changes (target price, horizon, multiple, target share count)",
                        "任何已确认的假设发生变化（目标价、时间范围、估值倍数、目标股份数）"),
        Bilingual("re-confirm the inputs and run the case again", "重新确认输入并再次运行"),
        ["target_price", "horizon_years", "valuation_multiple", "target_assumed_shares"],
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
        sensitivity=checks.sensitivity(),
        warnings=checks.warnings,
    )
