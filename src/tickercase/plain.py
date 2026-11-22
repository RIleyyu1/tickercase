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

from . import fmt, outcomes as outcomes_model
from .models import CaseResult, ClaimDraft
from .event_tilt import points_text
from .oracle import MIN_INDEPENDENT_WINDOWS, independent_windows
from .tiers import TIERS, tier

Pair = tuple[str, str]
__all__ = ["TIERS", "tier", "pct", "rate", "headline", "claim_sentence", "reasons", "other_condition", "why_a_range", "calibration_line", "index_parts", "answer_chips", "major_event_cards",
           "assumptions", "restate", "outcomes", "number_cards", "price_cards", "money_cards", "stale_note"]


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
    value: Optional[object] = None  # the one number of the reason, written large (a pair when the two languages write it differently),
    short: Optional[Pair] = None    # and one short line; the full sentence and the note open under the card
    tag: Optional[Pair] = None      # the plate of the card when it is a fact and not a reason for or against ("today", "needs")


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


ROLE_NAMES = {"CEO": ("CEO", "CEO"), "CFO": ("CFO", "CFO"), "chair": ("board chair", "董事长"), "founder": ("founder", "创始人"),
              "president": ("president", "总裁")}


@dataclass(frozen=True)
class EventCard:
    """A major event of the last weeks, said beside the answer: it is not part of the number."""

    title: Pair
    value: str            # the stock's move around the day, or "" when it could not be measured
    short: Pair
    full: Pair
    sources: tuple        # (label, url) pairs
    tag: Pair = ("not in the number", "不计入数字")


def major_event_cards(result: CaseResult) -> list[EventCard]:
    """The strong events only: a change at the top that the headlines report, with the stock's move around the day."""
    out: list[EventCard] = []
    for e in result.major_events:
        if not e.strong:
            continue
        role = ROLE_NAMES.get(e.role or "", ("top management", "高层"))
        r = e.reaction
        value = fmt.pct(r.stock, 0, signed=True) if r is not None else ""
        moved = ("", "")
        if r is not None:
            own = (f"; {fmt.pct(r.own, 0, signed=True)} after taking out the market and similar stocks" if r.own is not None else "",
                   f"；扣除大盘和同类股票后 {fmt.pct(r.own, 0, signed=True)}" if r.own is not None else "")
            moved = (f" From the close before to the close of {r.end}, the stock moved {fmt.pct(r.stock, 0, signed=True)}{own[0]}.",
                     f"从事发前一天收盘到 {r.end} 收盘，股价变动 {fmt.pct(r.stock, 0, signed=True)}{own[1]}。")
        if e.in_register:
            reg = (" It is in the event register of this stock.", "这件事已经在这只股票的事件表里。")
        elif e.register_updated is not None:
            reg = (f" The event register of this stock was last updated on {e.register_updated}, before this, and does not hold it yet.",
                   f"这只股票的事件表上次更新是 {e.register_updated}，早于这件事，还没有收录它。")
        else:
            reg = ("", "")
        full = (f"On {e.filing_date} the company filed a report of a change among its top officers (form 8-K, item 5.02), and {e.headlines} recent headlines "
                f"report a change of the {role[0]}.{moved[0]} This event does not change the number above directly: the methods read prices, "
                f"and the option prices and price swings since then already hold what the market thinks of it.{reg[0]}",
                f"{e.filing_date} 公司向监管机构报告了高管变动（8-K 表第 5.02 项），近期有 {e.headlines} 条新闻标题报道 {role[1]} 变动。{moved[1]}"
                f"这件事不会直接改变上面的数字：各算法只看价格，而此后的期权价格和股价波动已经包含了市场对它的看法。{reg[1]}")
        short = (f"{e.day:%b} {e.day.day}: {e.headlines} headlines; stock move over two days",
                 f"{e.day.month} 月 {e.day.day} 日 · {e.headlines} 条新闻 · 两天内股价变动") if r is not None else (
                 f"{e.day:%b} {e.day.day}: {e.headlines} headlines", f"{e.day.month} 月 {e.day.day} 日 · {e.headlines} 条新闻")
        sources = tuple([("SEC 8-K", e.filing_url)] + [(i.publisher or "news", i.url) for i in e.samples if i.url])
        o = getattr(result, "oracle", None)
        tag = ("not in the number", "不计入数字")
        if e.in_register and o is not None and o.event_tilt is not None:   # the register holds it and its events are scored
            pts = points_text(o.event_tilt)
            tag = ("in the number", "已计入数字")
            full = (f"On {e.filing_date} the company filed a report of a change among its top officers (form 8-K, item 5.02), and {e.headlines} recent headlines "
                    f"report a change of the {role[0]}.{moved[0]} It is in the event list of this stock, and the recent events together move the number above by "
                    f"{pts} points (at most 5 either way).",
                    f"{e.filing_date} 公司向监管机构报告了高管变动（8-K 表第 5.02 项），近期有 {e.headlines} 条新闻标题报道 {role[1]} 变动。{moved[1]}"
                    f"这件事已经在这只股票的事件表里，近期事件合计把上面的数字调整了 {pts} 个百分点（最多上下 5 个）。")
        else:
            full = (full[0] + " To let it count, press \"Full evidence\", open the events page and press UPDATE.",
                    full[1] + "要让它计入，点「看完整依据」，到事件页按「更新事件」。")
        out.append(EventCard((f"Big news: change of {role[0]}", f"最近的大事：{role[1]} 变动"), value, short, full, sources, tag))
    return out


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
    if o is not None and o.event_tilt is not None and o.base is not None and o.event_parts:
        top = max(o.event_parts, key=lambda p: abs(p.points))
        pts = points_text(o.event_tilt)
        out.append(Chip("events", ("Recent events", "近期事件"), (f"{pct(o.base)} → {pct(o.low)}", f"{pct(o.base)} → {pct(o.low)}"),
                        (f"The methods alone give {pct(o.base)}. {len(o.event_parts)} recent event{'' if len(o.event_parts) == 1 else 's'} from the news "
                         f"move it by {pts} points (at most 5 either way). The largest: {top.name.en} ({points_text(top.points)}). "
                         "An AI model picked and scored the events; the points follow a fixed rule. This part has not been checked against the past.",
                         f"几种算法本身给出 {pct(o.base)}。新闻里的 {len(o.event_parts)} 件近期事件把它调整了 {pts} 个百分点（最多上下 5 个）。"
                         f"影响最大的是：{top.name.zh}（{points_text(top.points)}）。事件由 AI 模型挑选和评分，点数按固定规则换算；这一部分没有和过去对照过。")))
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
    return (f"Checked against the past: when this index said {pct(o.index)}, the claim came true about {pct(o.calibrated)} of the time "
            "(large US companies that still trade, 2006 to 2025; companies that failed are missing, so this is on the high side).",
            f"和过去对照：这个指数说 {pct(o.index)} 的时候，实际发生了约 {pct(o.calibrated)}"
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


# ------------------------------------------------------------------ v0.23.0: what the number means, and two more questions


@dataclass(frozen=True)
class Outcome:
    key: str        # "reach", "between", "below"
    share: int      # whole percent; the three add up to 100
    title: Pair
    text: Pair


def outcomes(result: CaseResult) -> Optional[tuple[Pair, list[Outcome], Pair]]:
    """(the sentence above the bar, the three outcomes, how they were worked out), or None when there is nothing to show."""
    o = result.oracle
    three = outcomes_model.three(result)
    if three is None or not 0.01 <= three.reach <= 0.99:
        return None
    c = result.confirmed_claim.values
    price = format(c.target_price.normalize(), "f")
    spot = f"{o.spot:g}" if o.spot else format(c.reference_price.normalize(), "f")
    a = round(three.reach * 100)
    z = min(round(three.below * 100), 100 - a)
    m = 100 - a - z
    lead = (f"If you buy today and hold until {o.target_date}, by the same methods the three outcomes are:",
            f"如果今天买入并拿到 {o.target_date}，按同样的算法，三种结局的可能性是：")
    if three.touch:
        first = Outcome("reach", a, (f"It gets to {price} {c.currency} on the way", f"中途到过 {price} {c.currency}"),
                        ("The claim comes true. After that the price can still fall back.", "也就是这句话成真。到过之后还可能再跌回来。"))
        never = ("It never got there", "中途没到过")
    else:
        first = Outcome("reach", a, (f"It closes at {price} {c.currency} or higher on the last day", f"到期那天收盘不低于 {price} {c.currency}"),
                        ("The claim comes true.", "也就是这句话成真。"))
        never = ("It closes below the target", "收盘低于目标价")
    items = [first,
             Outcome("between", m, (f"Short of {price}, but above today's price", f"没到 {price}，但高于今天的价格"),
                     (f"{never[0]}, and on the last day the price is at or above today's {spot}.", f"{never[1]}，期末不低于今天的 {spot}。")),
             Outcome("below", z, ("Below today's price on the last day", "期末低于今天的价格"),
                     (f"{never[0]}, and the holding ends at a loss.", f"{never[1]}，最后还亏了。"))]
    names = "、".join(SHORT_NAMES[x][1] for x in three.methods)
    names_en = ", ".join(SHORT_NAMES[x][0] for x in three.methods)
    how = (f"The three add up to 100%. Each price method ({names_en}) splits its own result three ways and the splits are joined with the weights of the main number. "
           "Dividends and taxes are left out, and the past and today's option prices do not promise what comes next.",
           f"三个数加起来是 100%。每种看价格的算法（{names}）各自把结果分成三份，再按主数字的权重合在一起。没有算分红和税；过去的表现和今天的期权价格都不保证以后。")
    return lead, items, how


def number_cards(result: CaseResult) -> list[Reason]:
    """The reasons that say how the main number was worked out: the methods, and what is counted in it."""
    return [r for r in reasons(result) if r.counted or r.page != "fund"]


def money(x) -> Pair:
    """An amount in US dollars in the words of each language ('$215.9B', '2159 亿美元')."""
    v = float(x)
    sign, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e12:
        en, zh = f"{a / 1e12:.2f}T", f"{a / 1e12:.2f} 万亿"
    elif a >= 1e9:
        en, zh = f"{a / 1e9:.1f}B", (f"{a / 1e8:.0f} 亿" if a >= 1e10 else f"{a / 1e8:.1f} 亿")
    elif a >= 1e6:
        en, zh = f"{a / 1e6:.0f}M", (f"{a / 1e8:.1f} 亿" if a >= 1e8 else f"{a / 1e4:.0f} 万")
    else:
        en, zh = f"{a:,.0f}", f"{a:,.0f} "
    return (f"{sign}${en}", f"{sign}{zh}美元")


def _fact(tag: Pair, title: Pair, value: str, short: Pair, text: Pair, note: Optional[Pair] = None) -> Reason:
    return Reason("0", text, "fund", note, False, None, title, value, short, tag)


def _at(series, end):
    return next((p for p in series if p.period_end == end), None)


def price_cards(result: CaseResult) -> list[Reason]:
    """Question 1, what the price already counts on: today's multiple, the growth the target needs, and how often such
    growth happened (the two business reasons the page already had)."""
    out: list[Reason] = []
    cc, facts, o = result.confirmed_claim, result.reported_facts, result.oracle
    if cc is None:
        return out
    c = cc.values
    sales = c.valuation_method.metric_name == "annual_revenue"
    metric = ("revenue", "收入") if sales else ("profit", "利润")
    series = (facts.revenue if sales else facts.net_income) if facts is not None else []
    base = series[-1] if series else None
    spot = o.spot if (o is not None and o.spot) else float(c.reference_price)
    shares = facts.shares_outstanding[-1] if (facts is not None and facts.shares_outstanding) else None
    if base is not None and shares is not None and float(base.value) > 0:
        cap = spot * float(shares.value)
        mult = cap / float(base.value)
        cap_t, val_t = money(cap), money(base.value)
        out.append(_fact(
            ("today", "现状"), ("The price today", "今天的价格"), (f"{mult:.1f}×", f"{mult:.1f} 倍"),
            (f"the market pays ${mult:.1f} for each $1 of yearly {metric[0]}", f"市场为它每 1 美元的年{metric[1]}付 {mult:.1f} 美元"),
            (f"At {spot:g} a share the market values the whole company at about {cap_t[0]}. Its {metric[0]} in the fiscal year ending {base.period_end} "
             f"was {val_t[0]}, so the market pays about {mult:.1f} dollars for each dollar of yearly {metric[0]}.",
             f"按每股 {spot:g} 计算，市场给整家公司的估价约 {cap_t[1]}。截至 {base.period_end} 的财年，公司的{metric[1]}是 {val_t[1]}，"
             f"也就是每 1 美元的年{metric[1]}，市场付了约 {mult:.1f} 美元。"),
            (f"The share count is the one on the cover of the latest filing ({shares.form}, filed {shares.filed}). The higher this number, the more growth the price already counts on.",
             f"股数取自最近一份申报文件的封面（{shares.form}，{shares.filed} 提交）。这个倍数越高，价格里已经算进去的增长就越多。")))
    need = next((x for x in result.calculations if x.name.startswith("required_annual_") and x.status == "ok" and x.value is not None), None)
    growth = next((x for x in result.calculations if x.name == "required_metric_cagr" and x.status == "ok" and x.value is not None), None)
    if need is not None and growth is not None and base is not None and float(growth.value) > 0:
        price = format(c.target_price.normalize(), "f")
        a, b = money(base.value), money(need.value)
        mult_assumed = format(c.valuation_multiple.normalize(), "f")
        out.append(_fact(
            ("needs", "需要"), (f"To be worth {price}", f"要值 {price} {c.currency}"), f"{fmt.pct(float(growth.value), 0, signed=True)}",
            (f"a year: yearly {metric[0]} from {a[0]} to {b[0]}, if it is valued as today", f"每年：年{metric[1]}要从 {a[1]} 涨到 {b[1]}（假设估值倍数不变）"),
            (f"For a price of {price} to rest on the business, at {mult_assumed} dollars of market value for each dollar of yearly {metric[0]}, the company would have to "
             f"bring in {b[0]} a year by the deadline. It brought in {a[0]} in the fiscal year ending {base.period_end}. That is growth of about {rate(growth.value)} a year.",
             f"股价 {price} 要有业绩支撑，按每 1 美元年{metric[1]}对应 {mult_assumed} 美元市值计算，公司到期时每年的{metric[1]}要达到 {b[1]}。"
             f"截至 {base.period_end} 的财年是 {a[1]}。这相当于每年增长约 {rate(growth.value)}。"),
            ("If investors pay a higher multiple by then, less growth is needed; with a lower multiple, more.",
             "如果到时候市场愿意付更高的倍数，需要的增长就少一些；倍数变低，需要的就更多。")))
    return out + [r for r in reasons(result) if not (r.counted or r.page != "fund")]


def money_cards(result: CaseResult) -> list[Reason]:
    """Question 2, how much of what the company earns is left for its owners: profit per 100 of sales, free cash flow,
    what it spends to stay ahead, and whether the number of shares grows. Every figure is the company's own, from its
    annual reports to the SEC; a card is left out when the company did not report the figure."""
    out: list[Reason] = []
    facts = result.reported_facts
    if facts is None or not facts.revenue:
        return out
    rev = facts.revenue[-1]
    end, sales = rev.period_end, float(rev.value)
    if sales <= 0:
        return out
    year = (f"the fiscal year ending {end}", f"截至 {end} 的财年")
    tag_m = ("reported", "财报")
    ni = _at(facts.net_income, end)
    if ni is not None:
        per = float(ni.value) / sales * 100
        a, b = money(ni.value), money(rev.value)
        if per >= 0:
            out.append(_fact(tag_m, ("Of every $100 it sells", "每卖出 100 美元"), (f"${per:.0f}", f"{per:.0f} 美元"),
                             ("is left as profit", "留下的利润"),
                             (f"In {year[0]} the company sold {b[0]} and kept {a[0]} as profit: about {per:.0f} dollars of every 100.",
                              f"{year[1]}，公司卖出 {b[1]}，留下 {a[1]} 的利润：每 100 美元里约 {per:.0f} 美元。"),
                             ("Profit follows accounting rules; the cash that came in can be more or less (see the next card).",
                              "利润按会计规则计算，实际收到的现金可能更多或更少（见下一张卡片）。")))
        else:
            out.append(_fact(tag_m, ("Of every $100 it sells", "每卖出 100 美元"), (f"-${-per:.0f}", f"-{-per:.0f} 美元"),
                             ("it loses this much", "亏掉这么多"),
                             (f"In {year[0]} the company sold {b[0]} and lost {money(-float(ni.value))[0]}: about {-per:.0f} dollars for every 100 it sold.",
                              f"{year[1]}，公司卖出 {b[1]}，亏损 {money(-float(ni.value))[1]}：每卖出 100 美元约亏 {-per:.0f} 美元。"),
                             ("A company that loses money has to pay for itself with its cash, new debt or new shares.",
                              "亏损的公司要靠手里的现金、借债或发新股来维持。")))
    ocf, capex = _at(facts.operating_cash_flow, end), _at(facts.capital_expenditure, end)
    if ocf is not None and capex is not None:
        fcf = float(ocf.value) - float(capex.value)
        a, b, c3 = money(fcf), money(ocf.value), money(capex.value)
        out.append(_fact(tag_m, ("Cash left over", "真正剩下的现金"), a,
                         (f"free cash flow: {fmt.pct(fcf / sales, 0)} of what it sold", f"自由现金流：相当于收入的 {fmt.pct(fcf / sales, 0)}"),
                         (f"In {year[0]} the business brought in {b[0]} in cash and spent {c3[0]} on buildings and equipment. What is left, {a[0]}, is its free cash flow: "
                          "the cash it can return to its owners, keep, or use to pay debt.",
                          f"{year[1]}，公司的经营带来 {b[1]} 的现金，其中 {c3[1]} 花在厂房和设备上。剩下的 {a[1]} 就是自由现金流：可以还给股东、留在账上，或者还债。"),
                         ("One year can be unusual; a company that is building fast shows little free cash for a while.",
                          "单独一年可能不典型；正在大举扩张的公司，一段时间内自由现金流会很少。")))
    rd = _at(facts.research_development, end)
    if rd is not None or capex is not None:
        spent = (float(rd.value) if rd is not None else 0.0) + (float(capex.value) if capex is not None else 0.0)
        parts_en = " and ".join(x for x in (f"{money(rd.value)[0]} on research and development" if rd is not None else "",
                                            f"{money(capex.value)[0]} on buildings and equipment" if capex is not None else "") if x)
        parts_zh = "，".join(x for x in (f"研发 {money(rd.value)[1]}" if rd is not None else "", f"厂房和设备 {money(capex.value)[1]}" if capex is not None else "") if x)
        out.append(_fact(tag_m, ("What it spends to stay ahead", "为了保持领先花掉的"), fmt.pct(spent / sales, 0),
                         ("of what it sold goes to research and equipment" if rd is not None else "of what it sold goes to buildings and equipment",
                          "的收入花在研发和设备上" if rd is not None else "的收入花在厂房和设备上"),
                         (f"In {year[0]} the company spent {parts_en}: together {fmt.pct(spent / sales, 0)} of what it sold.",
                          f"{year[1]}，公司的投入是：{parts_zh}，合计相当于收入的 {fmt.pct(spent / sales, 0)}。"),
                         ("A business that has to keep spending this much to hold its place leaves less for its owners than its sales suggest.",
                          "一门生意如果必须一直这样投入才能守住位置，留给股东的就比收入看上去的少。")))
    pts = facts.shares_outstanding
    if len(pts) >= 2:
        last = pts[-1]
        before = [p for p in pts[:-1] if 300 <= (last.period_end - p.period_end).days <= 430]
        if before and float(last.value) > 0:
            prev = min(before, key=lambda p: abs((last.period_end - p.period_end).days - 365))
            change = float(last.value) / float(prev.value) - 1
            if abs(change) <= 0.5:   # a larger change is a share split, which the cover-page counts do not adjust for
                sbc = _at(facts.share_based_compensation, end)
                paid = ((f" It paid its staff {money(sbc.value)[0]} in shares in {year[0]} ({fmt.pct(float(sbc.value) / sales, 0)} of what it sold).",
                         f"{year[1]}它用股票给员工发了 {money(sbc.value)[1]} 的报酬（相当于收入的 {fmt.pct(float(sbc.value) / sales, 0)}）。") if sbc is not None else ("", ""))
                if change < -0.0005:
                    short = ("fewer shares than a year ago: each share owns a little more", "股数比一年前少：每一股占的份额变大了")
                elif change > 0.0005:
                    short = ("more shares than a year ago: each share owns a little less", "股数比一年前多：每一股占的份额变小了")
                else:
                    short = ("about as many shares as a year ago", "股数和一年前差不多")
                out.append(_fact(tag_m, ("The number of shares", "股票的数量"), fmt.pct(change, 1, signed=True), short,
                                 (f"The company reported {float(last.value) / 1e6:,.0f} million shares on {last.period_end} and {float(prev.value) / 1e6:,.0f} million on {prev.period_end}." + paid[0],
                                  f"公司在 {last.period_end} 报告的股数是 {float(last.value) / 1e8:.2f} 亿股，{prev.period_end} 是 {float(prev.value) / 1e8:.2f} 亿股。" + paid[1]),
                                 ("When a company issues new shares, each old share owns a smaller part of it; when it buys shares back, a larger part.",
                                  "公司发新股时，原来每一股占的份额会变小；回购股票时，份额会变大。")))
    return out


def stale_note(result: CaseResult) -> Optional[Pair]:
    """Said above the answer when a source did not answer and its earlier answers were used (the fallback of risk R.2)."""
    stale = getattr(result, "stale_sources", None)
    if not stale:
        return None
    en = "; ".join(f"{s.source} did not answer, so its figures are from {s.captured_at:%Y-%m-%d %H:%M} UTC, {s.age_hours:g} hours before this answer" for s in stale)
    zh = "；".join(f"{s.source} 这次没有应答，它的数据是 {s.captured_at:%Y-%m-%d %H:%M} UTC 取到的，比这个答案早 {s.age_hours:g} 小时" for s in stale)
    return (en + ". The price may have moved since; ask again later for a current answer.", zh + "。此后价格可能已经变化，稍后再查一次可以得到最新的答案。")
