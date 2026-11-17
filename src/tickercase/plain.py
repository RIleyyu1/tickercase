"""Plain words for a reader who does not follow stocks (the simple view, v0.16).

Everything here is written by rules from numbers the case already holds: the probability range, the method estimates,
the two base rates and the growth check. No model writes a sentence, so each sentence can be traced to the number
beside it in the seven report pages. Every text is a pair (English, Chinese).

The simple view must never say something the console does not: the word comes from ``tiers``, probabilities are written
by ``fmt``, and a method counts toward a range under the same rule as on the console (``counted``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from . import fmt
from .models import CaseResult, ClaimDraft
from .oracle import MIN_INDEPENDENT_WINDOWS, independent_windows
from .tiers import TIERS, tier

Pair = tuple[str, str]
__all__ = ["TIERS", "tier", "pct", "rate", "headline", "claim_sentence", "reasons", "other_condition", "why_a_range", "assumptions", "restate"]


def pct(p: float) -> str:
    """A probability or a share, as every page writes it ('40%', '0.6%', '<0.1%', '>99.9%')."""
    return fmt.prob(p, 0)


def rate(x) -> str:
    """A growth rate or a price change, which may be negative or above 100 % ('17%', '101%', '-16%')."""
    return fmt.pct(float(x), 0)


def counted(result: CaseResult, m) -> bool:
    """Whether a method is part of the headline range: the list the case recorded, or the older rule for cases stored
    before v0.17.0."""
    if result.oracle.counted:
        return m.id in result.oracle.counted
    if m.id == "M4" and independent_windows(result.price_base_rate) < MIN_INDEPENDENT_WINDOWS:
        return False
    if m.id == "M3" and result.oracle.condition == "touch":
        return False
    return True


def _value(result: CaseResult, m) -> Optional[float]:
    return m.touch_probability if result.oracle.condition == "touch" else m.probability


def counted_methods(result: CaseResult) -> list:
    o = result.oracle
    if o is None:
        return []
    return [m for m in o.methods if m.status == "ok" and _value(result, m) is not None and counted(result, m)]


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
    else:
        a, b = fmt.prob_ends(low, high)
        text = (f"about {a}", f"大约 {a}") if a == b else (f"roughly {a} to {b}", f"大约 {a} 到 {b}")
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
    sign: str               # "+" supports the claim, "-" speaks against it, "0" neither
    text: Pair
    page: str               # the report page that holds the number
    note: Optional[Pair] = None   # the limit of this number, which the console states and the simple view must keep
    counted: bool = False         # part of the main number (True) or shown for context (False)


def _sign(p: float) -> str:
    return "+" if p >= 0.5 else "-" if p <= 0.2 else "0"


def _months(n: int) -> Pair:
    if n % 12 == 0:
        y = n // 12
        return (f"{y}-year", f"{y} 年")
    return (f"{n}-month", f"{n} 个月")


def reasons(result: CaseResult) -> list[Reason]:
    """Up to five reasons, each one number said in everyday words: first the methods the main number rests on, then
    the numbers shown for context. ``counted`` says which group a reason belongs to, by the console's rule."""
    out: list[Reason] = []
    o = result.oracle
    touch = o is not None and o.condition == "touch"
    claim = result.confirmed_claim.values if result.confirmed_claim is not None else None
    by_id = {m.id: m for m in (o.methods if o is not None else []) if m.status == "ok"}
    is_counted = lambda mid: mid in by_id and counted(result, by_id[mid])  # noqa: E731

    def chance(mid: str) -> Optional[float]:
        m = by_id.get(mid)
        if m is None:
            return None
        return m.touch_probability if (touch and m.touch_probability is not None) else m.probability

    # 1. what option prices imply
    p = chance("M1")
    if p is not None:
        out.append(Reason(_sign(p), (
            f"Worked out from option prices, where traders put money on future prices, the chance is about {pct(p)}.",
            f"按期权市场（交易者用真金白银押未来价格的地方）的价格推算，这件事约有 {pct(p)} 的可能。"), "methods",
            ("This number includes what traders charge for risk, so it is not the true chance; when the date or the price lies beyond the options on sale, it is extended from the nearest ones.",
             "这个数含有交易者对风险的要价，不等于真实的可能性；期限或目标价超出现有期权时，是从最接近的期权推算的。"), is_counted("M1")))
    # 2. the stock's own swings
    p = chance("M2")
    if p is not None:
        out.append(Reason(_sign(p), (
            f"Worked out from how much this stock's price usually swings, without looking at any news, the chance is about {pct(p)}.",
            f"按这只股票平时的波动幅度推算（不看任何消息），这件事约有 {pct(p)} 的可能。"), "methods",
            ("It assumes the price swings as much as it did in the past year and leaves out the long-run tendency of stocks to rise, so it may be low.",
             "它假设以后的波动和过去一年差不多，而且不含股票长期上涨的倾向，所以可能偏低。"), is_counted("M2")))
    # 3. the stock's own history
    pr = result.price_base_rate
    reached = pr is not None and float(pr.required_return) <= 0
    if reached:
        above = rate(-float(pr.required_return) / (1 + float(pr.required_return))) if float(pr.required_return) > -1 else "—"
        out.append(Reason("+", (f"The price today is already {above} above the target, so the claim holds unless the price falls back.",
                                f"现在的股价已经比目标价高 {above}，只要不跌回去，这个说法就成立。"), "answer", None, True))
    elif pr is not None and pr.windows:
        use_touch = touch and pr.touch_hits is not None
        hits = pr.touch_hits if use_touch else pr.hits
        share = hits / pr.windows
        need = rate(pr.required_return)
        en_m, zh_m = _months(pr.window_months)
        verb_en = "reached at least this gain at a month-end" if use_touch else "ended with at least this gain"
        verb_zh = "在某个月末达到过这个涨幅" if use_touch else "期末达到了这个涨幅"
        indep = independent_windows(pr)
        own = is_counted("M4")
        note = (f"This is what would happen if the stock repeated its own past. A stock is usually asked about because it already rose, so this number is optimistic. "
                f"The periods start a month apart and overlap: they amount to about {indep} separate {en_m} periods.",
                f"这是“如果它重复自己过去的表现”的结果。一只股票被拿来讨论，往往正因为它已经涨了很多，所以这个数偏乐观。"
                f"这些时间段按月起算、互相重叠，相当于约 {indep} 段独立的 {zh_m}。")
        out.append(Reason(_sign(share), (
            f"The price needs to rise {need}. Of the {pr.windows} past {en_m} periods of this stock since {pr.history_start.year}, {hits} {verb_en} ({pct(share)}).",
            f"股价需要上涨 {need}。从 {pr.history_start.year} 年起这只股票的 {pr.windows} 个 {zh_m}时间段里，有 {hits} 个{verb_zh}（{pct(share)}）。"), "methods", note, own))
    # 4 and 5. the business: how fast it would have to grow
    br = result.base_rate
    metric = ("profit", "利润") if (claim is not None and claim.valuation_method.metric_name != "annual_revenue") else ("revenue", "收入")
    e1 = next((i for i in result.evidence_items if i.id == "E1"), None)
    need_growth = None
    if br is not None:
        need_growth = Decimal(str(br.required_cagr))
    elif e1 is not None and "required_cagr_from_reported" in e1.measured:
        need_growth = Decimal(e1.measured["required_cagr_from_reported"])
    if need_growth is not None and need_growth <= 0:
        out.append(Reason("+", (f"If the company is valued as it is today, its {metric[0]} does not need to grow for the price to be at this level.",
                                f"如果市场对公司的估值和今天一样，公司的{metric[1]}不用再增长，股价就能在这个水平。"), "fund", None, False))
        return out[:5]
    if br is not None and br.companies and br.rate is not None:
        wide = ((" The size band was widened to a quarter to four times the company's size to find enough companies.", "为了凑够公司，规模范围已放宽到 0.25–4 倍。")
                if br.band_widened else ("", ""))
        out.append(Reason(_sign(br.rate), (
            f"For the price to make sense at this level, the company's {metric[0]} would have to grow about {rate(br.required_cagr)} a year (if it is valued as it is today). "
            f"Of {br.companies} companies of similar size, {br.achieved} grew that fast from {br.start_year} to {br.end_year} ({pct(br.rate)}).",
            f"股价要到这个水平才说得过去，公司的{metric[1]}每年得增长约 {rate(br.required_cagr)}（假设市场给它的估值不变）。"
            f"{br.companies} 家规模相近的公司里，{br.start_year} 到 {br.end_year} 年有 {br.achieved} 家做到（{pct(br.rate)}）。"), "fund",
            ("This is about the business, not the price. Only companies that still report are counted, which makes this share look better than it was." + wide[0],
             "这说的是公司的业绩，不是股价。只统计了至今仍在申报的公司，所以这个比例偏乐观。" + wide[1]), is_counted("M3")))
    if e1 is not None and "required_cagr_from_reported" in e1.measured and "reported_cagr" in e1.measured:
        need, got = Decimal(e1.measured["required_cagr_from_reported"]), Decimal(e1.measured["reported_cagr"])
        ok = got >= need
        out.append(Reason("+" if ok else "-", (
            f"The {metric[0]} the company has published grew about {rate(got)} a year in recent years, {'at or above' if ok else 'below'} the {rate(need)} a year the claim needs.",
            f"公司公布的{metric[1]}近几年每年增长约 {rate(got)}，{'不低于' if ok else '低于'}这个说法需要的每年 {rate(need)}。"), "fund",
            ("Growth in the past does not promise the same growth later.", "过去增长快，不代表以后还能这么快。"), False))
    return out[:5]


def other_condition(result: CaseResult) -> Optional[Pair]:
    """For a 'reaches at any time' claim: the chance of still being at or above the target on the last day.

    The two market-based methods only, the same pair the console shows in its status bar.
    """
    o = result.oracle
    if o is None or o.condition != "touch":
        return None
    ends = [m.probability for m in o.methods if m.id in ("M1", "M2") and m.status == "ok" and m.probability is not None]
    if not ends:
        return None
    rng = fmt.prob_range(min(ends), max(ends))
    return (f"The range above counts any day the price gets there. Still being at or above the target on {o.target_date} is less likely: about {rng}.",
            f"上面的范围只要中途有一天到过就算。如果要求 {o.target_date} 那天收盘还在目标价以上，可能性更低：约 {rng}。")


def why_a_range(result: CaseResult) -> Optional[Pair]:
    """What the main number is made of. Since v0.17.0 it is the two market-based methods; a case without them says so."""
    o = result.oracle
    if o is None or o.low is None:
        return None
    used = counted_methods(result)
    n = len(used)
    ids = {m.id for m in used}
    if ids and ids <= {"M1", "M2"}:
        if n == 2:
            return ("The main number comes from two methods that ask the same question of different data: the option prices of today, and how much this stock usually swings. "
                    "It is not a margin of error, and the true chance can lie outside it.",
                    "主数字来自两种算法，它们用不同的数据回答同一个问题：今天的期权价格，和这只股票平时的波动幅度。它不是误差范围，真实的可能性也可能落在范围之外。")
        which = (("option prices", "期权价格") if "M1" in ids else ("how much this stock usually swings", "这只股票平时的波动幅度"))
        return (f"The main number comes from one method only ({which[0]}); the other market-based method could not be computed this time.",
                f"主数字这次只来自一种算法（{which[1]}），另一种市场算法没有算出结果。")
    if n < 2:
        return ("Only one method could be computed for this claim, so the estimate rests on that one method.",
                "这个说法只有一种算法能算出结果，所以估计只依据这一种算法。")
    return (f"Neither market-based method could be computed, so the range runs from the lowest to the highest of {n} other methods. "
            "It shows how far they disagree; it is not a margin of error.",
            f"两种市场算法都没有算出结果，所以范围取自其余 {n} 种算法中最低和最高的结果。它表示算法之间的分歧，不是误差范围。")


def _share_pace(x) -> Pair:
    x = float(x)
    if abs(x) < 0.0005:
        return ("stays about the same", "基本不变")
    word = fmt.pct(x, 1, signed=True)
    return (f"keeps changing at its recent pace ({word} a year)", f"按最近的速度继续变化（每年 {word}）")


def assumptions(result: CaseResult) -> list[Pair]:
    """What the simple view decided for the reader: the default assumptions of the confirmed claim, in everyday words."""
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
    sales = c.valuation_method.metric_name == "annual_revenue"
    mult = format(c.valuation_multiple.normalize(), "f")
    if is_default("valuation_multiple") or is_default("valuation_method"):
        if sales:
            out.append((f"Investors value the company then as they do today: about {mult} dollars of market value for each dollar it sells in a year.",
                        f"假设到时候市场对这家公司的估值和今天一样：公司每年每卖出 1 美元，对应约 {mult} 美元的市值。"))
        else:
            out.append((f"Investors value the company then as they do today: about {mult} dollars of market value for each dollar of yearly profit.",
                        f"假设到时候市场对这家公司的估值和今天一样：公司每年每赚 1 美元，对应约 {mult} 美元的市值。"))
    if c.target_shares_derived or is_default("share_change_rate"):
        en, zh = _share_pace(c.share_change_rate if c.share_change_rate is not None else 0)
        out.append((f"The number of the company's shares {en}.", f"假设公司的股票数量{zh}。"))
    if is_default("horizon_years"):
        out.append((f"No deadline was found in the sentence, so {format(c.horizon_years.normalize(), 'f')} years is used.",
                    f"这句话里没有找到期限，所以按 {format(c.horizon_years.normalize(), 'f')} 年计算。"))
    if result.oracle is not None and result.oracle.low is not None:
        out.append(("Interest rates and how much the price swings stay as they are today.", "假设利率和股价波动的幅度保持今天的水平。"))
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
