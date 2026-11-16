"""Plain words for a reader who does not follow stocks (the simple view, v0.16.0).

Everything here is written by rules from numbers the case already holds: the probability range, the four method
estimates, the two base rates and the growth check. No model writes a sentence, so each sentence can be traced to the
number beside it in the seven report pages. Every text is a pair (English, Chinese).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .models import CaseResult, ClaimDraft

Pair = tuple[str, str]

# five words for where the range sits; the range itself is always shown beside the word
TIERS: dict[str, Pair] = {
    "almost_no": ("Very unlikely", "几乎不可能"),
    "unlikely": ("Unlikely", "不太可能"),
    "uncertain": ("Possible, but uncertain", "有可能，但说不准"),
    "likely": ("Likely", "比较可能"),
    "very_likely": ("Very likely", "很可能"),
    "unknown": ("Could not be worked out", "算不出来"),
}


def pct(p: float) -> str:
    """0.403 -> '40%'; small and large values keep one decimal so 0.4% is not shown as 0%."""
    v = p * 100
    return f"{v:.1f}%" if (0 < v < 1 or 99 < v < 100) else f"{round(v)}%"


def tier(low: Optional[float], high: Optional[float]) -> str:
    """The word for a range. A range that lies on both sides of even odds, or is wide, is 'uncertain'."""
    if low is None or high is None:
        return "unknown"
    if high <= 0.10:
        return "almost_no"
    if low >= 0.85:
        return "very_likely"
    if high < 0.40:
        return "unlikely"
    if low > 0.60:
        return "likely"
    return "uncertain"


@dataclass(frozen=True)
class Headline:
    tier: str
    word: Pair
    range_text: Pair          # "roughly 40% to 79%"
    low: Optional[float]
    high: Optional[float]


def headline(result: CaseResult) -> Headline:
    o = result.oracle
    low, high = (o.low, o.high) if o is not None else (None, None)
    key = tier(low, high)
    if key == "unknown":
        text = ("there is not enough data for an estimate", "数据不够，给不出估计")
    elif abs(high - low) < 0.005:
        text = (f"about {pct(low)}", f"大约 {pct(low)}")
    else:
        text = (f"roughly {pct(low)} to {pct(high)}", f"大约 {pct(low)} 到 {pct(high)}")
    return Headline(key, TIERS[key], text, low, high)


def claim_sentence(result: CaseResult) -> Pair:
    """The claim as the case understood it, in one line."""
    c = result.confirmed_claim.values
    o = result.oracle
    day = o.target_date if o is not None else None
    price = format(c.target_price.normalize(), "f")
    touch = (o.condition if o is not None else c.price_condition) == "touch"
    when_en = f"by {day}" if day else f"within {format(c.horizon_years.normalize(), 'f')} years"
    when_zh = f"{day} 之前" if day else f"{format(c.horizon_years.normalize(), 'f')} 年内"
    if touch:
        return (f"{c.ticker} reaches {price} {c.currency} {when_en}", f"{c.ticker} 在 {when_zh}涨到 {price} {c.currency}")
    return (f"{c.ticker} closes at {price} {c.currency} or higher on {day or 'the last day'}",
            f"{c.ticker} 在 {day or '到期那天'} 收盘不低于 {price} {c.currency}")


@dataclass(frozen=True)
class Reason:
    sign: str      # "+" supports the claim, "-" speaks against it, "0" neither
    text: Pair
    page: str      # the report page that holds the number


def _sign(p: float) -> str:
    return "+" if p >= 0.5 else "-" if p <= 0.2 else "0"


def _months(n: int) -> Pair:
    if n % 12 == 0:
        y = n // 12
        return (f"{y}-year", f"{y} 年")
    return (f"{n}-month", f"{n} 个月")


def reasons(result: CaseResult) -> list[Reason]:
    """Up to four reasons, each one number said in everyday words, in a fixed order."""
    out: list[Reason] = []
    o = result.oracle
    touch = o is not None and o.condition == "touch"
    claim = result.confirmed_claim.values if result.confirmed_claim is not None else None
    # 1. the stock's own history
    pr = result.price_base_rate
    if pr is not None and pr.windows:
        hits = pr.touch_hits if (touch and pr.touch_hits is not None) else pr.hits
        rate = hits / pr.windows
        need = pct(float(pr.required_return))
        en_m, zh_m = _months(pr.window_months)
        verb_en = "reached this gain at some point" if (touch and pr.touch_hits is not None) else "ended with this gain"
        verb_zh = "中途达到过这个涨幅" if (touch and pr.touch_hits is not None) else "期末达到了这个涨幅"
        out.append(Reason(_sign(rate), (
            f"The price needs to rise {need}. Of the {pr.windows} past {en_m} periods of this stock since {pr.history_start.year}, {hits} {verb_en} ({pct(rate)}).",
            f"股价需要上涨 {need}。从 {pr.history_start.year} 年起这只股票的 {pr.windows} 个 {zh_m}时间段里，有 {hits} 个{verb_zh}（{pct(rate)}）。"), "methods"))
    # 2. what option prices imply
    m1 = next((m for m in (o.methods if o is not None else []) if m.id == "M1" and m.status == "ok"), None)
    if m1 is not None:
        p = m1.touch_probability if (touch and m1.touch_probability is not None) else m1.probability
        if p is not None:
            out.append(Reason(_sign(p), (
                f"Option prices, where traders put money on future prices, are in line with a chance of about {pct(p)}.",
                f"期权市场（交易者用真金白银押未来价格的地方）的价格，相当于认为这件事有约 {pct(p)} 的可能。"), "methods"))
    # 3. how often companies of this size grew that fast
    br = result.base_rate
    metric = ("net income", "净利润") if (claim is not None and claim.valuation_method.metric_name != "annual_revenue") else ("revenue", "收入")
    if br is not None and br.companies and br.rate is not None:
        need = pct(float(br.required_cagr))
        out.append(Reason(_sign(br.rate), (
            f"For the price to hold, the company's {metric[0]} would need to grow about {need} a year. Of {br.companies} companies of similar size, "
            f"{br.achieved} grew that fast from {br.start_year} to {br.end_year} ({pct(br.rate)}).",
            f"要撑住这个价格，公司的{metric[1]}需要每年增长约 {need}。{br.companies} 家规模相近的公司里，{br.start_year} 到 {br.end_year} 年"
            f"有 {br.achieved} 家做到（{pct(br.rate)}）。"), "fund"))
    # 4. the company's own growth so far
    e1 = next((i for i in result.evidence_items if i.id == "E1"), None)
    if e1 is not None and "required_cagr_from_reported" in e1.measured and "reported_cagr" in e1.measured:
        need, got = Decimal(e1.measured["required_cagr_from_reported"]), Decimal(e1.measured["reported_cagr"])
        sign = "+" if got >= need else "-"
        cmp_en = "at or above" if got >= need else "below"
        cmp_zh = "不低于" if got >= need else "低于"
        out.append(Reason(sign, (
            f"The company's reported {metric[0]} has grown about {pct(float(got))} a year, {cmp_en} the {pct(float(need))} a year the claim needs.",
            f"公司已披露的{metric[1]}近几年每年增长约 {pct(float(got))}，{cmp_zh}这个说法需要的每年 {pct(float(need))}。"), "fund"))
    return out[:4]


def other_condition(result: CaseResult) -> Optional[Pair]:
    """For a 'reaches at any time' claim: the chance of still being above the target on the last day."""
    o = result.oracle
    if o is None or o.condition != "touch":
        return None
    ends = [m.probability for m in o.methods if m.status == "ok" and m.probability is not None]
    if not ends:
        return None
    lo, hi = min(ends), max(ends)
    rng = pct(lo) if abs(hi - lo) < 0.005 else f"{pct(lo)}–{pct(hi)}"
    return (f"The range above counts any day the price gets there. The chance that it is still at or above the target on {o.target_date} is lower: about {rng}.",
            f"上面的范围只要中途有一天到过就算。如果问的是 {o.target_date} 那天收盘还在目标价以上，可能性更低：约 {rng}。")


def why_a_range(result: CaseResult) -> Optional[Pair]:
    o = result.oracle
    if o is None or o.low is None:
        return None
    n = sum(1 for m in o.methods if m.status == "ok")
    if n < 2:
        return ("Only one method could be computed for this claim, so the estimate rests on that one method.",
                "这个说法只有一种算法能算出结果，所以估计只依据这一种算法。")
    return (f"Why a range? {n} different methods were used and they do not agree, so the answer is a range and not one number.",
            f"为什么是一个范围？我们用了 {n} 种不同的算法，它们的结果不一样，所以给范围而不是一个数。")


def assumptions(result: CaseResult) -> list[Pair]:
    """What the simple view decided for the reader: every value of the confirmed claim that is a default assumption."""
    cc = result.confirmed_claim
    if cc is None:
        return []
    c, prov = cc.values, cc.value_provenance
    out: list[Pair] = []
    is_default = lambda name: str(prov.get(name, "")).startswith("default_assumption:")  # noqa: E731
    if is_default("price_condition") or "price_condition" not in prov:
        if c.price_condition == "touch":
            out.append(("The claim counts as true if the price reaches the target on any day before the deadline.", "只要截止日前有一天达到目标价，就算这个说法成立。"))
        else:
            out.append(("The claim counts as true only if the price is at or above the target on the last day.", "只有到期那天收盘不低于目标价，才算这个说法成立。"))
    kind = ("price-to-earnings", "市盈率") if c.valuation_method.metric_name != "annual_revenue" else ("price-to-sales", "市销率")
    if is_default("valuation_multiple") or is_default("valuation_method"):
        out.append((f"The company is valued then as it is today: {kind[0]} of {format(c.valuation_multiple.normalize(), 'f')}.",
                    f"假设到时候市场给公司的估值和今天一样：{kind[1]} {format(c.valuation_multiple.normalize(), 'f')} 倍。"))
    if c.target_shares_derived or is_default("share_change_rate"):
        rate = c.share_change_rate if c.share_change_rate is not None else Decimal("0")
        out.append((f"The number of shares keeps changing at its recent pace ({pct(float(rate))} a year).",
                    f"假设公司的股票数量按最近的速度继续变化（每年 {pct(float(rate))}）。"))
    if is_default("horizon_years"):
        out.append((f"No deadline was found in the sentence, so {format(c.horizon_years.normalize(), 'f')} years is used.",
                    f"这句话里没有找到期限，所以按 {format(c.horizon_years.normalize(), 'f')} 年计算。"))
    return out


@dataclass(frozen=True)
class Restated:
    """The four things the reader is asked to confirm, as the form holds them."""

    ticker: str
    company: Optional[str]
    target: str
    currency: str
    deadline: Optional[str]
    touch: bool
    reference: Optional[str]
    needed: Optional[float]     # the rise still needed, 0.78 = 78 %

    @property
    def complete(self) -> bool:
        return bool(self.ticker and self.target and self.deadline)


def restate(draft: ClaimDraft, *, company: Optional[str], deadline: Optional[str]) -> Restated:
    needed = None
    try:
        if draft.target_price and draft.reference_price and Decimal(str(draft.reference_price)) > 0:
            needed = float(Decimal(str(draft.target_price)) / Decimal(str(draft.reference_price)) - 1)
    except Exception:
        needed = None
    return Restated(ticker=(draft.ticker or "").strip().upper(), company=company, target=str(draft.target_price or "").strip(),
                    currency=(draft.currency or "USD").strip() or "USD", deadline=deadline, touch=(draft.price_condition or "end") == "touch",
                    reference=str(draft.reference_price or "").strip() or None, needed=needed)
