"""Events in the main number (v0.21.0): scored events, the fixed rule and its limits, the one-key update, and the words on both views."""

from datetime import date, timedelta

import pytest

from tickercase import plain
from tickercase.catalysts import BUILD_PROMPT, CATALYSTS_SCHEMA, prune
from tickercase.event_tilt import CAP, MAX_EVENTS, apply, event_parts, limited, points_text, unscored
from tickercase.models import Catalyst, CatalystEvent, CatalystRegister
from tickercase.storage import CatalystStore
from tickercase.ui import cockpit as ck
from tickercase.ui import simple as sm

from conftest import FIXED_NOW, FIXED_TODAY
from test_catalysts import FakeClaude, _find, _run, _svc

DEADLINE = FIXED_TODAY + timedelta(days=900)


def event(days_ago: int, effect: str, strength, text: str = "x") -> CatalystEvent:
    return CatalystEvent(day=FIXED_TODAY - timedelta(days=days_ago), change_en=text, change_zh=text, effect=effect, strength=strength)


def register(*catalysts: Catalyst) -> CatalystRegister:
    return CatalystRegister(ticker="SYNT", updated_at=FIXED_NOW, catalysts=list(catalysts))


def cat(cid: str, events, status: str = "achieved", expected_day=None) -> Catalyst:
    return Catalyst(id=cid, name_en=f"Story {cid}", name_zh=f"事件 {cid}", status=status, expected_day=expected_day, events=list(events))


def test_points_follow_the_rule_direction_strength_age_and_open_stories():
    reg = register(cat("C01", [event(0, "down", 3)]), cat("C04", [event(30, "up", 2)]), cat("C05", [event(60, "up", 1)]),
                   cat("C02", [event(0, "up", 1)], status="in_progress"),
                   cat("C03", [event(0, "up", 1)], status="upcoming", expected_day=DEADLINE + timedelta(days=30)))
    by = {(p.catalyst_id, p.age_days): p for p in event_parts(reg, today=FIXED_TODAY, deadline=DEADLINE)}
    assert by[("C01", 0)].points == pytest.approx(-0.03)            # 3 units of one point, on the day
    assert by[("C04", 30)].points == pytest.approx(0.01)            # 2 units, half gone after 30 days
    assert by[("C05", 60)].points == pytest.approx(0.0025)
    assert by[("C02", 0)].points == pytest.approx(0.015) and by[("C02", 0)].open     # the story is still open: x1.5
    assert by[("C03", 0)].points == pytest.approx(0.01) and not by[("C03", 0)].open  # it ends after the deadline


def test_events_without_a_score_old_events_and_future_dates_do_not_count():
    reg = register(cat("C01", [event(10, "up", None), event(10, "none", 0), event(10, "up", 0), event(121, "up", 3), event(-2, "up", 3)]))
    assert event_parts(reg, today=FIXED_TODAY, deadline=DEADLINE) == []
    assert unscored(reg, today=FIXED_TODAY) == 1
    assert event_parts(None, today=FIXED_TODAY, deadline=DEADLINE) == []


def test_one_story_counts_once_only_the_largest_five_count_and_the_sum_is_cut_at_the_limit():
    one = event_parts(register(cat("C01", [event(i, "down", 3) for i in range(7)] + [event(0, "up", 1)])), today=FIXED_TODAY, deadline=DEADLINE)
    assert [(p.age_days, p.effect) for p in one] == [(0, "down")]            # seven headlines about one matter are one reason
    reg = register(*[cat(f"C{i:02d}", [event(i, "down", 3)]) for i in range(7)])
    parts = event_parts(reg, today=FIXED_TODAY, deadline=DEADLINE)
    assert len(parts) == MAX_EVENTS and [p.age_days for p in parts] == [0, 1, 2, 3, 4]
    assert limited(0.5, sum(p.points for p in parts)) == -CAP
    assert limited(0.5, 0.2) == CAP and limited(0.5, 0.012) == pytest.approx(0.012)
    assert limited(0.04, -0.05) == pytest.approx(-0.02) and limited(0.98, 0.05) == pytest.approx(0.01)   # half of the way to the end of the scale
    assert points_text(-0.03) == "-3" and points_text(0.004) == "+0.4" and points_text(0.0) == "0" and points_text(-0.032) == "-3.2"


def test_the_case_keeps_the_index_and_adds_the_events(tmp_path):
    svc = _svc(tmp_path)
    plain_case = _run(svc)
    o0 = plain_case.oracle
    assert o0.base == o0.low and o0.event_tilt is None and o0.event_parts == [] and o0.index == o0.low
    assert plain.answer_chips(plain_case)[0].kind != "events"
    CatalystStore(tmp_path / "catalysts").save(register(cat("C01", [event(0, "down", 3, "The chief executive left."), event(3, "down", 2)], status="in_progress"),
                                                        cat("C02", [event(0, "down", 3)], status="in_progress")))
    r = _run(svc)
    o = r.oracle
    assert o.base == pytest.approx(o0.low) and o.event_raw == pytest.approx(-0.045 - 0.045)
    assert o.event_tilt == pytest.approx(limited(o.base, o.event_raw)) and o.low == o.high == pytest.approx(o.base + o.event_tilt)
    assert o.calibrated == o0.calibrated                      # the backtest line refers to the index before events
    assert "no backtest" in o.event_note.en and "没有回测" in o.event_note.zh and o.event_note.zh in r.report.headline.zh
    # applying again with another register starts from the same index
    again = apply(o, None, today=FIXED_TODAY)
    assert again.low == pytest.approx(o.base) and again.event_tilt is None and again.event_note is None
    # both views say the index, the adjustment and the result
    chips = plain.answer_chips(r)
    assert chips[0].kind == "events" and plain.pct(o.base) in chips[0].value[0] and "Story C01" in chips[0].full[0] and "事件 C01" in chips[0].full[1]
    line = plain.calibration_line(r)
    assert line is None or plain.pct(o.base) in line[0]
    html = sm.answer_screen(True, plain.headline(r), plain.claim_sentence(r), chips, plain.index_parts(r), o.calibrated)
    assert "近期事件" in html and sm.CSS.count("<") == 2
    block = ck.tilt_block(r, False)
    assert "The chief executive left." in block and "still open" in block and f"{o.base * 100:.1f}%" in block and f"{o.low * 100:.1f}%" in block
    assert "cut at the limit" in block
    assert "Events" in ck.prb_screen(r, False, False) and "事件调整" in ck.prb_screen(r, True, False)
    if r.structure is not None and r.structure.shifted is not None and r.structure.shifted.base is not None:
        sh = r.structure.shifted
        assert sh.low == pytest.approx(sh.base + limited(sh.base, o.event_raw))


def test_a_register_of_an_older_version_says_that_its_events_carry_no_score(tmp_path):
    svc = _svc(tmp_path)
    CatalystStore(tmp_path / "catalysts").save(register(cat("C01", [event(5, "none", None)])))
    r = _run(svc)
    assert r.oracle.event_tilt is None and r.oracle.low == r.oracle.base
    assert "carry no score" in ck.tilt_block(r, False) and "还没有评分" in ck.tilt_block(r, True)
    assert "not scored" in ck.catalyst_card(r.catalysts.catalysts[0], False)


def test_the_prompt_asks_for_a_score_and_never_for_a_probability():
    assert "effect" in BUILD_PROMPT and "strength" in BUILD_PROMPT and "You give no probability" in BUILD_PROMPT
    ev = CATALYSTS_SCHEMA["properties"]["catalysts"]["items"]["properties"]["events"]["items"]
    assert ev["properties"]["effect"]["enum"] == ["up", "down", "none"] and ev["properties"]["strength"]["enum"] == ["0", "1", "2", "3"]
    assert {"effect", "strength"} <= set(ev["required"])


def test_prune_keeps_what_passes_and_lists_what_does_not():
    good = Catalyst(id="P01", name_en="Good", name_zh="好", events=[event(1, "up", 1)])
    day = (FIXED_TODAY - timedelta(days=2)).isoformat()
    one_bad = Catalyst(id="P02", name_en="One bad event", name_zh="一件不对", events=[event(2, "down", 2), event(9, "up", 1)],
                       problems=[f"event {day}: 45% is not in the cited sources"])
    bad = Catalyst(id="P03", name_en="Bad", name_zh="坏", events=[event(1, "up", 3)], problems=["expected 2028: the year 2028 is not in the cited sources"])
    empty_update = Catalyst(id="P04", name_en="Update", name_zh="更新", updates="C01", events=[event(2, "up", 1)],
                            problems=[f"event {day}: no source cited"])
    kept, dropped = prune([good, one_bad, bad, empty_update])
    assert [c.id for c in kept] == ["P01", "P02"] and [e.day for e in kept[1].events] == [FIXED_TODAY - timedelta(days=9)]
    assert all(c.problems == [] for c in kept)
    assert [d[0] for d in dropped] == ["P02", "P03", "P04"] and dropped[0][3].startswith(f"event {day}")


def scored_build(sources: list[dict]) -> dict:
    body = FakeClaude.default_build(sources)
    events = body["catalysts"][0]["events"]
    events[0].update(effect="up", strength="1")
    events[1].update(effect="down", strength="3")
    slip = _find(sources, "delays Orbiter X first delivery")
    body["catalysts"].append({
        "updates": "", "name_en": "Made-up contract", "name_zh": "编造的合同", "kind": "contract", "status": "upcoming", "expected": "", "direction": "up",
        "summary_en": "A contract worth 999 million.", "summary_zh": "一份 9.99 亿的合同。", "source_ids": [slip["id"]], "search_terms": [],
        "events": [{"date": slip["date"], "change_en": "Signed.", "change_zh": "已签署。", "source_ids": [slip["id"]], "effect": "up", "strength": "3"}]})
    return body


def test_one_key_writes_what_passes_and_moves_the_number(tmp_path):
    svc = _svc(tmp_path)
    r = _run(svc)
    before = r.oracle.low
    fake = FakeClaude(build=scored_build)
    r = svc.update_catalysts(r, mode="synthetic", client=fake)
    prop = r.catalyst_proposal
    assert len(fake.calls) == 2 and prop.decided_at == FIXED_NOW and prop.accepted == ["P01"]
    assert {d[1] for d in prop.dropped} == {"Made-up contract"} and "999" in prop.dropped[0][3]
    reg = CatalystStore(tmp_path / "catalysts").get("SYNT")
    assert [c.name_en for c in reg.catalysts] == ["Orbiter X first delivery"] and reg.rejected == []   # a failed check is not a rejection by the user
    slip = next(e for e in reg.catalysts[0].events if e.day == date(2026, 8, 5))
    assert (slip.effect, slip.strength) == ("down", 3)
    o = r.oracle
    assert o.base == pytest.approx(before) and o.event_parts and o.event_tilt < 0 and o.low == pytest.approx(o.base + o.event_tilt)
    assert all(p.catalyst_id == "C01" for p in o.event_parts)
    # taking the catalyst out of the register takes its events out of the number
    r = svc.remove_catalyst(r, "C01")
    assert r.oracle.low == pytest.approx(before) and r.oracle.event_tilt is None
    # a later run reads the register again
    assert _run(svc).oracle.event_tilt is None


def test_the_model_is_told_what_the_detector_found_and_the_register_does_not_hold():
    from tickercase.catalysts import found_text
    from tickercase.models import MajorEvent

    e = MajorEvent(day=date(2026, 11, 11), filing_date=date(2026, 11, 12), role="CEO", headlines=19, strong=True)
    text = found_text([e, e.model_copy(update={"strong": False}), e.model_copy(update={"in_register": True})])
    assert text.count("item 5.02") == 1 and "19 headlines since 2026-11-11" in text and 'kind "leadership"' in text
    assert found_text([e.model_copy(update={"in_register": True})]) == ""


def test_the_big_news_card_says_whether_the_event_is_in_the_number():
    from test_major_events import CEO_NEWS
    from tickercase.models import MajorEvent, OracleSummary, Text

    e = MajorEvent(day=date(2026, 11, 12), filing_date=date(2026, 11, 12), role="CEO", headlines=5, samples=CEO_NEWS[:1], strong=True, in_register=True)
    o = OracleSummary(target_price="1", target_date=DEADLINE, low=0.4, high=0.4, tier="uncertain", tier_label=Text(en="a", zh="a"),
                      agreement=Text(en="a", zh="a"), basis="index", base=0.43, event_tilt=-0.03)
    card = plain.major_event_cards(type("R", (), {"major_events": [e], "oracle": o})())[0]
    assert card.tag == ("in the number", "已计入数字") and "-3 points" in card.full[0] and "-3 个百分点" in card.full[1]
    assert "in the number" in sm.events(False, [card]) and "not in the number" not in sm.events(False, [card])
    waiting = plain.major_event_cards(type("R", (), {"major_events": [e.model_copy(update={"in_register": False})], "oracle": o})())[0]
    assert waiting.tag[0] == "not in the number" and "press UPDATE" in waiting.full[0] and "更新事件" in waiting.full[1]
