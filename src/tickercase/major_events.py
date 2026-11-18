"""Major events found by code alone (v0.20.0): a change at the top of the company.

The catalyst register is written by Claude and only when the user asks, so it can be weeks old. A company must
report the departure or appointment of a top officer in an 8-K under item 5.02, and such a change fills the
headlines. This module reads both, at no cost, on every run:

- an 8-K with item 5.02 filed in the last ``window_days`` days is a candidate;
- headlines from a few days before the filing onward that name a top role (CEO, CFO, chair, founder, president) next
  to a word of change (resigns, ousted, interim, successor, ...) are counted;
- with at least ``STRONG_HEADLINES`` such headlines the event is "strong": it is shown in the simple view. A 5.02 filing
  without them is listed on the console's event page only, as a filing whose subject the code did not read (item 5.02
  also covers directors and pay plans).

The event does not enter the probability. It is said beside the number, with the stock's move around the day, and the
event page says when the register is older than the event.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

from .models import CatalystRegister, FilingRecord, MajorEvent, NewsItem

WINDOW_DAYS = 45
STRONG_HEADLINES = 3
LEAD_DAYS = 5           # a change is often reported a few days before the 8-K is filed
ROLES = (
    ("CEO", re.compile(r"\b(?:ceo|chief executive)\b", re.IGNORECASE)),
    ("CFO", re.compile(r"\b(?:cfo|chief financial)\b", re.IGNORECASE)),
    ("chair", re.compile(r"\b(?:chairman|chairwoman|board chair)\b", re.IGNORECASE)),
    ("founder", re.compile(r"\bfounder\b", re.IGNORECASE)),
    ("president", re.compile(r"\bpresident\b", re.IGNORECASE)),
)
CHANGE_RE = re.compile(
    r"\b(?:resign\w*|step(?:s|ped|ping)? down|oust\w*|fired|fires|firing|dismiss\w*|depart\w*|retir\w*|replac\w*|interim|successor|succeed\w*|"
    r"appoint\w*|names|named|search for|remov\w*|exit\w*|leav(?:e|es|ing)|left|reinstat\w*|return|comeback|without)\b", re.IGNORECASE)


def _day(item: NewsItem) -> date:
    return (item.published - timedelta(hours=4)).date()   # New York's day, as the register dates its headlines


def role_of(title: str) -> Optional[str]:
    """The top role a headline names next to a word of change; None when it does not report such a change."""
    if not CHANGE_RE.search(title):
        return None
    return next((name for name, pattern in ROLES if pattern.search(title)), None)


def has_item(record: FilingRecord, item: str) -> bool:
    return item in {x.strip() for x in (record.items or "").split(",")}


def covered(register: Optional[CatalystRegister], day: date) -> bool:
    """Whether the register already holds a leadership item with an event around that day."""
    if register is None:
        return False
    return any(c.kind == "leadership" and any(abs((e.day - day).days) <= LEAD_DAYS + 2 for e in c.events) for c in register.catalysts)


def leadership_events(records: Iterable[FilingRecord], news: Sequence[NewsItem], *, today: date,
                      register: Optional[CatalystRegister] = None, window_days: int = WINDOW_DAYS) -> list[MajorEvent]:
    """8-K item 5.02 filings of the last ``window_days`` days with the headlines that report a change at the top, newest first.

    Two filings close together (the departure, then the successor) share their headlines; each headline is counted for
    the latest filing it can belong to, so the list does not say one story twice.
    """
    filings = sorted((r for r in records if r.form_type in ("8-K", "8-K/A") and has_item(r, "5.02")
                      and timedelta(0) <= today - r.filing_date <= timedelta(days=window_days)), key=lambda r: r.filing_date, reverse=True)
    tagged = [(i, role_of(i.title)) for i in news]
    tagged = [(i, role) for i, role in tagged if role is not None]
    out: list[MajorEvent] = []
    taken: set[int] = set()
    for record in filings:
        start = record.filing_date - timedelta(days=LEAD_DAYS)
        mine = [(n, i, role) for n, (i, role) in enumerate(tagged) if n not in taken and _day(i) >= start]
        taken.update(n for n, _, _ in mine)
        roles = Counter(role for _, _, role in mine)
        role = roles.most_common(1)[0][0] if roles else None
        days = [_day(i) for _, i, _ in mine]
        day = min([record.filing_date] + [d for d in days if d <= record.filing_date])
        samples = sorted((i for _, i, r in mine if r == role), key=lambda i: i.published)[:3]
        out.append(MajorEvent(kind="leadership", day=day, filing_date=record.filing_date, role=role, headlines=len(mine),
                              samples=list(samples), filing_url=record.filing_index_url, strong=len(mine) >= STRONG_HEADLINES,
                              in_register=covered(register, day),
                              register_updated=register.updated_at.date() if register is not None else None))
    return out
