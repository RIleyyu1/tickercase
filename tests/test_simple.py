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
    for text in head.range_text:
        assert plain.pct(case.oracle.low) in text


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
