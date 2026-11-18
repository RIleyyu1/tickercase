"""Major events found by code (v0.20.0): an 8-K item 5.02 filing with headlines that report a change at the top."""

from datetime import date, datetime, timezone

from tickercase import plain
from tickercase.catalysts import BUILD_PROMPT, KINDS, SCOUT_PROMPT
from tickercase.major_events import STRONG_HEADLINES, leadership_events, role_of
from tickercase.models import Catalyst, CatalystEvent, CatalystRegister, FilingRecord, MajorEvent, NewsItem, Reaction
from tickercase.ui import simple as sm

TODAY = date(2026, 11, 17)
NOW = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)


def filing(day: str, items: str, form: str = "8-K") -> FilingRecord:
    return FilingRecord(provider_id="sec", ticker="XYZ", cik="1", accession_number=f"0000000001-26-{day[-5:].replace('-', '')}", form_type=form,
                        is_amendment=False, filing_date=date.fromisoformat(day), items=items, filing_index_url=f"https://sec.example/{day}",
                        source_response_url="https://sec.example/s", retrieved_at=NOW, data_mode="live")


def headline(day: str, title: str) -> NewsItem:
    return NewsItem(title=title, publisher="Wire", url=f"https://news.example/{abs(hash(title))}", origin="google_news",
                    published=datetime.fromisoformat(day + "T18:00:00+00:00"))


CEO_NEWS = [headline("2026-11-11", "Report: XYZ board weighs removing its CEO"),
            headline("2026-11-12", "XYZ CEO resigns; finance chief named interim chief executive"),
            headline("2026-11-13", "XYZ begins search for new CEO after founder steps down"),
            headline("2026-11-13", "XYZ shares fall 6% on chip export rules")]


def test_a_headline_needs_a_top_role_and_a_word_of_change():
    assert role_of("Jensen Huang resigns as Nvidia CEO") == "CEO"
    assert role_of("Nvidia names interim chief executive") == "CEO"
    assert role_of("Acme CFO to retire next year") == "CFO"
    assert role_of("Nvidia CEO says demand is strong") is None          # a role, no change
    assert role_of("Analyst steps down price target on Nvidia") is None  # a change, no role


def test_a_5_02_filing_with_headlines_is_a_strong_event_dated_at_the_first_report():
    events = leadership_events([filing("2026-11-12", "5.02,9.01"), filing("2026-11-17", "2.02,9.01")], CEO_NEWS, today=TODAY)
    assert len(events) == 1
    e = events[0]
    assert e.strong and e.role == "CEO" and e.headlines == 3 >= STRONG_HEADLINES
    assert e.day == date(2026, 11, 11) and e.filing_date == date(2026, 11, 12)   # a headline came a day before the filing
    assert not e.in_register and e.register_updated is None and len(e.samples) == 3


def test_a_5_02_filing_without_headlines_is_listed_as_weak_and_old_filings_are_left_out():
    events = leadership_events([filing("2026-11-03", "5.02"), filing("2026-07-02", "5.02"), filing("2026-11-10", "5.07")],
                               [headline("2026-11-04", "XYZ unveils new product line")], today=TODAY)
    assert [(e.filing_date, e.strong, e.role, e.headlines) for e in events] == [(date(2026, 11, 3), False, None, 0)]
    case_like = type("R", (), {"major_events": events})()
    assert plain.major_event_cards(case_like) == []          # the simple view shows strong events only


def test_two_filings_close_together_do_not_tell_one_story_twice():
    events = leadership_events([filing("2026-11-12", "5.02"), filing("2026-11-16", "5.02")], CEO_NEWS, today=TODAY)
    assert sum(e.headlines for e in events) == 3


def test_the_register_covers_the_event_only_with_a_leadership_item_near_that_day():
    def register(kind: str, day: str) -> CatalystRegister:
        c = Catalyst(id="C01", name_en="CEO change", name_zh="CEO 变动", kind=kind,
                     events=[CatalystEvent(day=date.fromisoformat(day), change_en="x", change_zh="x")])
        return CatalystRegister(ticker="XYZ", updated_at=NOW, catalysts=[c])

    recs = [filing("2026-11-12", "5.02")]
    assert leadership_events(recs, CEO_NEWS, today=TODAY, register=register("leadership", "2026-11-12"))[0].in_register
    assert not leadership_events(recs, CEO_NEWS, today=TODAY, register=register("product", "2026-11-12"))[0].in_register
    assert not leadership_events(recs, CEO_NEWS, today=TODAY, register=register("leadership", "2026-03-01"))[0].in_register


def test_the_card_says_the_move_the_sources_and_that_the_number_does_not_change():
    e = MajorEvent(day=date(2026, 11, 12), filing_date=date(2026, 11, 12), role="CEO", headlines=33, samples=CEO_NEWS[:2],
                   filing_url="https://sec.example/x", strong=True, register_updated=date(2026, 10, 28),
                   reaction=Reaction(start=date(2026, 11, 11), end=date(2026, 11, 13), days=2, stock=-0.081, own=-0.062, basis="market+peers"))
    card = plain.major_event_cards(type("R", (), {"major_events": [e]})())[0]
    assert card.value == "-8%" and "CEO" in card.title[0] and "CEO" in card.title[1]
    assert "does not change the number above directly" in card.full[0] and "-6%" in card.full[0] and "2026-10-28" in card.full[0]
    assert "不会直接改变上面的数字" in card.full[1] and "2026-10-28" in card.full[1]
    html = sm.events(False, [card])
    assert "https://sec.example/x" in html and "not in the number" in html and sm.esc(card.full[0]) in html
    assert sm.events(True, []) == ""


def test_a_change_at_the_top_is_a_kind_of_catalyst():
    assert "leadership" in KINDS
    assert "chief executive" in SCOUT_PROMPT and 'kind "leadership"' in BUILD_PROMPT
    assert Catalyst(id="C1", name_en="a", name_zh="a", kind="leadership").kind == "leadership"
