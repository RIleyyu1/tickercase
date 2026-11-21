"""The simple view (v0.16.0): one sentence in, one sentence to confirm, one page of answer in plain words.

All data is SYNTHETIC; no test touches the network.
"""

import pytest
from streamlit.testing.v1 import AppTest

from tickercase import plain
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.ui import simple as sm
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, ROOT, make_settings, offline_factory, ps_draft

APP = str(ROOT / "app.py")


# ------------------------------------------------------------------ the words


@pytest.mark.parametrize("low, high, word", [
    (0.01, 0.06, "almost_no"), (0.10, 0.35, "unlikely"), (0.40, 0.79, "uncertain"), (0.30, 0.55, "uncertain"),
    (0.65, 0.80, "likely"), (0.88, 0.97, "very_likely"), (None, None, "unknown"),
])
def test_the_word_follows_where_the_range_sits(low, high, word):
    assert plain.tier(low, high) == word


def test_percentages_keep_a_decimal_only_near_the_ends():
    assert plain.pct(0.403) == "40%"
    assert plain.pct(0.004) == "0.4%"
    assert plain.pct(0.996) == "99.6%"
    assert plain.pct(0.0) == "0%"


@pytest.fixture
def case(tmp_path):
    settings = make_settings(tmp_path)
    svc = CaseService(settings, fetcher_factory=offline_factory(settings), store=CaseStore(tmp_path / "cases"),
                      now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    draft = ps_draft()
    return svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")


def test_the_headline_states_the_same_range_as_the_report(case):
    head = plain.headline(case)
    assert head.low == case.oracle.low and head.high == case.oracle.high
    assert head.tier == plain.tier(case.oracle.low, case.oracle.high)
    from tickercase.fmt import prob_ends
    for text in head.range_text:
        assert prob_ends(case.oracle.low, case.oracle.high)[0] in text


def test_every_reason_is_a_number_of_the_case_in_both_languages(case):
    reasons = plain.reasons(case)
    assert 1 <= len(reasons) <= 4
    for r in reasons:
        assert r.sign in ("+", "-", "0")
        assert r.page in ("methods", "fund")
        assert "%" in r.text[0] and "%" in r.text[1]
    if case.price_base_rate is not None and case.price_base_rate.windows:
        assert str(case.price_base_rate.windows) in reasons[0].text[0]


def test_the_assumptions_made_for_the_reader_are_listed(case):
    made = plain.assumptions(case)
    assert all(len(pair) == 2 and pair[0] and pair[1] for pair in made)


def test_the_working_screen_groups_the_data_steps_into_five_lines():
    html = sm.working({"sec_filings": "ok", "sec_facts": "ok", "insiders": "ok", "price_history": "running"}, zh=False)
    assert html.count("tcs-step ") == 5
    assert html.count("tcs-step ok") == 1 and html.count("tcs-step run") == 1 and html.count("tcs-step wait") == 3
    every = [k for _, keys in sm.WORK_GROUPS for k in keys]
    assert len(every) == len(set(every)) == 14


# ------------------------------------------------------------------ the page


@pytest.fixture
def simple_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    monkeypatch.setenv("TICKERCASE_SNAPSHOT_DIR", str(tmp_path / "snap"))
    monkeypatch.setenv("TICKERCASE_CASE_DIR", str(tmp_path / "cases"))
    monkeypatch.setenv("TICKERCASE_UI", "simple")
    monkeypatch.setenv("TICKERCASE_TODAY", "2026-10-01")
    import streamlit as st

    st.cache_resource.clear()
    return tmp_path


def start(lang="en"):
    at = AppTest.from_file(APP)
    at.session_state["sec_mode"] = "synthetic"
    at.session_state["lang"] = lang
    at.run(timeout=30)
    assert not at.exception, at.exception
    return at


def keys(at):
    return {b.key for b in at.button}


def click(at, key):
    next(b for b in at.button if b.key == key).click()
    at.run(timeout=60)
    assert not at.exception, at.exception
    return at


def page_text(at):
    return " ".join(str(m.value) for m in at.markdown)


def test_the_simple_view_is_the_default_and_opens_on_one_sentence(simple_env):
    at = start()
    assert at.session_state["ui"] == "simple"
    assert "btn_s_read" in keys(at) and "btn_prefill" not in keys(at) and "btn_boot_enter" not in keys(at)
    assert "how likely" in page_text(at)


def test_one_sentence_one_confirmation_one_answer(simple_env):
    at = start()
    at.text_area(key="f_claim_text").set_value("SYNT reaches $100 by 2031")
    at.run(timeout=30)
    click(at, "btn_s_read")
    assert at.session_state["page"] == "check"
    said = page_text(at)
    assert "SYNT" in said and "100" in said and "Did we read it right?" in said
    click(at, "btn_s_yes")          # confirms and starts the run
    if at.session_state["page"] == "run":
        at.run(timeout=60)
    assert at.session_state["page"] == "result", at.session_state["run_error"]
    result = at.session_state["result"]
    answer = page_text(at)
    assert plain.headline(result).word[0] in answer
    assert "not advice to buy or sell" in answer
    assert {"btn_s_ev_price", "btn_s_ev_money", "btn_s_ev_number", "btn_s_again"} <= keys(at)
    assert not [r for r in at.radio if r.key == "ui"]          # one path: no switch between two views
    assert "Three questions" in answer and "How much of what it earns" in answer
    # a row of cards opens the page of the console that holds its evidence, and one key leads back
    click(at, "btn_s_ev_number")
    assert at.session_state["ui"] == "expert" and at.session_state["rpage"] == "methods" and "nav_methods" in keys(at)
    assert not [r for r in at.radio if r.key == "ui"]
    click(at, "btn_to_simple")
    assert at.session_state["ui"] == "simple" and "btn_s_ev_price" in keys(at)
    click(at, "btn_s_ev_money")
    assert at.session_state["rpage"] == "fund"


def test_a_sentence_without_a_stock_stays_on_the_start_page_and_says_why(simple_env):
    at = start()
    at.text_area(key="f_claim_text").set_value("it will go up a lot")
    at.run(timeout=30)
    click(at, "btn_s_read")
    assert at.session_state["page"] == "claim"
    assert at.warning or at.error


def test_the_same_page_in_chinese(simple_env):
    at = start("zh")
    assert "可能性" in page_text(at)
    at.text_area(key="f_claim_text").set_value("SYNT reaches $100 by 2031")
    at.run(timeout=30)
    click(at, "btn_s_read")
    assert "对吗" in page_text(at)


# ------------------------------------------------------------------ one story in both views (round 6 of the persona review)


def cases_of(tmp_path):
    """Several synthetic cases: both conditions, a hard target, an easy one and one already reached."""
    settings = make_settings(tmp_path)
    svc = CaseService(settings, fetcher_factory=offline_factory(settings), store=CaseStore(tmp_path / "cases"),
                      now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    out = []
    for overrides in ({}, {"price_condition": "touch"}, {"target_price": "900"}, {"target_price": "900", "price_condition": "touch"},
                      {"target_price": "1"}, {"target_price": "1", "price_condition": "touch"}):
        draft = ps_draft(**overrides)
        out.append(svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic"))
    return [c for c in out if c.oracle is not None and c.oracle.low is not None]


def test_the_simple_view_and_the_console_use_the_same_word_range_and_method_count(tmp_path):
    from tickercase.ui import cockpit as ck

    cases = cases_of(tmp_path)
    assert len(cases) >= 4
    for c in cases:
        head = plain.headline(c)
        assert head.word[0] == ck.tier_text(c.oracle, False) == c.oracle.tier_label.en
        assert head.word[1] == ck.tier_text(c.oracle, True) == c.oracle.tier_label.zh
        used = [m for m in c.oracle.methods if m.status == "ok" and ck.counted(c, m)
                and (m.touch_probability if c.oracle.condition == "touch" else m.probability) is not None]
        assert len(plain.counted_methods(c)) == len(used)
        why = plain.why_a_range(c)
        assert ("two methods" in why[0]) == (len(used) == 2 and c.oracle.basis == "market")
        # the reasons marked as the basis of the main number are exactly the counted methods that have a reason
        basis = [r for r in plain.reasons(c) if r.counted and r.page == "methods"]
        assert len(basis) == len([m for m in used if m.id in ("M1", "M2", "M4")])
        assert c.oracle.counted == [m.id for m in used]
        other = plain.other_condition(c)
        if other is not None:  # the same pair of market methods as the console's status bar
            assert ck.other_side(c, False)[0] in other[0]


def test_no_sentence_asks_for_a_negative_rise_or_growth(tmp_path):
    for c in cases_of(tmp_path):
        for r in plain.reasons(c):
            for text in r.text + (r.note or ("", "")):
                assert "rise -" not in text and "grow about -" not in text and "上涨 -" not in text and "增长约 -" not in text
                assert "None" not in text and "nan" not in text


def test_the_reasons_from_methods_keep_the_limits_the_console_states(case):
    for r in plain.reasons(case):
        if r.page == "methods" or "companies of similar size" in r.text[0]:
            assert r.note and r.note[0] and r.note[1], r.text[0]


@pytest.mark.parametrize("sentence, target, multiple, direction", [
    ("微软五年后一千", "1000", None, None), ("NVDA 三年后一千二", "1200", None, None),
    ("Coca-Cola will rise another 5% within three years", None, "1.05", None),
    ("听说特斯拉明年要跌到 200 美元", "200", None, "down"), ("Tesla will fall to $200 next year", "200", None, "down"),
    ("英伟达三年内会涨到 300 美元", "300", None, None),
])
def test_the_reader_understands_spoken_numbers_percent_gains_and_falls(sentence, target, multiple, direction):
    from tickercase.extract import extract_claim

    e = extract_claim(sentence, today=FIXED_TODAY)
    assert (str(e.target_price) if e.target_price is not None else None) == target
    assert (str(e.target_multiple) if e.target_multiple is not None else None) == multiple
    assert e.direction == direction


def read(at, sentence):
    at.text_area(key="f_claim_text").set_value(sentence)
    at.run(timeout=30)
    return click(at, "btn_s_read")


def test_an_unknown_company_gets_one_plain_message_not_silence(simple_env):
    at = read(start("zh"), "茅台明年到两千")
    assert at.session_state["page"] == "claim"
    said = " ".join(str(w.value) for w in at.warning)
    assert "没认出是哪家公司" in said and "核对页" not in said and "代码换了" not in said


def test_a_claim_about_a_fall_is_named_as_one_and_does_not_go_on(simple_env):
    at = read(start("zh"), "听说 SYNT 明年要跌到 20 美元")
    assert at.session_state["page"] == "claim"
    assert "下跌" in " ".join(str(w.value) for w in at.warning)


def test_a_sentence_without_a_price_asks_for_it_in_one_line(simple_env):
    at = read(start("en"), "SYNT will do great by 2031")
    assert at.session_state["page"] == "check"
    said = page_text(at)
    assert "(no price yet)" in said and " at USD" not in said
    warnings = [str(w.value) for w in at.warning]
    assert len(warnings) == 1 and "the price it should reach" in warnings[0]
    assert next(b for b in at.button if b.key == "btn_s_yes").disabled
    assert "btn_s_edit" not in keys(at)          # the form is already open


def test_a_second_sentence_says_nothing_about_the_first(simple_env):
    at = read(start("en"), "SYNT reaches $100 by 2031")
    click(at, "btn_s_back")
    at = read(at, "it will go up a lot")
    said = " ".join(str(w.value) for w in at.warning) + page_text(at)
    assert "SYNT" not in said.replace("f_claim_text", "") or "ticker changed" not in said
    assert "ticker changed" not in said and "Check" not in " ".join(str(w.value) for w in at.warning)


def test_a_rise_of_more_than_double_is_not_capped_at_100_percent():
    r = plain.Restated(ticker="MSFT", company="MICROSOFT CORP", target="1200", currency="USD", deadline="2031-11-13", touch=False,
                       reference="523.88", needed=1200 / 523.88 - 1)
    assert "129%" in sm.confirm_sentence(False, r) and "129%" in sm.confirm_sentence(True, r)


def test_the_headline_is_a_weighted_index_where_history_and_peers_are_a_small_share(tmp_path):
    from tickercase.oracle import METHOD_WEIGHTS

    assert abs(sum(METHOD_WEIGHTS.values()) - 1) < 1e-9 and METHOD_WEIGHTS["M4"] < 1 / 3 and METHOD_WEIGHTS["M3"] <= 0.05
    seen = 0
    for c in cases_of(tmp_path):
        o = c.oracle
        ok = {m.id: m for m in o.methods if m.status == "ok"}
        if "M1" in ok or "M2" in ok:
            seen += 1
            touch = o.condition == "touch"
            vals = {i: (m.touch_probability if touch else m.probability) for i, m in ok.items() if i in o.counted}
            assert o.basis == "index" and set(o.weights) == set(o.counted) == set(vals)
            assert abs(sum(o.weights.values()) - 1) < 1e-9
            assert o.low == o.high and abs(o.low - sum(o.weights[i] * vals[i] for i in vals)) < 1e-9
            assert o.spread_low == min(vals.values()) and o.spread_high == max(vals.values())
            assert not touch or "M3" not in o.counted           # the peer rate does not measure a touch
            market = sum(o.weights.get(i, 0) for i in ("M1", "M2"))
            assert market >= 0.6                                 # the two market methods carry most of the number
    assert seen


def test_weights_are_shared_out_again_when_a_method_is_missing():
    from tickercase.oracle import METHOD_WEIGHTS as W, weighted_index

    index, weights = weighted_index({"M1": 0.30, "M4": 0.60})
    assert abs(sum(weights.values()) - 1) < 1e-9 and weights["M1"] > weights["M4"]
    assert abs(index - (W["M1"] * 0.30 + W["M4"] * 0.60) / (W["M1"] + W["M4"])) < 1e-9
    assert weighted_index({"M1": 0.2})[0] == 0.2


def test_the_backtest_line_reads_between_two_bins_and_is_silent_outside_them():
    from tickercase import calibration

    data = calibration.table()
    assert data is not None and data["cases"] > 10000 and data["weights"] == __import__("tickercase.oracle", fromlist=["x"]).METHOD_WEIGHTS
    for condition in ("touch", "end"):
        bins = [b for b in data[condition]["bins"] if b["n"] >= data["min_cases"]]
        mid = (bins[1]["said"] + bins[2]["said"]) / 2
        got = calibration.calibrated(mid, condition)
        assert min(bins[1]["happened"], bins[2]["happened"]) <= got <= max(bins[1]["happened"], bins[2]["happened"])
        assert calibration.calibrated(bins[1]["said"], condition) == pytest.approx(bins[1]["happened"])
        assert calibration.calibrated(bins[-1]["said"] + 0.01, condition) is None
    assert calibration.calibrated(None, "touch") is None and calibration.calibrated(0.5, "other") is None


def test_the_answer_page_carries_the_backtest_line_when_there_is_one(tmp_path):
    from tickercase import plain

    for c in cases_of(tmp_path):
        line = plain.calibration_line(c)
        if c.oracle.calibrated is None:
            assert line is None
        else:
            assert plain.pct(c.oracle.calibrated) in line[0] and "companies that failed are missing" in line[0] and "倒闭的公司" in line[1]


@pytest.mark.parametrize("sentence, day", [
    ("LLY closes above $1500 on 2028-11-13", "2028-11-13"), ("礼来 2028年11月13日 收在 1500 美元以上", "2028-11-13"),
    ("礼来 2028/11/13 收在 1500 美元以上", "2028-11-13"), ("特斯拉明年年底到 500 美元", "2027-12-31"),
])
def test_a_date_written_out_is_the_deadline_as_written(sentence, day):
    from tickercase.extract import extract_claim

    e = extract_claim(sentence, today=FIXED_TODAY)
    assert str(e.target_date) == day
    assert str(e.target_price) in ("1500", "500")


def test_a_target_that_is_not_a_number_is_named_as_that(simple_env):
    at = read(start("en"), "SYNT reaches $100 by 2031")
    click(at, "btn_s_edit")
    at.text_input(key="f_target_price").set_value("abc")
    at.run(timeout=30)
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "have to be numbers" in warnings and "could not be fetched" not in warnings
    assert "abc USD" not in page_text(at)


def test_the_history_reason_is_written_when_the_stock_has_a_price_history(tmp_path):
    # the synthetic company has no price history, so a rate is put on the case: a live run met a name clash here
    from datetime import date as _date

    from tickercase import plain
    from tickercase.models import PriceBaseRate

    case = cases_of(tmp_path)[0]
    fields = dict(symbol="SYNT", window_months=60, windows=120, hits=30, rate=0.25, required_return="1.0", history_start=_date(2005, 1, 31), touch_hits=45)
    case = case.model_copy(update={"price_base_rate": PriceBaseRate.model_construct(**fields)})
    texts = [r.text[0] for r in plain.reasons(case)]
    assert any("Of the 120 past" in x for x in texts)


def test_the_moving_answer_keeps_every_sentence_and_the_style_sheet_survives_the_page(tmp_path):
    from tickercase import plain
    from tickercase.ui import simple as sm

    assert sm.CSS.count("<") == 2                      # an angle bracket inside the sheet makes the page drop all of it
    for c in cases_of(tmp_path):
        chips, parts = plain.answer_chips(c), plain.index_parts(c)
        html = sm.answer_screen(False, plain.headline(c), plain.claim_sentence(c), chips, parts, c.oracle.calibrated)
        assert plain.headline(c).word[0] in html
        for full in (plain.calibration_line(c), plain.why_a_range(c), plain.other_condition(c)):
            assert full is None or sm.esc(full[0]) in html          # what was a paragraph is still in the page
        assert abs(sum(p.weight for p in parts) - 1) < 1e-9 and html.count("tcs-dot") == (len(parts) if len(parts) > 1 else 0)
        if 0.01 <= c.oracle.low <= 0.99:
            assert f'--to:{round(c.oracle.low * 100)}"' in html and f"{round(c.oracle.low * 100)}%</span>" in html
        cards = sm.reasons(False, plain.reasons(c))
        for r in plain.reasons(c):
            assert r.title and r.value and r.short
            assert sm.esc(r.text[0]) in cards and (r.note is None or sm.esc(r.note[0]) in cards)


def test_the_control_panel_skin_stays_on_the_simple_pages_and_survives_the_page():
    from tickercase.ui import cockpit as ck
    from tickercase.ui import simple as sm

    assert sm.SKIN.count("<") == 2 and sm.CSS.count("<") == 2   # one angle bracket more and the page drops the sheet
    assert "tcs-" not in sm.SKIN                                  # SKIN restyles Streamlit's widgets only; the markup's rules are in CSS
    for zh in (True, False):
        top = sm.bridge(zh)
        assert top.count("tcs-lamp") == 4 and "tc-l-red" not in top   # three lamps in their holder; red is kept for warnings
        assert 'aria-hidden="true"' in top and "TICKERCASE" in top
    screen = sm.crt("x")
    assert screen.count('class="tcs-screw"') == 4 and "tcs-bz" in screen
    assert ".tcs-screw{" in sm.CSS and ".tcs-screw" not in ck.CSS   # the console's sheet has no such rule to fall back on
    for name in ("tcs-lit", "tcs-breathe", "tcs-sheen", "tcs-stamp", "tcs-settle"):
        assert f"@keyframes {name}{{" in sm.CSS and sm.CSS.count(name) >= 2   # every new movement is defined and used
    source = open("app.py", encoding="utf-8").read()
    assert source.count("st.html(sm.SKIN)") == 1 and source.index("if simple:") < source.index("st.html(sm.SKIN)") < source.index("elif not st.session_state[\"booted\"]")


# ------------------------------------------------------------------ v0.23.0: what the number means, two more questions


def test_the_lognormal_split_adds_up_and_a_path_that_reached_the_target_stays_reached():
    from tickercase import outcomes
    from tickercase.oracle import p_end_above, p_touch

    for touch in (True, False):
        for vol, years, rate in ((0.5149, 3.0, 0.0507), (0.2, 1.0, 0.0), (0.9, 5.0, 0.03)):
            reach, between, below = outcomes.lognormal_split(150.96, 300.0, vol, years, rate, touch)
            assert abs(reach + between + below - 1) < 1e-9 and min(reach, between, below) >= 0
            down = 1 - p_end_above(150.96, 150.96, vol, years, rate)
            if touch:
                assert reach == p_touch(150.96, 300.0, vol, years, rate) and below < down   # some paths reached it and fell back
            else:
                assert reach == p_end_above(150.96, 300.0, vol, years, rate) and below == down


def test_the_reflection_formula_agrees_with_a_simulation():
    import math
    import random
    from tickercase import outcomes

    rng = random.Random(7)
    spot, target, vol, years, rate, steps, paths = 100.0, 160.0, 0.5, 2.0, 0.04, 500, 4000
    dt = years / steps
    drift, sd = (rate - vol * vol / 2) * dt, vol * math.sqrt(dt)
    b = math.log(target / spot)
    reach = below = 0
    for _ in range(paths):
        x = top = 0.0
        for _ in range(steps):
            x += drift + sd * rng.gauss(0, 1)
            top = max(top, x)
        if top >= b:
            reach += 1
        elif x < 0:
            below += 1
    r, _, z = outcomes.lognormal_split(spot, target, vol, years, rate, True)
    # a path watched at 500 moments misses some highs, so the simulated share of reached paths is a little low
    assert abs(reach / paths - r) < 0.04 and abs(below / paths - z) < 0.04


def test_down_windows_are_counted_beside_the_hits():
    from datetime import date
    from decimal import Decimal
    from tickercase.providers.market import price_down_windows, price_touch_hits

    closes = [(date(2020, 1 + i, 1), p) for i, p in enumerate([100, 80, 220, 90, 95, 70, 60])]
    # windows of 2 months, a rise of 100 % needed: starts 100, 80, 220, 90, 95
    assert price_touch_hits(closes, 2, Decimal("1")) == 2                  # 100 -> 220 and 80 -> 220
    assert price_down_windows(closes, 2, Decimal("1")) == (3, 3)            # 220 -> 95, 90 -> 70, 95 -> 60; none of them reached
    assert price_down_windows(closes, 3, Decimal("1")) == (3, 2)            # 100 -> 90 reached 220 on the way
    assert price_down_windows(closes[:2], 2, Decimal("1")) == (0, 0)


def test_the_three_outcomes_add_up_to_100_and_start_from_the_main_number(tmp_path):
    from tickercase import outcomes, plain
    from tickercase.ui import simple as sm

    seen = 0
    for c in cases_of(tmp_path):
        three = outcomes.three(c)
        said = plain.outcomes(c)
        if three is None:
            assert said is None
            continue
        assert abs(three.reach + three.between + three.below - 1) < 1e-9 and three.reach == c.oracle.low
        assert "M3" not in three.methods and set(three.methods) <= set(c.oracle.weights)
        if said is None:
            continue
        seen += 1
        lead, items, how = said
        assert [x.key for x in items] == ["reach", "between", "below"] and sum(x.share for x in items) == 100
        assert items[0].share == round(c.oracle.low * 100)
        for zh in (True, False):
            html = sm.outcomes(zh, lead, items, how)
            assert html.count('class="tcs-leg"') == 1 and all(f"{x.share}%" in html for x in items)
    assert seen >= 1
    # a method of the index that cannot be split (a case stored before v0.23.0 holds no down windows; an input is missing):
    # the card is left out, never worked out from part of the index
    whole = next(c for c in cases_of(tmp_path) if outcomes.three(c) is not None)
    for mid in outcomes.three(whole).methods:
        if mid == "M4":
            broken = whole.model_copy(update={"price_base_rate": whole.price_base_rate.model_copy(update={"down_windows": None, "down_untouched": None})})
        else:
            methods = [m.model_copy(update={"inputs": {}}) if m.id == mid else m for m in whole.oracle.methods]
            broken = whole.model_copy(update={"oracle": whole.oracle.model_copy(update={"methods": methods})})
        assert outcomes.three(broken) is None and plain.outcomes(broken) is None


def test_the_cards_of_the_two_questions_come_from_the_companys_own_reports(tmp_path):
    from decimal import Decimal
    from tickercase import plain
    from tickercase.models import MetricPoint
    from tickercase.ui import simple as sm

    c = cases_of(tmp_path)[0]
    facts = c.reported_facts
    rev = facts.revenue[-1]

    def point(value, concept):
        return MetricPoint(period_start=rev.period_start, period_end=rev.period_end, value=Decimal(value), unit="USD", concept=concept, form="10-K",
                           accession="0000000000-26-000001", filed=rev.filed)

    sales = float(rev.value)
    full = c.model_copy(update={"reported_facts": facts.model_copy(update={
        "operating_cash_flow": [point(str(round(sales * 0.4)), "NetCashProvidedByUsedInOperatingActivities")],
        "capital_expenditure": [point(str(round(sales * 0.1)), "PaymentsToAcquirePropertyPlantAndEquipment")],
        "research_development": [point(str(round(sales * 0.15)), "ResearchAndDevelopmentExpense")]})})
    titles = [r.title[0] for r in plain.money_cards(full)]
    assert "Cash left over" in titles and "What it spends to stay ahead" in titles and "Of every $100 it sells" in titles
    cash = next(r for r in plain.money_cards(full) if r.title[0] == "Cash left over")
    assert "30%" in cash.short[0]                                           # 40 % in, 10 % spent on equipment
    spend = next(r for r in plain.money_cards(full) if r.title[0] == "What it spends to stay ahead")
    assert spend.value == "25%"
    # without the cash-flow figures the cards that need them are left out; nothing is made up
    assert "Cash left over" not in [r.title[0] for r in plain.money_cards(c)]
    for r in plain.money_cards(full) + plain.price_cards(full):
        assert r.sign in "+-0" and r.title and r.value and r.short and r.text[0] and r.text[1]
        if r.tag is not None:                                               # a fact: a plate of its own, never "for" or "against"
            html = sm.reasons(False, [r])
            assert "· " + r.tag[0] in html and "+ For" not in html and "Against" not in html
    price = plain.price_cards(full)
    assert price[0].title[0] == "The price today" and price[0].value[0].endswith("×") and price[0].value[1].endswith("倍")
    # every reason of the page before is still on it, in one of the two rows
    old = {r.title for r in plain.reasons(c)}
    assert old == {r.title for r in plain.number_cards(c)} | {r.title for r in plain.price_cards(c) if r.tag is None}


def test_the_sec_provider_reads_cash_flow_capital_spending_and_research():
    import inspect
    from datetime import datetime, timezone
    from tickercase.http_client import FetchedJson
    from tickercase.models import DataMode
    from tickercase.providers.sec_facts import SecCompanyFactsProvider

    def rows(value):
        return {"units": {"USD": [{"start": "2025-01-01", "end": "2025-12-31", "val": value, "form": "10-K", "filed": "2026-02-01", "accn": "0000000000-26-000001", "fy": 2025, "fp": "FY"},
                                  {"start": "2025-10-01", "end": "2025-12-31", "val": 1, "form": "10-K", "filed": "2026-02-01", "accn": "0000000000-26-000001"}]}}

    payload = {"entityName": "Example Corp", "facts": {"us-gaap": {
        "Revenues": rows(1000), "NetIncomeLoss": rows(200), "NetCashProvidedByUsedInOperatingActivities": rows(300),
        "PaymentsToAcquireProductiveAssets": rows(50), "ResearchAndDevelopmentExpense": rows(120), "ShareBasedCompensation": rows(30)}, "dei": {}}}
    kw = {"url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json", "payload": payload, "retrieved_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
          "source_captured_at": None, "data_mode": list(DataMode)[0]}
    fetched = FetchedJson(**{k: v for k, v in kw.items() if k in inspect.signature(FetchedJson).parameters})
    facts = SecCompanyFactsProvider(client=None, resolver=object()).parse("EXM", "0000000001", "Example Corp", fetched)
    assert [float(p.value) for p in facts.operating_cash_flow] == [300] and [float(p.value) for p in facts.capital_expenditure] == [50]
    assert [float(p.value) for p in facts.research_development] == [120] and [float(p.value) for p in facts.share_based_compensation] == [30]
    assert facts.capital_expenditure[0].concept == "PaymentsToAcquireProductiveAssets"     # the second concept, when the first is not reported
