"""Probability that the claim comes true, from independent methods compared side by side.

M1 options    risk-neutral P(price at the target date >= target) from the option market's
              implied volatility at the target strike (Black-Scholes N(d2)); also the
              probability of touching the target at any time before the date.
M2 model      same lognormal formula with the stock's historical volatility instead of
              the option market's; isolates how much the market's view differs from history.
M3 base rate  share of US companies of similar size whose revenue (or net income) grew at
              least as fast as the claim needs, over the same number of years (SEC frames).
              Probability of the business condition, not of the price.
M4 history    share of past windows of the same length in which this stock rose at least
              as much as needed. Windows overlap heavily, so it is used in the range only when
              the history covers at least 4 non-overlapping windows; otherwise it is context.

The range is [min, max] over the usable methods. No method is weighted by judgement;
where they disagree the summary says which is higher and why it measures something different.

The claim's condition decides which probability the range uses: "end" (at or above the
target on the date) or "touch" (reaches it at any time before). For touch, M1 and M2 use
the first-passage formula, M4 counts windows whose highest month-end close reached the
target, and M3 is context only: a spike does not need the business to grow into the price.

event_move() reads the option market's term structure for the extra move it prices around
one dated event; scenario() gives the probability under the user's own event scenario.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from statistics import NormalDist
from typing import Optional

from .models import (
    BaseRate,
    EventMove,
    ScenarioResult,
    TermPoint,
    LadderRow,
    MarketSnapshot,
    OptionsSnapshot,
    OracleSummary,
    PriceBaseRate,
    ProbabilityMethod,
    Text,
    ValidatedClaim,
    ValuationMethod,
)
from .providers.options import iv_at

_N = NormalDist()
MIN_INDEPENDENT_WINDOWS = 4


def independent_windows(price_rate: Optional[PriceBaseRate]) -> int:
    if price_rate is None or price_rate.window_months < 1:
        return 0
    return (price_rate.windows + price_rate.window_months) // price_rate.window_months
# tier of the middle estimate: <5% lottery, 5-20% unlikely, 20-50% possible, >50% likely
TIERS = (
    (0.05, "lottery", "lottery-ticket odds", "彩票级"),
    (0.20, "low", "unlikely", "不太可能"),
    (0.50, "possible", "possible", "有可能"),
    (1.01, "likely", "likely", "较可能"),
)


def T(en: str, zh: str) -> Text:
    return Text(en=en, zh=zh)


def p_end_above(spot: float, level: float, vol: float, years: float, rate: float) -> float:
    """Lognormal P(S_T >= level) with drift rate (risk-neutral when rate is the risk-free rate)."""
    if level <= 0:
        return 1.0
    sd = vol * math.sqrt(years)
    return _N.cdf((math.log(spot / level) + (rate - vol * vol / 2) * years) / sd)


def p_touch(spot: float, level: float, vol: float, years: float, rate: float) -> float:
    """P(max S_t >= level for t <= T) for geometric Brownian motion (first-passage formula)."""
    if level <= spot:
        return 1.0
    nu = rate - vol * vol / 2
    b = math.log(level / spot)
    sd = vol * math.sqrt(years)
    return min(1.0, _N.cdf((-b + nu * years) / sd) + math.exp(2 * nu * b / (vol * vol)) * _N.cdf((-b - nu * years) / sd))


def _pct(p: Optional[float]) -> str:
    if p is None:
        return "—"
    if p < 0.001:
        return "<0.1%"
    return f"{p * 100:.1f}%"


def build_oracle(
    claim: ValidatedClaim,
    *,
    market: Optional[MarketSnapshot],
    options: Optional[OptionsSnapshot],
    base_rate: Optional[BaseRate],
    price_rate: Optional[PriceBaseRate],
    risk_free: Optional[Decimal],
    today: date,
) -> OracleSummary:
    touch = claim.price_condition == "touch"
    target = float(claim.target_price)
    target_date = claim.reference_price_date + timedelta(days=round(float(claim.horizon_years) * 365.25))
    years = max((target_date - today).days / 365.25, 1 / 365.25)
    spot = float(market.last_close) if market else (float(options.underlying_price) if options and options.underlying_price else None)
    r = float(risk_free) if risk_free is not None else 0.0
    r_note = T(f"risk-free rate {r * 100:.2f}% (10-year Treasury yield)", f"无风险利率 {r * 100:.2f}%（10 年期美债收益率）") if risk_free is not None \
        else T("risk-free rate unavailable; 0% used", "未取得无风险利率，按 0% 计算")
    methods: list[ProbabilityMethod] = []

    # ---------------------------------------------------------------- M1 options
    m1_name = T("Option market (implied)", "期权市场（隐含）")
    m1_measures = T("what traders with money at stake price in, via implied volatility", "真金白银交易的期权，通过隐含波动率反映的定价")
    if options is not None and options.target_iv and spot:
        vol = options.target_iv
        lim = [T("Risk-neutral probability: it prices risk, not a forecast of real-world odds.", "风险中性概率：反映的是风险定价，不是对真实概率的预测。"),
               T("Uses one implied volatility for the whole period.", "整个期间只用一个隐含波动率。")]
        if options.target_iv_extrapolated:
            lim.append(T(f"No option is listed at {target:g}; the highest strike ({options.max_strike}) IV is used.",
                         f"没有 {target:g} 行权价的期权，用最高行权价（{options.max_strike}）的隐含波动率代替。"))
        if options.expiry < target_date:
            lim.append(T(f"The longest expiry ({options.expiry}) ends before the target date; its volatility is extended to {target_date}.",
                         f"最远到期日（{options.expiry}）早于目标日，把它的波动率延用到 {target_date}。"))
        p = p_end_above(spot, target, vol, years, r)
        methods.append(ProbabilityMethod(
            id="M1", name=m1_name, status="ok", probability=p, touch_probability=p_touch(spot, target, vol, years, r), measures=m1_measures,
            detail=T(f"expiry {options.expiry}, implied volatility {vol * 100:.0f}% at strike ~{target:g}; open interest at or above the target: "
                     f"{options.oi_at_or_above_target:,} of {options.total_call_oi:,} calls; {r_note.en}",
                     f"到期日 {options.expiry}，行权价约 {target:g} 处的隐含波动率 {vol * 100:.0f}%；目标价及以上的未平仓合约 "
                     f"{options.oi_at_or_above_target:,} 张（共 {options.total_call_oi:,} 张看涨）；{r_note.zh}"),
            inputs={"spot": f"{spot:g}", "target": f"{target:g}", "years": f"{years:.2f}", "implied_volatility": f"{vol:.4f}", "rate": f"{r:.4f}"},
            limitations=lim))
    else:
        methods.append(ProbabilityMethod(id="M1", name=m1_name, status="not_computable", measures=m1_measures,
                                         detail=T("no usable option chain in this run", "本次没有取得可用的期权链")))

    # ---------------------------------------------------------------- M2 historical-volatility model
    m2_name = T("Statistical model (historical volatility)", "统计模型（历史波动率）")
    m2_measures = T("how often a stock this volatile ends above the target, if it moves like it has in the past",
                    "如果股价按过去的波动方式运动，到期时高于目标价的可能性")
    if market is not None and market.annualized_volatility and spot:
        vol = float(market.annualized_volatility)
        methods.append(ProbabilityMethod(
            id="M2", name=m2_name, status="ok", probability=p_end_above(spot, target, vol, years, r),
            touch_probability=p_touch(spot, target, vol, years, r), measures=m2_measures,
            detail=T(f"historical volatility {vol * 100:.0f}% a year ({market.volatility_window}); {r_note.en}",
                     f"历史年化波动率 {vol * 100:.0f}%（{market.volatility_window}）；{r_note.zh}"),
            inputs={"spot": f"{spot:g}", "target": f"{target:g}", "years": f"{years:.2f}", "historical_volatility": f"{vol:.4f}", "rate": f"{r:.4f}"},
            limitations=[T("Constant volatility and no jumps; past volatility is not a forecast.", "假设波动率不变、没有跳空；过去的波动率不是预测。")]))
    else:
        methods.append(ProbabilityMethod(id="M2", name=m2_name, status="not_computable", measures=m2_measures,
                                         detail=T("no price history in this run", "本次没有取得价格历史")))

    # ---------------------------------------------------------------- M3 fundamental base rate
    is_ps = claim.valuation_method is ValuationMethod.PRICE_TO_SALES
    metric = T("revenue", "营收") if is_ps else T("net income", "净利润")
    m3_name = T("Base rate (similar companies)", "历史基准率（同规模公司）")
    m3_measures = T(f"how many US companies of similar size grew {metric.en} as fast as the claim needs",
                    f"规模相近的美国上市公司里，{metric.zh}增速达到观点要求的比例")
    if base_rate is not None and base_rate.rate is not None and base_rate.companies > 0:
        methods.append(ProbabilityMethod(
            id="M3", name=m3_name, status="ok", probability=base_rate.rate, measures=m3_measures,
            detail=T(f"{base_rate.achieved} of {base_rate.companies} companies with {metric.en} between {base_rate.size_low:,.0f} and "
                     f"{base_rate.size_high:,.0f} USD in {base_rate.start_year} grew at least {float(base_rate.required_cagr) * 100:.1f}% a year "
                     f"to {base_rate.end_year}; the median grew {float(base_rate.median_cagr or 0) * 100:.1f}% a year",
                     f"{base_rate.start_year} 年{metric.zh}在 {base_rate.size_low / Decimal('1e8'):,.1f} 亿至 {base_rate.size_high / Decimal('1e8'):,.1f} 亿美元之间的 "
                     f"{base_rate.companies} 家公司中，有 {base_rate.achieved} 家到 {base_rate.end_year} 年保持了每年至少 "
                     f"{float(base_rate.required_cagr) * 100:.1f}% 的增长；中位数为每年 {float(base_rate.median_cagr or 0) * 100:.1f}%"),
            inputs={"companies": str(base_rate.companies), "achieved": str(base_rate.achieved), "required_cagr": format(base_rate.required_cagr, "f")},
            limitations=[T("Probability of the business condition, not of the price; the valuation assumption still has to hold.",
                           "这是业绩条件的概率，不是股价的概率；估值假设仍需成立。"),
                         T(base_rate.note, "只统计两年都还在申报的公司，被收购或退市的公司不在其中，所以比例偏乐观。")]
            + ([T("The claim is about touching the price at some point; a spike does not need the business to grow into it, so this is context only.",
                  "观点问的是期间触及；冲高不需要业绩先兑现，所以这一项只作参考，不计入区间。")] if touch else [])))
    else:
        methods.append(ProbabilityMethod(id="M3", name=m3_name, status="not_computable", measures=m3_measures,
                                         detail=T("no base rate in this run (needs SEC frames and a positive reported base)",
                                                  "本次没有基准率（需要 SEC frames 数据和为正的已披露基数）")))

    # ---------------------------------------------------------------- M4 own price history
    m4_name = T("This stock's own history", "这只股票自己的历史")
    m4_measures = T("share of past windows of the same length in which this stock rose at least as much", "过去相同长度的时间段里，这只股票涨幅达到要求的比例")
    if price_rate is not None and price_rate.windows > 0:
        indep = independent_windows(price_rate)
        enough = indep >= MIN_INDEPENDENT_WINDOWS
        lim = [T("Past returns of one stock, and a stock is usually asked about because it already rose (selection bias).",
                 "只是一只股票的过去表现；而且人们往往是因为它已经涨过才问它（选择偏差）。")]
        if not enough:
            lim.append(T(f"The windows overlap: the history holds only about {indep} non-overlapping {price_rate.window_months}-month periods, "
                         f"so this is shown as context and not used in the range.",
                         f"这些时间段大量重叠：历史里只有约 {indep} 段互不重叠的 {price_rate.window_months} 个月，所以只作参考，不计入区间。"))
        methods.append(ProbabilityMethod(
            id="M4", name=m4_name, status="ok", probability=price_rate.rate, measures=m4_measures,
            touch_probability=(price_rate.touch_hits / price_rate.windows) if price_rate.touch_hits is not None else None,
            detail=T(f"{price_rate.hits} of {price_rate.windows} monthly-start windows of {price_rate.window_months} months since {price_rate.history_start} "
                     f"rose at least {float(price_rate.required_return) * 100:.0f}%; best {float(price_rate.best_return or 0) * 100:.0f}%",
                     f"自 {price_rate.history_start} 以来，{price_rate.windows} 个 {price_rate.window_months} 个月的时间段中有 {price_rate.hits} 个涨幅达到 "
                     f"{float(price_rate.required_return) * 100:.0f}%；最好的一段涨了 {float(price_rate.best_return or 0) * 100:.0f}%"),
            inputs={"windows": str(price_rate.windows), "hits": str(price_rate.hits)}, limitations=lim))
    else:
        methods.append(ProbabilityMethod(id="M4", name=m4_name, status="not_computable", measures=m4_measures,
                                         detail=T("not enough price history for a window this long", "价格历史不够长")))

    # ---------------------------------------------------------------- synthesis
    def value(m: ProbabilityMethod) -> Optional[float]:
        return m.touch_probability if touch else m.probability

    usable = [m for m in methods if m.status == "ok" and value(m) is not None
              and not (m.id == "M4" and independent_windows(price_rate) < MIN_INDEPENDENT_WINDOWS)
              and not (touch and m.id == "M3")]
    if usable:
        values = sorted(value(m) for m in usable)
        low, high = values[0], values[-1]
        mid = values[len(values) // 2] if len(values) % 2 else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2
        tier = next(t for t in TIERS if mid < t[0])
        lo_m = min(usable, key=value)
        hi_m = max(usable, key=value)
        if len(usable) == 1:
            agreement = T(f"Only one method could be computed ({hi_m.name.en}); treat the result with care.",
                          f"只有一种方法算出了结果（{hi_m.name.zh}），请谨慎看待。")
        elif high < 0.10:
            agreement = T(f"All {len(usable)} methods put it below 10% ({_pct(low)} to {_pct(high)}); the lowest is {lo_m.name.en}, "
                          f"which measures {lo_m.measures.en}.",
                          f"{len(usable)} 种方法都低于 10%（{_pct(low)} 到 {_pct(high)}）；最低的是{lo_m.name.zh}，它衡量的是{lo_m.measures.zh}。")
        elif low > 0 and high / low <= 3:
            agreement = T(f"The {len(usable)} methods agree within a factor of 3: {_pct(low)} to {_pct(high)}.",
                          f"{len(usable)} 种方法相差不到 3 倍：{_pct(low)} 到 {_pct(high)}。")
        else:
            agreement = T(f"The methods disagree: {hi_m.name.en} gives {_pct(high)}, {lo_m.name.en} gives {_pct(low)}. "
                          f"They measure different things: {hi_m.measures.en}; versus {lo_m.measures.en}.",
                          f"各方法差别较大：{hi_m.name.zh}给出 {_pct(high)}，{lo_m.name.zh}给出 {_pct(low)}。"
                          f"它们衡量的东西不同：前者是{hi_m.measures.zh}；后者是{lo_m.measures.zh}。")
        tier_key, tier_label = tier[1], T(tier[2], tier[3])
    else:
        low = high = None
        tier_key, tier_label = "unknown", T("Not enough data", "数据不足")
        agreement = T("No method could be computed in this run.", "本次没有任何方法能算出结果。")

    # ---------------------------------------------------------------- ladder
    ladder: list[LadderRow] = []
    if spot:
        levels = [(0.5, T("half of today", "今天的一半")), (1.0, T("today's price", "今天的价格")), (1.5, T("+50%", "+50%")),
                  (2.0, T("double", "翻倍"))]
        rows = [(spot * k, label) for k, label in levels]
        rows.append((target, T("the claim's target", "观点目标价")))
        hv = float(market.annualized_volatility) if market and market.annualized_volatility else None
        for level, label in sorted(rows, key=lambda x: x[0]):
            o_p = o_t = None
            if options is not None:
                iv, _ = iv_at(options.calls, level)
                if iv:
                    o_p, o_t = p_end_above(spot, level, iv, years, r), p_touch(spot, level, iv, years, r)
            m_p = p_end_above(spot, level, hv, years, r) if hv else None
            m_t = p_touch(spot, level, hv, years, r) if hv else None
            ladder.append(LadderRow(level=Decimal(str(round(level, 2))), label=label, options_p=o_p, model_p=m_p, options_touch=o_t, model_touch=m_t))

    return OracleSummary(target_price=claim.target_price, target_date=target_date, low=low, high=high, tier=tier_key, tier_label=tier_label,
                         methods=methods, ladder=ladder, agreement=agreement, risk_free_rate=risk_free, condition="touch" if touch else "end",
                         spot=spot, base_volatility=float(market.annualized_volatility) if market and market.annualized_volatility else None,
                         implied_volatility=options.target_iv if options is not None else None)


# ---------------------------------------------------------------- event move from the term structure

LONG_GAP_DAYS = 120


def _years(d: date, today: date) -> float:
    return max((d - today).days, 0) / 365.25


def event_move(term: list[TermPoint], event_date: date, today: date) -> EventMove:
    """Extra one-standard-deviation move the options price around ``event_date``.

    Total implied variance of an expiry is iv^2 * t. The variance added between the last expiry
    before the event and the first one after it, minus what an ordinary stretch of that length
    costs (the forward volatility of a neighbouring interval), is attributed to the event.
    """
    pts = sorted((p for p in term if p.expiry > today and p.atm_iv), key=lambda p: p.expiry)
    after = next((i for i, p in enumerate(pts) if p.expiry >= event_date), None)
    if event_date <= today or after is None or len(pts) < 2:
        why = T("The event date must fall between today and the last listed option expiry, and at least two expiries are needed.",
                "事件日期需要在今天和最远的期权到期日之间，并且至少需要两个到期日。")
        return EventMove(event_date=event_date, status="not_computable", note=why)

    def var(i: int) -> float:
        return pts[i].atm_iv ** 2 * _years(pts[i].expiry, today) if i >= 0 else 0.0

    def t(i: int) -> float:
        return _years(pts[i].expiry, today) if i >= 0 else 0.0

    before = after - 1
    # ordinary volatility: the forward volatility of the interval just before, else just after
    if before >= 1:
        base = (var(before) - var(before - 1)) / max(t(before) - t(before - 1), 1e-9)
    elif after + 1 < len(pts):
        base = (var(after + 1) - var(after)) / max(t(after + 1) - t(after), 1e-9)
    else:
        base = pts[max(before, 0)].atm_iv ** 2
    base = max(base, 0.0)
    extra = var(after) - var(before) - base * (t(after) - t(before))
    gap = (pts[after].expiry - (pts[before].expiry if before >= 0 else today)).days
    b_exp = pts[before].expiry if before >= 0 else None
    notes = []
    if gap > LONG_GAP_DAYS:
        notes.append(T(f"The expiries around the event are {gap} days apart, so other news in that stretch is mixed in.",
                       f"事件前后两个到期日相隔 {gap} 天，期间的其他消息也混在里面。"))
    if extra <= 0:
        notes.insert(0, T("The option market does not charge extra volatility around this date.", "期权市场没有为这个日期额外加价波动。"))
        return EventMove(event_date=event_date, before_expiry=b_exp, after_expiry=pts[after].expiry, status="not_priced", gap_days=gap,
                         note=T(" ".join(n.en for n in notes), "".join(n.zh for n in notes)))
    return EventMove(event_date=event_date, before_expiry=b_exp, after_expiry=pts[after].expiry, status="ok", move=math.sqrt(extra), gap_days=gap,
                     note=T(" ".join(n.en for n in notes), "".join(n.zh for n in notes)) if notes else None)


# ---------------------------------------------------------------- user scenario

def scenario(*, spot: float, target: float, vol: float, rate: float, years: float, event_years: float,
             up: float, down: float, p_success: float, touch: bool, market_p: Optional[float] = None) -> ScenarioResult:
    """Probability with one dated jump: +up on success, +down (negative) on failure, ordinary volatility otherwise.

    End: exact for lognormal diffusion plus one jump (the jump's timing does not matter for the final price).
    Touch: P(touch before the event) + P(no touch before) x P(touch after, starting from today's price moved by the jump);
    an approximation, because the price on the event date is taken as today's.
    """
    event_years = min(max(event_years, 0.0), years)
    rest = years - event_years

    def after(start: float) -> float:
        if start >= target and touch:
            return 1.0
        if touch:
            return p_touch(start, target, vol, rest, rate) if rest > 0 else 0.0
        return p_end_above(start, target, vol, years, rate)

    pre = p_touch(spot, target, vol, event_years, rate) if touch and event_years > 0 else 0.0
    ps = pre + (1 - pre) * after(spot * (1 + up))
    pf = pre + (1 - pre) * after(spot * (1 + down))
    p = max(0.0, min(1.0, p_success))
    slope = ps - pf
    prob = pf + p * slope
    break_even = None
    if pf >= 0.5:
        break_even = 0.0
    elif slope > 0 and ps >= 0.5:
        break_even = (0.5 - pf) / slope
    implied = None
    if market_p is not None and slope > 1e-12 and pf <= market_p <= ps:
        implied = (market_p - pf) / slope
    return ScenarioResult(probability=prob, p_if_success=ps, p_if_failure=pf, break_even=break_even, market_implied=implied)
