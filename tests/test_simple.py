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
    assert {"btn_s_detail", "btn_s_again"} <= keys(at)
    # the detailed report is the console's seven pages
    click(at, "btn_s_detail")
    assert at.session_state["ui"] == "expert" and "nav_methods" in keys(at)


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

    assert abs(sum(METHOD_WEIGHTS.values()) - 1) < 1e-9 and METHOD_WEIGHTS["M4"] + METHOD_WEIGHTS["M3"] <= 0.25
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
            assert market >= 0.75                                # the two market methods carry the number
    assert seen


def test_weights_are_shared_out_again_when_a_method_is_missing():
    from tickercase.oracle import weighted_index

    index, weights = weighted_index({"M2": 0.30, "M4": 0.60})
    assert abs(sum(weights.values()) - 1) < 1e-9 and weights["M2"] > weights["M4"]
    assert abs(index - (0.40 * 0.30 + 0.15 * 0.60) / 0.55) < 1e-9
    assert weighted_index({"M1": 0.2})[0] == 0.2


def test_the_backtest_line_reads_between_two_bins_and_is_silent_outside_them():
    from tickercase import calibration

    data = calibration.table()
    assert data is not None and data["cases"] > 10000 and data["weights"]["M4"] == 0.15
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
