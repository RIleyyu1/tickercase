"""Layered plain-language report for readers without a finance background.

Structure (adapted from komako-workshop/digital-oracle's report template: layered
signal tables -> analysis -> scenarios -> conclusion and signals to monitor):

  headline            one sentence: what the claim needs vs what the company has done
  layers 1-4          signal | data | what it means, from the claim's needs to data quality
  agreements          checks that point the same way
  divergences         checks that disagree, and what that says
  time view           next report, next fiscal year, the target date
  scenarios           price at the target date under simple formulas (no probabilities)
  upside / downside   what would improve or weaken the case
  monitor             concrete signals with current values and trigger levels

Every number comes from the deterministic calculations, checks and data of the
case. The report adds wording, not judgement; the verdict stays the one from
``analysis.py``. Scenario prices are formulas, not forecasts.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Optional

from .analysis import _ctx, _history_start, _years, share_trend
from .calculations import annualized_rate
from .models import (
    BaseRate,
    BenchmarkReturn,
    CalculationItem,
    InsiderSummary,
    OptionsSnapshot,
    OracleSummary,
    PredictionMarkets,
    PriceBaseRate,
    SentimentSnapshot,
    EvidenceItem,
    MarketSnapshot,
    MetricPoint,
    MonitorRow,
    PlainReport,
    ProbabilityReference,
    RecheckCondition,
    ReportLayer,
    ReportRow,
    ReportedFacts,
    ScenarioRow,
    Text,
    ValidatedClaim,
    ValuationMethod,
    Verdict,
)

CCY_ZH = {"USD": "美元", "HKD": "港元", "CNY": "人民币", "EUR": "欧元"}


def T(en: str, zh: str) -> Text:
    return Text(en=en, zh=zh)


def money_en(v: Decimal, ccy: str = "USD") -> str:
    a, sign = abs(v), "-" if v < 0 else ""
    prefix = "$" if ccy == "USD" else ""
    suffix = "" if ccy == "USD" else f" {ccy}"
    for size, unit in ((Decimal("1e12"), "T"), (Decimal("1e9"), "B"), (Decimal("1e6"), "M")):
        if a >= size:
            return f"{sign}{prefix}{a / size:,.2f}{unit}{suffix}"
    return f"{sign}{prefix}{a:,.2f}{suffix}"


def money_zh(v: Decimal, ccy: str = "USD") -> str:
    a, sign = abs(v), "-" if v < 0 else ""
    name = CCY_ZH.get(ccy, ccy)
    for size, unit, places in ((Decimal("1e12"), "万亿", 2), (Decimal("1e8"), "亿", 2), (Decimal("1e4"), "万", 0)):
        if a >= size:
            return f"{sign}{a / size:,.{places}f} {unit}{name}"
    return f"{sign}{a:,.2f} {name}"


def count_en(v: Decimal) -> str:
    for size, unit in ((Decimal("1e9"), "B"), (Decimal("1e6"), "M")):
        if abs(v) >= size:
            return f"{v / size:,.2f}{unit}"
    return f"{v:,.0f}"


def count_zh(v: Decimal) -> str:
    for size, unit, places in ((Decimal("1e8"), "亿", 2), (Decimal("1e4"), "万", 0)):
        if abs(v) >= size:
            return f"{v / size:,.{places}f} {unit}"
    return f"{v:,.0f}"


def pct(v: Decimal, signed: bool = False) -> str:
    if abs(v) < Decimal("0.0005"):
        return "0.0%"
    return f"{v * 100:+.1f}%" if signed else f"{v * 100:.1f}%"


def prob_text(p: float) -> str:
    return "<0.1%" if p < 0.001 else f"{p * 100:.1f}%"


def times(x: Decimal) -> tuple[str, str]:
    return f"about {x:.1f}x", f"约 {x:.1f} 倍"


def _calc(calcs: list[CalculationItem], name: str) -> Optional[Decimal]:
    return next((c.value for c in calcs if c.name == name and c.status == "ok"), None)


def _item(items: list[EvidenceItem], id_: str) -> Optional[EvidenceItem]:
    return next((i for i in items if i.id == id_), None)


def _dec(item: Optional[EvidenceItem], key: str) -> Optional[Decimal]:
    if item is None or key not in item.measured:
        return None
    return Decimal(item.measured[key])


def _growth(series: list[MetricPoint]) -> Optional[tuple[Decimal, MetricPoint, MetricPoint]]:
    if len(series) < 2 or series[-1].value <= 0:
        return None
    start = _history_start(series)
    if start is None or start.value <= 0:
        return None
    return annualized_rate(series[-1].value, start.value, _years(start.period_end, series[-1].period_end)), start, series[-1]


TONE = {"supporting": "good", "contrary": "bad", "missing": "missing", "neutral": "neutral"}


def build_report(
    claim: ValidatedClaim,
    calculations: list[CalculationItem],
    *,
    facts: Optional[ReportedFacts],
    market: Optional[MarketSnapshot],
    items: list[EvidenceItem],
    verdict: Verdict,
    rechecks: list[RecheckCondition],
    probability: Optional[ProbabilityReference],
    today: date,
    oracle: Optional[OracleSummary] = None,
    options: Optional[OptionsSnapshot] = None,
    base_rate: Optional[BaseRate] = None,
    price_rate: Optional[PriceBaseRate] = None,
    benchmarks: Optional[list[BenchmarkReturn]] = None,
    insiders: Optional[InsiderSummary] = None,
    prediction: Optional[PredictionMarkets] = None,
    sentiment: Optional[SentimentSnapshot] = None,
) -> PlainReport:
    ccy = claim.currency
    is_ps = claim.valuation_method is ValuationMethod.PRICE_TO_SALES
    metric = T("revenue", "营收") if is_ps else T("net income", "净利润")
    mult = "P/S" if is_ps else "P/E"
    name = (facts.company_name if facts and facts.company_name else claim.ticker)
    target_date = claim.reference_price_date + timedelta(days=round(float(claim.horizon_years) * 365.25))
    when = T(f"by about {target_date:%b %Y}", f"到 {target_date.year} 年 {target_date.month} 月前后")
    target_s = f"{claim.target_price:,}"
    series = (facts.revenue if is_ps else facts.net_income) if facts else []
    latest = series[-1] if series else None

    e1, e2, e3, e5, e6 = (_item(items, i) for i in ("E1", "E2", "E3", "E5", "E6"))
    req = _dec(e1, "required_cagr_from_reported")
    hist = _dec(e1, "reported_cagr")
    years_from_fy = _dec(e1, "years_from_period_end_to_target")
    required = _calc(calculations, f"required_{claim.valuation_method.metric_name}")
    cap_target = _calc(calculations, "target_market_cap")
    total_ret = _calc(calculations, "required_return")
    annual_ret = _calc(calculations, "annualized_price_return")
    current_mult = _dec(e2, "current_multiple")
    shares_now = facts.shares_outstanding[-1].value if facts and facts.shares_outstanding else claim.current_shares
    cap_now = market.last_close * shares_now if market and shares_now else None
    past_price = _dec(e6, "past_annualized_price_change")

    # ------------------------------------------------------------------ headline
    label = verdict.label
    if label == "insufficiently_specified":
        reason = e1.detail if e1 else "no data"
        reason_zh = e1.detail_zh if e1 else "缺少数据"
        headline = T(f"Not enough data to judge whether {name} reaches {target_s} {when.en}: {reason}.",
                     f"数据不足，无法判断 {name} 能否{when.zh}到 {target_s}：{reason_zh}。")
    elif req is not None and hist is not None:
        core_en = (f"For {name} to reach {target_s} {when.en}, its {metric.en} has to grow about {pct(req)} a year; "
                   f"over the last few years it changed {pct(hist, True)} a year.")
        core_zh = (f"{name} 要{when.zh}涨到 {target_s}，{metric.zh}需要每年增长约 {pct(req)}；"
                   f"过去几年实际是每年 {pct(hist, True)}。")
        tail = {
            "supported_today": ("Its track record already meets that pace, so today's evidence supports the claim.",
                                "公司过去的表现已经达到这个速度，目前的证据支持这个观点。"),
            "partially_supported": ("That is within reach of its record, but there are gaps, so the claim is only partly supported.",
                                    "这和过去的表现有差距但不算太远，所以只是部分成立。"),
            "not_supported_today": ("That is far beyond its record, so today's evidence does not support the claim.",
                                    "这远远超过公司过去的表现，按目前的证据，这个观点不成立。"),
        }[label]
        headline = T(f"{core_en} {tail[0]}", f"{core_zh}{tail[1]}")
    elif latest is not None and latest.value <= 0:
        headline = T(f"For {name} to reach {target_s} {when.en}, it would need {metric.en} of {money_en(required, ccy)} a year, "
                     f"but its latest annual {metric.en} is {money_en(latest.value)}. Today's evidence does not support the claim.",
                     f"{name} 要{when.zh}涨到 {target_s}，需要每年{metric.zh} {money_zh(required, ccy)}，"
                     f"但最近一年{metric.zh}是 {money_zh(latest.value)}。按目前的证据，这个观点不成立。")
    else:
        headline = T(f"{verdict.display}: see the layers below for what the claim needs.", f"{verdict.display_zh}：观点需要什么，见下方各层。")

    meaning = {
        "supported_today": T("Public data supports the claim today: the company has already grown at the pace the claim needs. It is not a guarantee.",
                             "目前的公开数据支持这个观点：公司过去的增长已经达到观点需要的速度。这不是保证。"),
        "partially_supported": T("Some evidence supports the claim and some does not: only part of what it needs is already happening.",
                                 "有支持也有差距：观点需要的条件只有一部分已经在发生。"),
        "not_supported_today": T("On today's public data, the claim needs far more than the company has delivered. It is not impossible, but something would have to change a lot.",
                                 "按今天的公开数据，观点需要的远超公司过去的表现。这不代表不可能，但需要发生很大的变化。"),
        "insufficiently_specified": T("There is not enough data to compare the claim with the company's record.",
                                      "数据不够，无法把观点和公司过去的表现做比较。"),
    }[label]

    # ------------------------------------------------------------------ layer 1: what the claim needs
    l1 = ReportLayer(title=T("Layer 3 · Fundamentals: what the claim needs", "第 3 层 · 基本面：观点需要什么"))
    if total_ret is not None and annual_ret is not None:
        means_en = f"{pct(annual_ret)} a year for {claim.horizon_years} years"
        means_zh = f"相当于 {claim.horizon_years} 年里每年 {pct(annual_ret)}"
        if past_price is not None:
            means_en += f"; the stock moved {pct(past_price, True)} a year over the last 5 years"
            means_zh += f"；这只股票过去 5 年每年 {pct(past_price, True)}"
        l1.rows.append(ReportRow(signal=T("Share price", "股价"),
                                 data=T(f"{claim.reference_price} → {target_s} ({pct(total_ret, True)})", f"{claim.reference_price} → {target_s}（{pct(total_ret, True)}）"),
                                 meaning=T(means_en, means_zh)))
    if cap_target is not None:
        if cap_now:
            x = cap_target / cap_now
            data = T(f"{money_en(cap_now, ccy)} today → {money_en(cap_target, ccy)}", f"今天 {money_zh(cap_now, ccy)} → {money_zh(cap_target, ccy)}")
            m_en, m_zh = times(x)
            meaning_t = T(f"the whole company must be worth {m_en} what the market pays today", f"整家公司的价值要变成今天的{m_zh}")
        else:
            data = T(money_en(cap_target, ccy), money_zh(cap_target, ccy))
            meaning_t = T("price × assumed share count at the target date", "目标价 × 届时的股数")
        l1.rows.append(ReportRow(signal=T("Company value", "公司总市值"), data=data, meaning=meaning_t))
    if required is not None:
        data = T(f"{money_en(required, ccy)} a year", f"每年 {money_zh(required, ccy)}")
        if latest is not None and latest.value > 0:
            m_en, m_zh = times(required / latest.value)
            meaning_t = T(f"{m_en} the latest year ({money_en(latest.value)}), assuming {mult} {claim.valuation_multiple}",
                          f"是最近一年（{money_zh(latest.value)}）的{m_zh}，按 {mult} {claim.valuation_multiple} 估值")
        else:
            meaning_t = T(f"at an assumed {mult} of {claim.valuation_multiple}", f"按假设的 {mult} {claim.valuation_multiple} 估值")
        l1.rows.append(ReportRow(signal=T(f"Needed {metric.en}", f"需要的{metric.zh}"), data=data, meaning=meaning_t))
    if req is not None:
        tone = TONE[e1.stance] if e1 else "neutral"
        cmp_en = f"past pace {pct(hist, True)} a year" if hist is not None else "no past pace to compare"
        cmp_zh = f"过去是每年 {pct(hist, True)}" if hist is not None else "没有可比较的过去增速"
        l1.rows.append(ReportRow(signal=T(f"Needed {metric.en} growth", f"需要的{metric.zh}增速"),
                                 data=T(f"{pct(req)} a year", f"每年 {pct(req)}"), meaning=T(cmp_en, cmp_zh), tone=tone))

    # ------------------------------------------------------------------ layer 2: track record
    l2 = ReportLayer(title=T("Layer 3 · Fundamentals: what the company has done", "第 3 层 · 基本面：公司过去做到了什么"))
    if facts is not None:
        for label_t, pts in ((T("Revenue growth", "营收增速"), facts.revenue), (T("Net income growth", "净利润增速"), facts.net_income)):
            g = _growth(pts)
            if g is not None:
                rate, start, end = g
                l2.rows.append(ReportRow(signal=label_t,
                                         data=T(f"{pct(rate, True)} a year ({start.period_end.year}–{end.period_end.year})",
                                                f"每年 {pct(rate, True)}（{start.period_end.year}–{end.period_end.year}）"),
                                         meaning=T(f"{money_en(start.value)} → {money_en(end.value)}", f"{money_zh(start.value)} → {money_zh(end.value)}"),
                                         tone="good" if rate > 0 else "bad"))
            elif pts:
                l2.rows.append(ReportRow(signal=label_t, data=T(f"latest {money_en(pts[-1].value)}", f"最近 {money_zh(pts[-1].value)}"),
                                         meaning=T("not enough positive history to compute a growth rate", "历史数据不足或不为正，无法计算增速"), tone="missing"))
        if facts.revenue and facts.net_income and facts.revenue[-1].period_end == facts.net_income[-1].period_end and facts.revenue[-1].value > 0:
            margin = facts.net_income[-1].value / facts.revenue[-1].value
            l2.rows.append(ReportRow(signal=T("Profit margin", "利润率"), data=T(pct(margin), pct(margin)),
                                     meaning=T(f"keeps about {margin * 100:.0f} cents of every $1 of sales as profit",
                                               f"每卖 100 元，大约赚 {margin * 100:.0f} 元"),
                                     tone="good" if margin > 0 else "bad"))
        trend = share_trend(facts.shares_outstanding) if facts.shares_outstanding else None
        if trend is not None:
            rate, start, end = trend
            if rate > Decimal("0.005"):
                m = T("new shares are issued, so each share owns a smaller slice", "公司在增发，每股分到的份额被摊薄")
            elif rate < Decimal("-0.005"):
                m = T("buybacks: each share owns a slightly larger slice", "公司在回购，每股分到的份额变大")
            else:
                m = T("roughly unchanged", "基本不变")
            l2.rows.append(ReportRow(signal=T("Share count", "股数变化"),
                                     data=T(f"{pct(rate, True)} a year ({count_en(start.value)} → {count_en(end.value)})",
                                            f"每年 {pct(rate, True)}（{count_zh(start.value)} → {count_zh(end.value)} 股）"),
                                     meaning=m, tone="bad" if rate > Decimal("0.03") else "neutral"))
    if not l2.rows:
        l2.rows.append(ReportRow(signal=T("Financial history", "财务历史"), data=T("not available", "未取得"),
                                 meaning=T("SEC financial data was not available in this run", "本次没有取得 SEC 财务数据"), tone="missing"))

    # ------------------------------------------------------------------ layer 3: market view
    l3 = ReportLayer(title=T("Layer 3 · Fundamentals: how the market values it today", "第 3 层 · 基本面：市场今天给的估值"))
    if current_mult is not None:
        l3.rows.append(ReportRow(
            signal=T(f"Valuation ({mult})", f"估值（{mult}）"),
            data=T(f"today {current_mult:.1f}, claim assumes {claim.valuation_multiple}", f"今天 {current_mult:.1f}，观点假设 {claim.valuation_multiple}"),
            meaning=T(f"investors pay about ${current_mult:.0f} for each $1 of yearly {metric.en}",
                      f"投资者愿意为每 1 元年{metric.zh}付约 {current_mult:.0f} 元"),
            tone=TONE[e2.stance] if e2 else "neutral"))
    if market is not None:
        if market.annualized_volatility is not None:
            v = market.annualized_volatility
            l3.rows.append(ReportRow(signal=T("Price swings", "股价波动"), data=T(f"{pct(v)} a year", f"每年约 {pct(v)}"),
                                     meaning=T(f"moves of ±{v * 100:.0f}% within a year are common for this stock",
                                               f"一年内上下波动 {v * 100:.0f}% 左右对这只股票很常见")))
        if past_price is not None:
            l3.rows.append(ReportRow(signal=T("Past 5 years", "过去 5 年股价"),
                                     data=T(f"{pct(past_price, True)} a year", f"每年 {pct(past_price, True)}"),
                                     meaning=T(f"the claim needs {pct(annual_ret)} a year from here" if annual_ret is not None else "",
                                               f"观点需要从现在起每年 {pct(annual_ret)}" if annual_ret is not None else "")))
    if not l3.rows:
        l3.rows.append(ReportRow(signal=T("Market data", "市场数据"), data=T("not available", "未取得"),
                                 meaning=T("no price data in this run", "本次没有取得股价数据"), tone="missing"))

    # ------------------------------------------------------------------ layer 4: data quality
    l4 = ReportLayer(title=T("Layer 5 · How reliable the data is", "第 5 层 · 数据可靠吗"))
    if e5 is not None:
        l4.rows.append(ReportRow(signal=T("Latest report", "最新财报"), data=T(e5.detail, e5.detail_zh),
                                 meaning=T("newer results may change the picture" if e5.stance == "missing" else "recent enough",
                                           "可能已有更新的结果" if e5.stance == "missing" else "足够新"), tone=TONE[e5.stance]))
    sources_en = "SEC filings and XBRL financial data; daily closing prices" if facts or market else "no public data"
    sources_zh = "SEC 申报与 XBRL 财务数据；每日收盘价" if facts or market else "没有公开数据"
    modes = {x.data_mode.value for x in (facts, market) if x is not None}
    l4.rows.append(ReportRow(signal=T("Sources", "数据来源"), data=T(sources_en, sources_zh),
                             meaning=T(f"data mode: {', '.join(sorted(modes)) or 'none'}", f"数据模式：{'、'.join(sorted(modes)) or '无'}"),
                             tone="bad" if "synthetic" in modes else "neutral"))
    missing = [i for i in items if i.stance == "missing"]
    if missing:
        l4.rows.append(ReportRow(signal=T("Gaps", "缺口"), data=T(f"{len(missing)} check(s) without data", f"{len(missing)} 项检查缺数据"),
                                 meaning=T("; ".join(i.title for i in missing), "；".join(i.title_zh for i in missing)), tone="missing"))
    l4.rows.append(ReportRow(signal=T("Not read", "没有读取"), data=T("filing text, guidance, news", "申报正文、管理层指引、新闻"),
                             meaning=T("only reported numbers and prices are compared", "只比较了已披露的数字和股价")))

    # ------------------------------------------------------------------ analysis
    agreements: list[Text] = []
    divergences: list[Text] = []
    contrary = [i for i in items if i.stance == "contrary"]
    supporting = [i for i in items if i.stance == "supporting"]
    if len(contrary) >= 2:
        agreements.append(T(f"{len(contrary)} checks point against the claim: " + ", ".join(i.title for i in contrary) + ".",
                            f"有 {len(contrary)} 项检查都对观点不利：" + "、".join(i.title_zh for i in contrary) + "。"))
    if len(supporting) >= 2:
        agreements.append(T(f"{len(supporting)} checks support it: " + ", ".join(i.title for i in supporting) + ".",
                            f"有 {len(supporting)} 项检查支持观点：" + "、".join(i.title_zh for i in supporting) + "。"))
    if not agreements:
        agreements.append(T("No two checks point clearly the same way; read the layers one by one.", "没有两项检查明显指向同一方向，请逐层查看。"))
    s1, s2 = (e1.stance if e1 else None), (e2.stance if e2 else None)
    if s1 == "contrary" and s2 == "supporting":
        divergences.append(T("Valuation is not the problem (the assumed multiple is not above today's); the problem is results: "
                             f"{metric.en} would have to grow far faster than it has.",
                             f"问题不在估值（假设的倍数不比今天高），而在业绩：{metric.zh}需要的增长远超过去。"))
    if s1 in ("supporting", "neutral") and s2 == "contrary":
        divergences.append(T("The business pace is close to what is needed, but the claim also needs the market to pay a higher multiple than today.",
                             "业绩增速接近需要的水平，但观点还要求市场给出比今天更高的估值。"))
    if past_price is not None and annual_ret is not None and hist is not None:
        if past_price >= annual_ret and s1 == "contrary":
            divergences.append(T(f"The stock has risen at the needed pace before ({pct(past_price, True)} a year), but {metric.en} did not keep up "
                                 f"({pct(hist, True)}): past gains came mostly from a richer valuation, which is hard to repeat.",
                                 f"股价过去涨得够快（每年 {pct(past_price, True)}），但{metric.zh}没有跟上（每年 {pct(hist, True)}）："
                                 "过去的涨幅主要来自估值变贵，这很难一直重复。"))
        if past_price < annual_ret and s1 == "supporting":
            divergences.append(T(f"{metric.en.capitalize()} has grown fast enough, but the share price has not followed so far.",
                                 f"{metric.zh}增长够快，但股价到目前为止没有跟上。"))
    if e3 is not None and "reported_annual_change" in e3.measured and Decimal(e3.measured["reported_annual_change"]) > Decimal("0.03"):
        divergences.append(T("The share count is growing quickly, so company-level growth turns into less per-share growth.",
                             "股数增长较快，公司整体的增长分到每股上会变少。"))
    if not divergences:
        divergences.append(T("No notable disagreement between the checks.", "各项检查之间没有明显矛盾。"))

    # ------------------------------------------------------------------ time view
    time_view: list[ReportRow] = []
    r1 = next((r for r in rechecks if r.id == "R1"), None)
    r4 = next((r for r in rechecks if r.id == "R4"), None)
    if r4 is not None:
        time_view.append(ReportRow(signal=T("Next 3 months", "未来 3 个月"), data=T(r4.trigger, r4.trigger_zh),
                                   meaning=T("new quarterly numbers update every check", "新的季度数据会更新所有检查")))
    if r1 is not None and req is not None and latest is not None:
        path = latest.value * (1 + req)
        time_view.append(ReportRow(signal=T("Next fiscal year", "下一个财年"),
                                   data=T(f"{metric.en} about {money_en(path, ccy)}", f"{metric.zh}约 {money_zh(path, ccy)}"),
                                   meaning=T("the first milestone on the claim's path", "这是观点路径上的第一个里程碑")))
    if required is not None:
        time_view.append(ReportRow(signal=T("Target date", "目标日期"), data=T(f"{target_date}", f"{target_date}"),
                                   meaning=T(f"{metric.en} of {money_en(required, ccy)} a year and a {mult} of {claim.valuation_multiple}",
                                             f"每年{metric.zh} {money_zh(required, ccy)}，且 {mult} 为 {claim.valuation_multiple}")))

    # ------------------------------------------------------------------ scenarios
    scenarios: list[ScenarioRow] = []
    note = None
    if latest is not None and latest.value > 0 and hist is not None and years_from_fy is not None:
        def price_for(growth: Decimal, multiple: Decimal) -> Decimal:
            with localcontext(_ctx()):
                metric_t = latest.value * ((1 + growth).ln() * years_from_fy).exp()
                return metric_t * multiple / claim.target_assumed_shares

        def row(name_t: Text, growth: Decimal, multiple: Decimal, meaning_t: Text) -> ScenarioRow:
            p = price_for(growth, multiple)
            vs = p / claim.target_price - 1
            return ScenarioRow(name=name_t,
                               assumptions=T(f"{metric.en} {pct(growth, True)} a year, {mult} {multiple:.1f}",
                                             f"{metric.zh}每年 {pct(growth, True)}，{mult} {multiple:.1f}"),
                               price=f"{p:,.2f}", vs_target=pct(vs, True), meaning=meaning_t)

        if current_mult is not None:
            scenarios.append(row(T("Past pace, today's valuation", "延续过去增速 + 今天的估值"), hist, current_mult,
                                 T("if nothing changes", "如果一切照旧")))
        scenarios.append(row(T("Past pace, claimed valuation", "延续过去增速 + 观点假设的估值"), hist, claim.valuation_multiple,
                             T("the business keeps its record; valuation as the claim assumes", "业绩按过去的速度，估值按观点的假设")))
        if req is not None:
            scenarios.append(row(T("The claim's path", "观点需要的路径"), req, claim.valuation_multiple,
                                 T("what has to happen for the target", "要达到目标价必须发生的情况")))
        note = T("Scenario prices are formulas: latest annual metric grown at the stated rate to the target date, times the multiple, "
                 "divided by the assumed share count. They are not forecasts and carry no probabilities.",
                 "情景价格只是公式推演：最近一年的指标按所列增速增长到目标日，乘以估值倍数，再除以届时的股数。它们不是预测，也没有概率。")
        if probability is not None and probability.status == "ok":
            note = T(note.en + f" The optional probability section estimates P(price ≥ target) = {probability.target_probability * 100:.1f}% under its own model.",
                     note.zh + f" 附加的概率参考页在其模型假设下估计到期价 ≥ 目标价的概率为 {probability.target_probability * 100:.1f}%。")

    # ------------------------------------------------------------------ conclusion: upside / downside / monitor
    upside: list[Text] = []
    downside: list[Text] = []
    if r1 is not None and req is not None and latest is not None:
        path = latest.value * (1 + req)
        upside.append(T(f"Next annual {metric.en} at or above {money_en(path, ccy)} would show the company on the claim's path.",
                        f"下一年{metric.zh}达到或超过 {money_zh(path, ccy)}，说明公司正走在观点需要的路径上。"))
        if hist is not None and hist < req:
            downside.append(T(f"If {metric.en} keeps changing at {pct(hist, True)} a year, the gap to the target widens every year.",
                              f"如果{metric.zh}继续按每年 {pct(hist, True)} 变化，和目标的差距会逐年扩大。"))
        else:
            downside.append(T(f"{metric.en.capitalize()} growth slowing below {pct(req)} a year.", f"{metric.zh}增速放慢到每年 {pct(req)} 以下。"))
    if current_mult is not None:
        if claim.valuation_multiple > current_mult:
            upside.append(T(f"A market willing to pay a {mult} near {claim.valuation_multiple} (today {current_mult:.1f}).",
                            f"市场愿意给出接近 {claim.valuation_multiple} 的 {mult}（今天 {current_mult:.1f}）。"))
        downside.append(T(f"A {mult} falling well below the assumed {claim.valuation_multiple}.", f"{mult} 明显跌破假设的 {claim.valuation_multiple}。"))
    downside.append(T("Faster share issuance than assumed.", "增发速度快于假设。"))

    monitor: list[MonitorRow] = []
    if r1 is not None and latest is not None:
        monitor.append(MonitorRow(signal=T(f"Annual {metric.en}", f"年度{metric.zh}"),
                                  current=T(money_en(latest.value), money_zh(latest.value)),
                                  threshold=T(r1.trigger, r1.trigger_zh),
                                  meaning=T("below it: off the claim's path", "低于它：偏离观点路径")))
    r2 = next((r for r in rechecks if r.id == "R2"), None)
    if r2 is not None and shares_now is not None:
        monitor.append(MonitorRow(signal=T("Share count", "股数"), current=T(count_en(shares_now), count_zh(shares_now) + " 股"),
                                  threshold=T(r2.trigger, r2.trigger_zh), meaning=T("above it: more dilution than assumed", "超过它：稀释多于假设")))
    r3 = next((r for r in rechecks if r.id == "R3"), None)
    if r3 is not None and current_mult is not None:
        monitor.append(MonitorRow(signal=T(f"Today's {mult}", f"当前 {mult}"), current=T(f"{current_mult:.1f}", f"{current_mult:.1f}"),
                                  threshold=T(r3.trigger, r3.trigger_zh), meaning=T("below it: the valuation assumption gets harder", "低于它：估值假设更难实现")))
    if r4 is not None:
        monitor.append(MonitorRow(signal=T("Next report", "下一份财报"), current=T(e5.measured.get("latest_periodic_filing", "") if e5 else "", e5.measured.get("latest_periodic_filing", "") if e5 else ""),
                                  threshold=T(r4.trigger, r4.trigger_zh), meaning=T("re-run the case with new numbers", "用新数据重新运行")))

    # ------------------------------------------------------------------ oracle layers
    pricing = ReportLayer(title=T("Layer 1 · Direct pricing (options and prediction markets)", "第 1 层 · 直接定价信号（期权与预测市场）"))
    if options is not None:
        pricing.rows.append(ReportRow(
            signal=T("Option expiry used", "使用的期权到期日"), data=T(f"{options.expiry} ({options.days_to_expiry} days)", f"{options.expiry}（{options.days_to_expiry} 天）"),
            meaning=T("the longest listed expiry" if options.expiry < (oracle.target_date if oracle else options.expiry) else "first expiry after the target date",
                      "最远的上市到期日" if options.expiry < (oracle.target_date if oracle else options.expiry) else "目标日之后的第一个到期日")))
        if options.atm_iv is not None:
            pricing.rows.append(ReportRow(signal=T("Implied volatility (at today's price)", "隐含波动率（平值）"),
                                          data=T(f"{options.atm_iv * 100:.0f}% a year", f"每年 {options.atm_iv * 100:.0f}%"),
                                          meaning=T("how much movement traders pay for", "交易者为多大的波动付费")))
        pricing.rows.append(ReportRow(
            signal=T("Highest strike listed", "最高行权价"), data=T(f"{options.max_strike}", f"{options.max_strike}"),
            meaning=T(f"open interest at or above {claim.target_price}: {options.oi_at_or_above_target:,} contracts" + (" — nobody holds a bet at the target" if options.oi_at_or_above_target == 0 else ""),
                      f"目标价 {claim.target_price} 及以上的未平仓合约 {options.oi_at_or_above_target:,} 张" + ("——没有人押注目标价" if options.oi_at_or_above_target == 0 else "")),
            tone="bad" if options.oi_at_or_above_target == 0 else "neutral"))
    if oracle is not None:
        m1 = next((m for m in oracle.methods if m.id == "M1" and m.status == "ok"), None)
        if m1 is not None:
            pricing.rows.append(ReportRow(
                signal=T("Option-implied probability", "期权隐含概率"),
                data=T(f"end above target {m1.probability * 100:.1f}%, touch {m1.touch_probability * 100:.1f}%",
                       f"到期高于目标 {m1.probability * 100:.1f}%，期间触及 {m1.touch_probability * 100:.1f}%"),
                meaning=T("touching is far likelier than staying there: a spike-and-fade path" if m1.touch_probability > 2 * m1.probability else "risk-neutral, priced by traders",
                          "触及远比守住容易：更像冲高后回落" if m1.touch_probability > 2 * m1.probability else "风险中性概率，由交易者定价"),
                tone="bad" if m1.probability < 0.05 else "neutral"))
    if prediction is not None:
        if prediction.markets:
            for mk in prediction.markets[:2]:
                pricing.rows.append(ReportRow(signal=T("Polymarket", "Polymarket"), data=T(mk.question, mk.question),
                                              meaning=T(f"Yes {mk.probability_yes * 100:.0f}%, ends {mk.end_date:%Y-%m-%d}: a short-term contract, far shorter than the claim" if mk.probability_yes is not None and mk.end_date else "price unavailable",
                                                        f"「是」{mk.probability_yes * 100:.0f}%，{mk.end_date:%Y-%m-%d} 结束：短期合约，远短于观点的期限" if mk.probability_yes is not None and mk.end_date else "无价格")))
        else:
            pricing.rows.append(ReportRow(signal=T("Polymarket", "Polymarket"), data=T("no open contract on this stock", "没有这只股票的在售合约"),
                                          meaning=T("no event market prices this claim", "没有事件市场为这个观点定价"), tone="missing"))

    rates = ReportLayer(title=T("Layer 2 · Base rates and peers", "第 2 层 · 历史基准率与对标"))
    if base_rate is not None and base_rate.companies:
        ex_en = ", ".join(f"{e['name']} ({float(e['cagr']) * 100:.0f}%/yr)" for e in base_rate.examples[:3])
        rates.rows.append(ReportRow(
            signal=T(f"Similar-sized companies reaching {pct(base_rate.required_cagr)} {metric.en} growth", f"同规模公司达到每年 {pct(base_rate.required_cagr)} {metric.zh}增速"),
            data=T(f"{base_rate.achieved} of {base_rate.companies} ({(base_rate.rate or 0) * 100:.1f}%), {base_rate.start_year}–{base_rate.end_year}",
                   f"{base_rate.companies} 家中 {base_rate.achieved} 家（{(base_rate.rate or 0) * 100:.1f}%），{base_rate.start_year}–{base_rate.end_year}"),
            meaning=T(f"median {pct(base_rate.median_cagr or Decimal(0))} a year; fastest: {ex_en}", f"中位数每年 {pct(base_rate.median_cagr or Decimal(0))}；最快的：{ex_en}"),
            tone="bad" if (base_rate.rate or 0) < 0.05 else "good" if (base_rate.rate or 0) > 0.3 else "neutral"))
    if price_rate is not None and price_rate.windows:
        rates.rows.append(ReportRow(
            signal=T(f"This stock over {price_rate.window_months}-month windows", f"这只股票的 {price_rate.window_months} 个月窗口"),
            data=T(f"{price_rate.hits} of {price_rate.windows} rose ≥{float(price_rate.required_return) * 100:.0f}%", f"{price_rate.windows} 个中 {price_rate.hits} 个涨幅 ≥{float(price_rate.required_return) * 100:.0f}%"),
            meaning=T(f"since {price_rate.history_start}; overlapping windows, context only unless the history is long",
                      f"自 {price_rate.history_start} 起；窗口互相重叠，历史不够长时只作参考")))
    for b in benchmarks or []:
        rates.rows.append(ReportRow(signal=T(b.label, b.label), data=T(f"{pct(b.total_return, True)} ({b.start}–{b.end})", f"{pct(b.total_return, True)}（{b.start}–{b.end}）"),
                                    meaning=T(f"{pct(b.annual_return, True)} a year; the claim needs {pct(annual_ret)} a year" if annual_ret is not None else "",
                                              f"每年 {pct(b.annual_return, True)}；观点需要每年 {pct(annual_ret)}" if annual_ret is not None else "")))

    env = ReportLayer(title=T("Layer 4 · Insiders and market environment", "第 4 层 · 内部人与市场环境"))
    if insiders is not None:
        plan_share = (insiders.plan_sale_value / insiders.sale_value) if insiders.sale_value else Decimal(0)
        env.rows.append(ReportRow(
            signal=T("Insider open-market trades (12 months)", "内部人公开市场交易（12 个月）"),
            data=T(f"buys {insiders.purchases} ({money_en(insiders.purchase_value)}), sales {insiders.sales} ({money_en(insiders.sale_value)})",
                   f"买入 {insiders.purchases} 笔（{money_zh(insiders.purchase_value)}），卖出 {insiders.sales} 笔（{money_zh(insiders.sale_value)}）"),
            meaning=T(f"{plan_share * 100:.0f}% of sales under pre-set 10b5-1 plans (less informative); {insiders.sellers} sellers, {insiders.buyers} buyers",
                      f"其中 {plan_share * 100:.0f}% 的卖出属于预先设定的 10b5-1 计划（信息量较低）；卖出 {insiders.sellers} 人，买入 {insiders.buyers} 人"),
            tone="bad" if insiders.purchases == 0 and insiders.sales > 0 else "good" if insiders.purchases > insiders.sales else "neutral"))
        env.rows.append(ReportRow(
            signal=T("Other Form 4 entries", "其他 Form 4 记录"),
            data=T(f"grants {insiders.grants}, option exercises {insiders.exercises}, tax withholding {insiders.tax_withholding}, other {insiders.other}",
                   f"授予 {insiders.grants}、行权 {insiders.exercises}、代扣税 {insiders.tax_withholding}、其他 {insiders.other}"),
            meaning=T(f"not buy/sell decisions; {insiders.filings_read} of {insiders.filings_listed} filings read",
                      f"这些不是买卖决定；读取了 {insiders.filings_listed} 份中的 {insiders.filings_read} 份")))
    if sentiment is not None:
        env.rows.append(ReportRow(signal=T("CNN Fear & Greed", "CNN 恐惧贪婪指数"),
                                  data=T(f"{sentiment.score:.0f} ({sentiment.rating})", f"{sentiment.score:.0f}（{sentiment.rating}）"),
                                  meaning=T(f"a month ago {sentiment.previous_month:.0f}; below 30 means investors are fearful" if sentiment.previous_month is not None else "below 30 means fearful",
                                            f"一个月前 {sentiment.previous_month:.0f}；低于 30 表示市场恐惧" if sentiment.previous_month is not None else "低于 30 表示市场恐惧"),
                                  tone="bad" if sentiment.score < 30 else "good" if sentiment.score > 60 else "neutral"))
    if oracle is not None and oracle.risk_free_rate is not None:
        env.rows.append(ReportRow(signal=T("10-year Treasury yield", "10 年期美债收益率"), data=T(pct(oracle.risk_free_rate), pct(oracle.risk_free_rate)),
                                  meaning=T("the return available without stock risk", "不承担股票风险就能拿到的回报")))

    if oracle is not None and oracle.low is not None:
        reason_en = reason_zh = ""
        if req is not None and hist is not None:
            reason_en = f" The business would need {metric.en} growth of {pct(req)} a year; it has managed {pct(hist, True)}."
            reason_zh = f"业绩上，{metric.zh}需要每年增长 {pct(req)}，过去是每年 {pct(hist, True)}。"
        lo_s, hi_s = prob_text(oracle.low), prob_text(oracle.high)
        if oracle.condition == "touch":
            # a spike does not need the business to grow into the price, so the growth sentence is left out
            headline = T(f"Probability that {name} touches {target_s} at some point before {target_date:%b %Y}: about {lo_s}–{hi_s}, "
                         f"{oracle.tier_label.en}. {oracle.agreement.en}",
                         f"{name} 在 {target_date.year} 年 {target_date.month} 月之前任意时点触及 {target_s} 的概率约 {lo_s}–{hi_s}，"
                         f"属于「{oracle.tier_label.zh}」。{oracle.agreement.zh}")
        else:
            headline = T(f"Probability that {name} is at or above {target_s} {when.en}: about {lo_s}–{hi_s}, "
                         f"{oracle.tier_label.en}.{reason_en} {oracle.agreement.en}",
                         f"{name} {when.zh}达到 {target_s} 的概率约 {lo_s}–{hi_s}，属于「{oracle.tier_label.zh}」。"
                         f"{reason_zh}{oracle.agreement.zh}")
        m1 = next((m for m in oracle.methods if m.id == "M1" and m.status == "ok"), None)
        m3 = next((m for m in oracle.methods if m.id == "M3" and m.status == "ok"), None)
        if m1 and m3 and m3.probability is not None and m1.probability > 3 * max(m3.probability, 1e-9):
            divergences.insert(0, T(f"The option market ({m1.probability * 100:.1f}%) is more generous than the business base rate ({m3.probability * 100:.1f}%): "
                                    "traders pay for big swings, but the growth the claim needs is rare among similar companies.",
                                    f"期权市场（{m1.probability * 100:.1f}%）比业绩基准率（{m3.probability * 100:.1f}%）乐观："
                                    "交易者为大幅波动付费，但观点需要的增长在同规模公司里很少见。"))
        if m1 and m1.touch_probability and m1.touch_probability > 2 * m1.probability:
            divergences.append(T(f"Touching {target_s} at some point ({m1.touch_probability * 100:.1f}%) is much likelier than being there at the end ({m1.probability * 100:.1f}%).",
                                 f"期间某个时点触及 {target_s}（{m1.touch_probability * 100:.1f}%）比到期时守在那里（{m1.probability * 100:.1f}%）容易得多。"))
    if insiders is not None and insiders.purchases == 0 and insiders.sales > 0:
        agreements.append(T(f"Insiders made no open-market purchases in 12 months while selling {money_en(insiders.sale_value)}.",
                            f"内部人 12 个月内没有任何公开市场买入，同时卖出 {money_zh(insiders.sale_value)}。"))
    if sentiment is not None and sentiment.score < 30:
        agreements.append(T(f"Market mood is fearful (Fear & Greed {sentiment.score:.0f}), a headwind for high-growth stories.",
                            f"市场情绪偏恐惧（恐惧贪婪指数 {sentiment.score:.0f}），对高增长故事不利。"))

    fundamentals = ReportLayer(title=T("Layer 3 · Fundamentals", "第 3 层 · 基本面"),
                               rows=[r for r in (*l1.rows, *l2.rows, *l3.rows) if not (r.tone == "missing" and r.signal.en == "Financial history" and l1.rows)])
    if len(agreements) > 1:
        agreements = [a for a in agreements if not a.en.startswith("No two checks")]
    layers = [layer for layer in (pricing, rates, fundamentals, env, l4) if layer.rows]
    return PlainReport(headline=headline, verdict_meaning=meaning, layers=layers, agreements=agreements,
                       divergences=divergences, time_view=time_view, scenarios=scenarios, scenario_note=note,
                       upside=upside, downside=downside, monitor=monitor)
