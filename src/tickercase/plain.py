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
__all__ = ["TIERS", "tier", "pct", "rate", "headline", "claim_sentence", "reasons", "other_condition", "why_a_range", "calibration_line", "index_parts", "answer_chips",
           "assumptions", "restate"]


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
    spread_low: Optional[float] = None    # the lowest and highest of the methods in the index, for the faint band on the bar
    spread_high: Optional[float] = None


def headline(result: CaseResult) -> Headline:
    o = result.oracle
    low, high = (o.low, o.high) if o is not None else (None, None)
    key = tier(low, high)
    if key == "unknown":
        text = ("there is not enough data for an estimate", "数据不够，给不出估计")
    else:
        a, b = fmt.prob_ends(low, high)
        text = (f"about {a}", f"大约 {a}") if a == b else (f"roughly {a} to {b}", f"大约 {a} 到 {b}")
    return Headline(key, TIERS[key], text, low, high, o.spread_low if o is not None else None, o.spread_high if o is not None else None)


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
    weight: Optional[float] = None  # its share of the main number, 0.42 = 42 %
    title: Optional[Pair] = None    # the card of this reason in the simple view: two or three words,
    value: Optional[str] = None     # the one number of the reason, written large,
    short: Optional[Pair] = None    # and one short line; the full sentence and the note open under the card


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
    weight_of = lambda mid: (o.weights.get(mid) if (o is not None and is_counted(mid)) else None)  # noqa: E731

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
             "这个数含有交易者对风险的要价，不等于真实的可能性；期限或目标价超出现有期权时，是从最接近的期权推算的。"), is_counted("M1"), weight_of("M1"),
            title=("Options market", "期权市场"), value=pct(p), short=("what traders bet with real money", "交易者用真金白银押出来的看法")))
    # 2. the stock's own swings
    p = chance("M2")
    if p is not None:
        out.append(Reason(_sign(p), (
            f"Worked out from how much this stock's price usually swings, without looking at any news, the chance is about {pct(p)}.",
            f"按这只股票平时的波动幅度推算（不看任何消息），这件事约有 {pct(p)} 的可能。"), "methods",
            ("It leaves out the long-run tendency of stocks to rise. In the backtest it was low: by about 8 points on average, more at three years.",
             "它不含股票长期上涨的倾向。回测里它偏低：平均低约 8 个百分点，三年期更多。"), is_counted("M2"), weight_of("M2"),
            title=("Its usual swings", "平时的波动"), value=pct(p), short=("from how much its price usually moves", "只看它平时涨跌有多大")))
    # 3. the stock's own history
    pr = result.price_base_rate
    reached = pr is not None and float(pr.required_return) <= 0
    if reached:
        above = rate(-float(pr.required_return) / (1 + float(pr.required_return))) if float(pr.required_return) > -1 else "—"
        out.append(Reason("+", (f"The price today is already {above} above the target, so the claim holds unless the price falls back.",
                                f"现在的股价已经比目标价高 {above}，只要不跌回去，这个说法就成立。"), "answer", None, True,
                          title=("Already there", "已经到了"), value=above, short=("above the target today", "现在已经高于目标价")))
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
        note = (f"This is what would happen if the stock repeated its own past, so it gets under a third of the main number. In the backtest it was a little high (3 to 5 points); "
                f"no stock there had risen as much as some that are asked about. The periods overlap: about {indep} separate {en_m} periods.",
                f"这是“如果它重复自己过去的表现”的结果，所以占比不到三分之一。回测里它略偏高（3 到 5 个百分点），但回测里没有涨幅特别极端的股票。"
                f"这些时间段互相重叠，相当于约 {indep} 段独立的 {zh_m}。")
        out.append(Reason(_sign(share), (
            f"The price needs to rise {need}. Of the {pr.windows} past {en_m} periods of this stock since {pr.history_start.year}, {hits} {verb_en} ({pct(share)}).",
            f"股价需要上涨 {need}。从 {pr.history_start.year} 年起这只股票的 {pr.windows} 个 {zh_m}时间段里，有 {hits} 个{verb_zh}（{pct(share)}）。"), "methods", note, own, weight_of("M4"),
            title=("Its own past", "它自己的过去"), value=pct(share),
            short=(f"{hits} of {pr.windows} past {en_m} periods did it", f"过去 {pr.windows} 个 {zh_m}里有 {hits} 个做到")))
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
                                f"如果市场对公司的估值和今天一样，公司的{metric[1]}不用再增长，股价就能在这个水平。"), "fund", None, False,
                          title=("The business", "公司业绩"), value="0%", short=(f"more {metric[0]} growth is not needed", f"{metric[1]}不用再增长")))
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
             "这说的是公司的业绩，不是股价。只统计了至今仍在申报的公司，所以这个比例偏乐观。" + wide[1]), is_counted("M3"), weight_of("M3"),
            title=("Similar companies", "规模相近的公司"), value=pct(br.rate),
            short=(f"{br.achieved} of {br.companies} grew fast enough", f"{br.companies} 家里有 {br.achieved} 家增长够快")))
    if e1 is not None and "required_cagr_from_reported" in e1.measured and "reported_cagr" in e1.measured:
        need, got = Decimal(e1.measured["required_cagr_from_reported"]), Decimal(e1.measured["reported_cagr"])
        ok = got >= need
        out.append(Reason("+" if ok else "-", (
            f"The {metric[0]} the company has published grew about {rate(got)} a year in recent years, {'at or above' if ok else 'below'} the {rate(need)} a year the claim needs.",
            f"公司公布的{metric[1]}近几年每年增长约 {rate(got)}，{'不低于' if ok else '低于'}这个说法需要的每年 {rate(need)}。"), "fund",
            ("Growth in the past does not promise the same growth later.", "过去增长快，不代表以后还能这么快。"), False,
            title=(f"Its {metric[0]} growth", f"公司的{metric[1]}增长"), value=rate(got),
            short=(f"a year; the claim needs {rate(need)}", f"每年；这个说法需要 {rate(need)}")))
    return out[:5]


def _last_day_range(result: CaseResult) -> Optional[str]:
    o = result.oracle
    if o is None or o.condition != "touch":
        return None
    ends = [m.probability for m in o.methods if m.id in ("M1", "M2") and m.status == "ok" and m.probability is not None]
    return fmt.prob_range(min(ends), max(ends)) if ends else None


def other_condition(result: CaseResult) -> Optional[Pair]:
    """For a 'reaches at any time' claim: the chance of still being at or above the target on the last day.

    The two market-based methods only, the same pair the console shows in its status bar.
    """
    o = result.oracle
    rng = _last_day_range(result)
    if rng is None:
        return None
    return (f"The number above counts any day the price gets there. Still being at or above the target on {o.target_date} is less likely: about {rng}.",
            f"上面的数字只要中途有一天到过就算。如果要求 {o.target_date} 那天收盘还在目标价以上，可能性更低：约 {rng}。")


SHORT_NAMES = {"M1": ("options", "期权"), "M2": ("swings", "波动"), "M4": ("own past", "自己的过去"), "M3": ("similar companies", "同类公司")}


@dataclass(frozen=True)
class Part:
    """One method of the index, for its dot on the bar."""

    id: str
    name: Pair
    value: float
    weight: float


def index_parts(result: CaseResult) -> list[Part]:
    """The methods that make the main number, with the result and the share of each, lowest result first."""
    o = result.oracle
    if o is None or o.basis != "index":
        return []
    parts = [Part(m.id, SHORT_NAMES.get(m.id, (m.id, m.id)), _value(result, m), o.weights.get(m.id, 0.0)) for m in counted_methods(result)]
    return sorted(parts, key=lambda p: p.value)


@dataclass(frozen=True)
class Chip:
    """A fact beside the main number: a few words and a figure on the page, the full sentence one tap away."""

    kind: str          # "past", "last_day", "made_of"
    label: Pair
    value: Pair
    full: Pair


def answer_chips(result: CaseResult) -> list[Chip]:
    out: list[Chip] = []
    o = result.oracle
    line = calibration_line(result)
    if line is not None:
        out.append(Chip("past", ("In the past it really happened", "过去实际发生"), (f"about {pct(o.calibrated)}", f"约 {pct(o.calibrated)}"), line))
    other = other_condition(result)
    rng = _last_day_range(result)
    if other is not None and rng is not None:
        out.append(Chip("last_day", ("Still there on the last day", "到期那天还在"), (f"about {rng}", f"约 {rng}"), other))
    why = why_a_range(result)
    if why is not None:
        n = len(counted_methods(result))
        label = (f"{n} methods, weighted", f"{n} 种算法加权") if n > 1 else ("One method", "一种算法")
        out.append(Chip("made_of", label, ("", ""), why))
    return out


def why_a_range(result: CaseResult) -> Optional[Pair]:
    """What the main number is made of: the methods, the share of each, and how far apart they are."""
    o = result.oracle
    if o is None or o.low is None:
        return None
    used = counted_methods(result)
    n = len(used)
    if n < 2:
        return ("Only one method could be computed for this claim, so the estimate rests on that one method.",
                "这个说法只有一种算法能算出结果，所以估计只依据这一种算法。")
    if o.basis == "index" and o.spread_low is not None:
        rng = fmt.prob_range(o.spread_low, o.spread_high)
        return (f"The main number is a weighted average of {n} methods, listed below with the share of each. On their own they give {rng}. "
                "It is an estimate, not a promise.",
                f"主数字是 {n} 种算法的加权平均，每种的比例列在下面。它们各自的结果是 {rng}。这是一个估计，不是保证。")
    return (f"{n} methods were used; the range runs from the lowest of their results to the highest. It is not a margin of error.",
            f"我们用了 {n} 种算法，范围是其中最低和最高的结果。它不是误差范围。")


def calibration_line(result: CaseResult) -> Optional[Pair]:
    """What a number like this has been worth in the backtest, with what the backtest leaves out."""
    o = result.oracle
    if o is None or o.calibrated is None or o.low is None:
        return None
    return (f"Checked against the past: when this index said {pct(o.low)}, the claim came true about {pct(o.calibrated)} of the time "
            "(large US companies that still trade, 2006 to 2025; companies that failed are missing, so this is on the high side).",
            f"和过去对照：这个指数说 {pct(o.low)} 的时候，实际发生了约 {pct(o.calibrated)}"
            "（2006 到 2025 年、至今仍在交易的美国大公司；倒闭的公司不在其中，所以这个数偏高）。")


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
