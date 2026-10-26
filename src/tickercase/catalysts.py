"""Catalyst register (v0.12): the specific things a stock's price depends on, with their dated history.

A catalyst is a product or program milestone, an approval, a contract, a deal, a financing or a legal matter whose
outcome or timing moves the business (RKLB: Neutron's first launch; INTC: 18A and foundry customers). The probability
methods only see the price; the register says what the price is waiting for and how it reacted each time the
story changed.

Who does what:
- Code fetches the sources: sentences from the company's 8-K press releases of the last year (F..) and headlines
  from Google News and Yahoo Finance (N..). Every source has a date, an outlet and a link.
- Claude, in two calls, (1) names the main themes and the words to search for, after which code runs a targeted
  Google News search for each theme over two years, and (2) writes the catalysts: name, kind, status, the latest
  stated timing, and the dated events, each citing source ids.
- Code checks every catalyst: the cited ids exist, each event is dated like one of its cited sources, the year of
  the expected timing appears in a cited source, and every number in the text appears in a cited source. Problems
  are listed next to the catalyst.
- Code measures the market reaction to each event (``structure.reaction``: the event day and the next, with the
  part the market and the peers do not explain).
- The user accepts or rejects each proposal. Accepted ones go into the stock's register, which later runs reuse
  and update; rejected names are not proposed again.

The probability range is unchanged. A dated catalyst feeds the event scenario: its date, and the move the option
market prices around that date.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional, Sequence

from pydantic import BaseModel, ValidationError

from .models import Catalyst, CatalystEvent, CatalystProposal, CatalystRegister, CatalystSource, FilingRecord, NewsItem
from .narrative import FALLBACK_MODELS, SCALES, USAGE_FIELDS, _matches, numbers_in, uses_effort

PROMPT_VERSION = "1"
MAX_THEMES = 5
MAX_CATALYSTS = 6
MAX_NEWS = 250
PER_THEME = 45
KINDS = ("product", "regulatory", "contract", "financing", "deal", "operations", "legal", "other")
STATUSES = ("upcoming", "in_progress", "achieved", "delayed", "cancelled")
DIRECTIONS = ("up", "down", "mixed")
EXPECTED_RE = re.compile(r"^(\d{4})(?:-(H[12]|Q[1-4]|\d{2})(?:-(\d{2}))?)?$")

SCOUT_PROMPT = """You read headlines and sentences from a listed company's press releases.
Name the catalysts the stock price depends on: specific products or programs, regulatory decisions, contracts, acquisitions, financings or legal matters whose outcome or timing moves the business.
Not catalysts: price moves, analyst ratings, index or fund flows, general market news.
For each, give 1 to 3 search words that find news about it (for example a product or program name).
At most {n}, most important first. Use only what the sources say."""

BUILD_PROMPT = """You keep the catalyst register of one listed company. Sources are numbered: N.. are news headlines (date, outlet), F.. are sentences from the company's 8-K press releases (date).
Rules:
- Use only the sources. No outside knowledge, and no dates or outcomes the sources do not state.
- A catalyst is a specific product or program milestone, regulatory decision, contract, acquisition, financing or legal matter whose outcome or timing moves the business. Price moves, analyst ratings and general market news are not catalysts.
- For each catalyst give: a short name; kind; status; expected = the latest stated target timing as YYYY, YYYY-H1, YYYY-H2, YYYY-Q1 to YYYY-Q4, YYYY-MM or YYYY-MM-DD (empty if no source states one); direction = how success would move the price; one summary sentence; source_ids; 1 to 3 search_terms.
- events = the dated history: each time a source reports a change (a schedule slip, a test result, a milestone reached, a deal signed or closed). date = the date of the source that first reports it. change = one short sentence on what changed. Cite the sources of each event.
- Every number and date you write must appear in the sources you cite.
- A catalyst already in the confirmed register: return it only if the sources show something new, with updates set to its id and only the new events. Leave updates empty for new catalysts. Do not propose names the user rejected.
- Write name_en, summary_en and change_en in English, and name_zh, summary_zh and change_zh in Simplified Chinese.
- At most {n} catalysts, most important first."""


# ------------------------------------------------------------------ sources


def _title_key(item: NewsItem) -> str:
    return re.sub(r"\W+", " ", item.title.lower()).strip()


def _spread(items: list[NewsItem], k: int) -> list[NewsItem]:
    """At most k items spread evenly over time, so a theme keeps its early history instead of only its latest week."""
    items = sorted(items, key=lambda x: x.published)
    if len(items) <= k:
        return items
    step = (len(items) - 1) / (k - 1)
    return [items[round(i * step)] for i in range(k)]


def source_table(filings: Sequence[tuple[FilingRecord, list[str], str]], news: Iterable[NewsItem], *,
                 themes: Sequence[Sequence[NewsItem]] = (), max_news: int = MAX_NEWS, per_theme: int = PER_THEME) -> list[CatalystSource]:
    """Numbered sources: 8-K sentences (F01..) newest filing first, then headlines (N001..) newest first, one per title.

    Headlines from the theme searches come first (at most ``per_theme`` each, spread over time); the general
    headlines fill the rest up to ``max_news``.
    """
    out: list[CatalystSource] = []
    for record, sentences, url in filings:
        items = ", ".join(x.strip() for x in (record.items or "").split(",") if x.strip() and x.strip() != "9.01")
        for s in sentences:
            out.append(CatalystSource(id=f"F{len(out) + 1:02d}", kind="filing", day=record.filing_date, text=s,
                                      publisher=f"SEC {record.form_type}" + (f" ({items})" if items else ""), url=url))
    seen: set[str] = set()
    chosen: list[NewsItem] = []

    def take(candidates: Iterable[NewsItem], room: int) -> None:
        for item in candidates:
            if room <= 0 or len(chosen) >= max_news:
                return
            key = _title_key(item)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(item)
            room -= 1

    for group in themes:
        unique = list({_title_key(i): i for i in group}.values())
        take(_spread(unique, per_theme), per_theme)
    take(sorted(news, key=lambda x: x.published, reverse=True), max_news)
    for n, item in enumerate(sorted(chosen, key=lambda x: x.published, reverse=True), start=1):
        out.append(CatalystSource(id=f"N{n:03d}", kind="news", day=(item.published - timedelta(hours=4)).date(), text=item.title,
                                  publisher=item.publisher, url=item.url))
    return out


def sources_text(sources: Sequence[CatalystSource]) -> str:
    return "\n".join(json.dumps({"id": s.id, "date": s.day.isoformat(), "outlet": s.publisher, "text": s.text}, ensure_ascii=False) for s in sources)


def register_text(register: Optional[CatalystRegister]) -> str:
    if register is None or (not register.catalysts and not register.rejected):
        return "Confirmed register: empty."
    rows = [json.dumps({"id": c.id, "name": c.name_en, "status": c.status, "expected": c.expected or "",
                        "events": [f"{e.day}: {e.change_en}" for e in c.events]}, ensure_ascii=False) for c in register.catalysts]
    return ("Confirmed register (one catalyst per line):\n" + "\n".join(rows)
            + (f"\nRejected by the user: {', '.join(register.rejected)}" if register.rejected else ""))


# ------------------------------------------------------------------ Claude calls


class _Theme(BaseModel):
    name: str
    search_terms: list[str]


class _Themes(BaseModel):
    themes: list[_Theme]


class _Event(BaseModel):
    date: str
    change_en: str
    change_zh: str
    source_ids: list[str]


class _Catalyst(BaseModel):
    updates: str
    name_en: str
    name_zh: str
    kind: str
    status: str
    expected: str
    direction: str
    summary_en: str
    summary_zh: str
    source_ids: list[str]
    search_terms: list[str]
    events: list[_Event]


class _Catalysts(BaseModel):
    catalysts: list[_Catalyst]


def _obj(props: dict, required: Optional[list[str]] = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


STR = {"type": "string"}
STRS = {"type": "array", "items": STR}
THEMES_SCHEMA = _obj({"themes": {"type": "array", "items": _obj({"name": STR, "search_terms": STRS})}})
CATALYSTS_SCHEMA = _obj({"catalysts": {"type": "array", "items": _obj({
    "updates": STR, "name_en": STR, "name_zh": STR, "kind": {"type": "string", "enum": list(KINDS)},
    "status": {"type": "string", "enum": list(STATUSES)}, "expected": STR, "direction": {"type": "string", "enum": list(DIRECTIONS)},
    "summary_en": STR, "summary_zh": STR, "source_ids": STRS, "search_terms": STRS,
    "events": {"type": "array", "items": _obj({"date": STR, "change_en": STR, "change_zh": STR, "source_ids": STRS})}})}})


class CallResult(BaseModel):
    status: str  # ok | refused | error
    text: str = ""
    model: str
    usage: dict[str, int] = {}
    error: Optional[str] = None


def request_params(*, model: str, effort: str, system: str, user: str, schema: dict) -> dict:
    params: dict[str, Any] = dict(model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": user}],
                                  output_config={"format": {"type": "json_schema", "schema": schema}})
    if uses_effort(model):
        params["output_config"]["effort"] = effort
    if model in FALLBACK_MODELS:
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"
    return params


def call(client: Any, **kw) -> CallResult:
    model = kw["model"]
    try:
        response = client.beta.messages.create(**request_params(**kw))
    except Exception as exc:  # typed SDK errors are reported as text; the case itself is unaffected
        return CallResult(status="error", model=model, error=f"{type(exc).__name__}: {getattr(exc, 'message', exc)}")
    usage = getattr(response, "usage", None)
    used = {k: int(getattr(usage, k, 0) or 0) for k in USAGE_FIELDS} if usage is not None else {}
    served = getattr(response, "model", model) or model
    if getattr(response, "stop_reason", None) == "refusal":
        return CallResult(status="refused", model=served, usage=used, error="the model declined this request")
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
    return CallResult(status="ok", text=text, model=served, usage=used)


def add_usage(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


def scout(client: Any, sources: Sequence[CatalystSource], *, company: str, ticker: str, register: Optional[CatalystRegister],
          model: str, effort: str) -> tuple[list[str], CallResult]:
    """First call: the main themes and their search words."""
    user = (f"Company: {company} ({ticker}).\n{register_text(register)}\n\nSources (JSON, one per line):\n{sources_text(sources)}\n\n"
            "Name the catalysts and their search words.")
    res = call(client, model=model, effort=effort, system=SCOUT_PROMPT.format(n=MAX_THEMES), user=user, schema=THEMES_SCHEMA)
    if res.status != "ok":
        return [], res
    try:
        parsed = _Themes.model_validate_json(res.text)
    except ValidationError as exc:
        return [], res.model_copy(update={"status": "error", "error": f"themes did not match the schema: {exc.errors()[:1]}"})
    terms: list[str] = []
    for theme in parsed.themes[:MAX_THEMES]:
        term = next((t.strip() for t in theme.search_terms if t.strip()), "") or theme.name.strip()
        if term and term.lower() not in {t.lower() for t in terms}:
            terms.append(term)
    return terms, res


def build(client: Any, sources: Sequence[CatalystSource], *, company: str, ticker: str, register: Optional[CatalystRegister],
          model: str, effort: str) -> tuple[list[Catalyst], CallResult]:
    """Second call: the catalysts and their dated events, each citing sources."""
    user = (f"Company: {company} ({ticker}).\n{register_text(register)}\n\nSources (JSON, one per line):\n{sources_text(sources)}\n\n"
            "Write the catalyst register entries.")
    res = call(client, model=model, effort=effort, system=BUILD_PROMPT.format(n=MAX_CATALYSTS), user=user, schema=CATALYSTS_SCHEMA)
    if res.status != "ok":
        return [], res
    try:
        parsed = _Catalysts.model_validate_json(res.text)
    except ValidationError as exc:
        return [], res.model_copy(update={"status": "error", "error": f"catalysts did not match the schema: {exc.errors()[:1]}"})
    by_id = {s.id: s for s in sources}
    out = []
    for i, c in enumerate(parsed.catalysts[:MAX_CATALYSTS], start=1):
        events = []
        for e in c.events:
            try:
                day = date.fromisoformat(e.date.strip()[:10])
            except ValueError:
                continue  # an event without a usable date is dropped; the check below reports it through the count
            events.append(CatalystEvent(day=day, change_en=e.change_en.strip(), change_zh=e.change_zh.strip(), source_ids=e.source_ids,
                                        sources=[by_id[x] for x in e.source_ids if x in by_id]))
        dropped = len(c.events) - len(events)
        cat = Catalyst(id=f"P{i:02d}", name_en=c.name_en.strip(), name_zh=c.name_zh.strip(), kind=c.kind if c.kind in KINDS else "other",
                       status=c.status if c.status in STATUSES else "upcoming", expected=c.expected.strip() or None,
                       direction=c.direction if c.direction in DIRECTIONS else "up", summary_en=c.summary_en.strip(), summary_zh=c.summary_zh.strip(),
                       search_terms=c.search_terms[:3], source_ids=c.source_ids, sources=[by_id[x] for x in c.source_ids if x in by_id],
                       events=sorted(events, key=lambda e: e.day), updates=c.updates.strip() or None, proposed_by=res.model)
        cat.expected_day = expected_day(cat.expected)
        cat.problems = check(cat, by_id) + ([f"{dropped} event(s) without a valid date were dropped"] if dropped else [])
        out.append(cat)
    return out, res


# ------------------------------------------------------------------ checks


def expected_day(expected: Optional[str]) -> Optional[date]:
    """A day for the scenario: the middle of the stated span (2027 -> 07-01, 2027-H2 -> 10-01, 2027-Q1 -> 02-15, 2027-03 -> 03-15)."""
    m = EXPECTED_RE.match(expected or "")
    if not m:
        return None
    year, part, day = int(m.group(1)), m.group(2), m.group(3)
    try:
        if part is None:
            return date(year, 7, 1)
        if part in ("H1", "H2"):
            return date(year, 4 if part == "H1" else 10, 1)
        if part.startswith("Q"):
            return date(year, 3 * int(part[1]) - 1, 15)
        return date(year, int(part), int(day) if day else 15)
    except ValueError:
        return None


def _source_numbers(sources: Iterable[CatalystSource]) -> list[Decimal]:
    out = []
    for s in sources:
        for _, value, unit in numbers_in(s.text):
            out.append(value * SCALES.get(unit, Decimal(1)))
        out += [Decimal(s.day.year), Decimal(s.day.month), Decimal(s.day.day)]
    return out


def check(c: Catalyst, by_id: dict[str, CatalystSource]) -> list[str]:
    """What does not hold up: unknown ids, events dated unlike their sources, a timing or a number the cited sources do not contain."""
    problems = []
    cited = list(c.source_ids) + [x for e in c.events for x in e.source_ids]
    unknown = sorted({x for x in cited if x not in by_id})
    if unknown:
        problems.append(f"cites unknown sources: {', '.join(unknown)}")
    if not any(x in by_id for x in cited):
        problems.append("no source cited")
    for e in c.events:
        days = [by_id[x].day for x in e.source_ids if x in by_id]
        if not days:
            problems.append(f"event {e.day}: no source cited")
        elif all(abs((d - e.day).days) > 1 for d in days):
            problems.append(f"event {e.day}: none of its sources is dated {e.day} (they are {', '.join(sorted({d.isoformat() for d in days}))})")
    all_sources = [by_id[x] for x in cited if x in by_id]
    if c.expected:
        m = EXPECTED_RE.match(c.expected)
        if not m:
            problems.append(f"expected '{c.expected}' is not a date or span the page can read")
        elif not any(m.group(1) in s.text for s in all_sources):
            problems.append(f"expected {c.expected}: the year {m.group(1)} is not in the cited sources")
    pool = _source_numbers(all_sources)
    for label, text, sources in [("summary", c.summary_en, all_sources), ("summary", c.summary_zh, all_sources)] + \
            [(f"event {e.day}", t, [by_id[x] for x in e.source_ids if x in by_id] + all_sources) for e in c.events for t in (e.change_en, e.change_zh)]:
        local = _source_numbers(sources) or pool
        for raw, value, unit in numbers_in(text):
            if not _matches(value, unit, local, magnitudes=True):
                problems.append(f"{label}: {raw} is not in the cited sources")
    return list(dict.fromkeys(problems))


# ------------------------------------------------------------------ the register


def _dedupe_sources(sources: Iterable[CatalystSource]) -> list[CatalystSource]:
    seen, out = set(), []
    for s in sources:
        key = (s.day, s.text)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def merge(register: Optional[CatalystRegister], proposal: CatalystProposal, accepted: Sequence[str], *, ticker: str, company: Optional[str],
          now: datetime) -> CatalystRegister:
    """Accepted proposals join the register (new ones get the next C.. id; updates add their new events); rejected new names are remembered."""
    reg = register.model_copy(deep=True) if register is not None else CatalystRegister(ticker=ticker, company=company, updated_at=now)
    by_id = {c.id: c for c in reg.catalysts}
    next_n = max([int(c.id[1:]) for c in reg.catalysts if c.id[1:].isdigit()] + [0]) + 1
    for p in proposal.catalysts:
        if p.id not in accepted:
            if not p.updates and p.name_en not in reg.rejected:
                reg.rejected.append(p.name_en)
            continue
        target = by_id.get(p.updates or "")
        if target is None:
            new = p.model_copy(update={"id": f"C{next_n:02d}", "updates": None, "confirmed_at": now})
            next_n += 1
            reg.catalysts.append(new)
            by_id[new.id] = new
            continue
        known = {(e.day, e.change_en) for e in target.events}
        target.events = sorted(target.events + [e for e in p.events if (e.day, e.change_en) not in known], key=lambda e: e.day)
        target.status = p.status
        if p.expected:
            target.expected, target.expected_day = p.expected, p.expected_day
        target.summary_en, target.summary_zh = p.summary_en or target.summary_en, p.summary_zh or target.summary_zh
        target.sources = _dedupe_sources(target.sources + p.sources)
        target.search_terms = list(dict.fromkeys(target.search_terms + p.search_terms))[:3]
        target.problems, target.confirmed_at, target.proposed_by = p.problems, now, p.proposed_by
    reg.updated_at = now
    if company and not reg.company:
        reg.company = company
    return reg


def fill_reactions(catalysts: Iterable[Catalyst], reaction: Callable[[date], Any], *, today: date) -> None:
    """Measure each event's reaction; events measured on fewer than two days are measured again later."""
    for c in catalysts:
        for e in c.events:
            if e.reaction is None or e.reaction.days < 2:
                if e.day <= today:
                    e.reaction = reaction(e.day) or e.reaction


def upcoming(register: Optional[CatalystRegister], *, today: date, until: date) -> list[Catalyst]:
    """Confirmed catalysts with an expected day between today and the claim's deadline, soonest first."""
    if register is None:
        return []
    out = [c for c in register.catalysts if c.expected_day and today < c.expected_day <= until and c.status not in ("achieved", "cancelled")]
    return sorted(out, key=lambda c: c.expected_day)


def proposal_error(status: str, model: str, now: datetime, error: str, **kw) -> CatalystProposal:
    return CatalystProposal(status=status, model=model, created_at=now, error=error, **kw)
