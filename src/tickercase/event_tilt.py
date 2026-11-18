"""Events in the main number (v0.21.0): a bounded adjustment of the index, computed by code from scored events.

The four methods read prices and reported figures. The catalyst register holds what the news says: Claude picks the
events and gives each a direction ("up" or "down" for the share price) and a strength (1 minor, 2 material, 3 changes
the outlook of the company). Claude gives no probability. Code turns the scores into points by one fixed rule:

    points of an event = direction x strength x 1 point x 0.5 ^ (age in days / 30) x (1.5 if its story is still open)

- An event loses half its weight every 30 days, because the price takes the news in; after 120 days it is left out.
- A story is open when its catalyst is not achieved or cancelled and is not dated after the claim's deadline: the
  outcome is still ahead, and the two methods that read past prices cannot see it.
- One story counts once: of the events of one catalyst only the one with the most points is used, so five headlines
  about the same matter are not five reasons.
- Only the five largest count. Their sum is cut at +-5 points, and never moves the number more than half of the way
  to 0 % or to 100 %.

This part of the number has no backtest: there is no archive of past news to replay. The index before the events
stays on the page (``OracleSummary.base``), the backtest line refers to that index, and every event shows its points.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from .models import CatalystRegister, EventTiltPart, OracleSummary, Text
from .tiers import TIERS, tier

UNIT = 0.01            # one unit of strength, on the day of the event
CAP = 0.05             # the events together move the number by at most this much
HALF_LIFE_DAYS = 30
MAX_AGE_DAYS = 120
OPEN_FACTOR = 1.5
MAX_EVENTS = 5
CLOSED = ("achieved", "cancelled")


def is_open(catalyst, deadline: date) -> bool:
    """The story has not ended and is not dated after the deadline of the claim."""
    return catalyst.status not in CLOSED and (catalyst.expected_day is None or catalyst.expected_day <= deadline)


def event_parts(register: Optional[CatalystRegister], *, today: date, deadline: date) -> list[EventTiltPart]:
    """The scored events of the last 120 days with their points: the largest of each catalyst, largest first, at most five."""
    out: list[EventTiltPart] = []
    for c in register.catalysts if register is not None else []:
        open_ = is_open(c, deadline)
        first = len(out)
        for e in c.events:
            age = (today - e.day).days
            if e.effect not in ("up", "down") or not e.strength or age < 0 or age > MAX_AGE_DAYS:
                continue
            strength = max(1, min(3, int(e.strength)))
            weight = 0.5 ** (age / HALF_LIFE_DAYS) * (OPEN_FACTOR if open_ else 1.0)
            out.append(EventTiltPart(catalyst_id=c.id, name=Text(en=c.name_en, zh=c.name_zh or c.name_en), day=e.day,
                                     change=Text(en=e.change_en, zh=e.change_zh or e.change_en), effect=e.effect, strength=strength,
                                     age_days=age, weight=weight, open=open_,
                                     points=(1 if e.effect == "up" else -1) * strength * UNIT * weight))
        if len(out) > first + 1:  # one story counts once: its event with the most points, the latest of equals
            out[first:] = [max(out[first:], key=lambda p: (abs(p.points), p.day.toordinal()))]
    out.sort(key=lambda p: (-abs(p.points), -p.day.toordinal(), p.catalyst_id))
    return out[:MAX_EVENTS]


def limited(base: float, raw: float) -> float:
    """The sum cut at the cap, and at half of the way from the index to 0 % or to 100 %."""
    t = max(-CAP, min(CAP, raw))
    return max(-base / 2, min((1 - base) / 2, t))


def unscored(register: Optional[CatalystRegister], *, today: date) -> int:
    """Events of the last 120 days that carry no score: the register was written before v0.21.0."""
    return sum(1 for c in (register.catalysts if register is not None else []) for e in c.events
               if e.strength is None and 0 <= (today - e.day).days <= MAX_AGE_DAYS)


def points_text(x: float) -> str:
    """Percentage points with a sign: '-3', '+0.4', '0'."""
    v = x * 100
    if abs(v) < 0.05:
        return "0"
    return f"{v:+.0f}" if abs(v) >= 0.95 and abs(v - round(v)) < 0.05 else f"{v:+.1f}"


def apply(oracle: OracleSummary, register: Optional[CatalystRegister], *, today: date) -> OracleSummary:
    """The summary with the events added to the index. Applying it again with another register starts from the same index."""
    base = oracle.base if oracle.base is not None else oracle.low
    if base is None or oracle.basis != "index":
        return oracle
    parts = event_parts(register, today=today, deadline=oracle.target_date)
    raw = sum(p.points for p in parts)
    shift = limited(base, raw) if parts else 0.0
    value = base + shift
    key = tier(value, value)
    note = None
    if parts:
        cut = "" if abs(shift - raw) < 1e-9 else f" (their sum is {points_text(raw)}; the limit is {points_text(CAP)[1:]} points)"
        cut_zh = "" if abs(shift - raw) < 1e-9 else f"（合计 {points_text(raw)}，上限 {points_text(CAP)[1:]} 个百分点）"
        note = Text(en=f"Events: {len(parts)} scored event{'' if len(parts) == 1 else 's'} of the register move the index of {base * 100:.1f}% by "
                       f"{points_text(shift)} points to {value * 100:.1f}%{cut}. The scores are Claude's reading of the news; the points follow a fixed "
                       "rule, and this part has no backtest.",
                    zh=f"事件：事件表里 {len(parts)} 件已评分的事把 {base * 100:.1f}% 的指数调整了 {points_text(shift)} 个百分点，到 {value * 100:.1f}%{cut_zh}。"
                       "评分是 Claude 对新闻的判断，点数按固定规则换算；这一部分没有回测。")
    return oracle.model_copy(update={"low": value, "high": value, "base": base, "event_tilt": shift if parts else None, "event_raw": raw if parts else None,
                                     "event_parts": parts, "event_note": note, "tier": key, "tier_label": Text(en=TIERS[key][0], zh=TIERS[key][1])})


def shifted(p: Optional[float], oracle: OracleSummary) -> Optional[float]:
    """Another index (the same methods at another price) with the same events."""
    if p is None or oracle.event_raw is None:
        return p
    return p + limited(p, oracle.event_raw)
