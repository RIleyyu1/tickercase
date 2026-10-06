"""Page-level tests with Streamlit's AppTest (no browser, no network)."""

from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from conftest import ROOT

APP = str(ROOT / "app.py")


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    monkeypatch.setenv("TICKERCASE_SNAPSHOT_DIR", str(tmp_path / "snap"))
    monkeypatch.setenv("TICKERCASE_CASE_DIR", str(tmp_path / "cases"))
    monkeypatch.setenv("TICKERCASE_SEC_MODE", "live")
    # the cached service must not leak between tests
    import streamlit as st

    st.cache_resource.clear()
    return tmp_path


def run(at):
    at.run(timeout=30)
    assert not at.exception, at.exception
    return at


def button(at, key):
    return next(b for b in at.button if b.key == key)


def texts(at):
    parts = [e.value for e in at.error] + [w.value for w in at.warning] + [s.value for s in at.success] + [i.value for i in at.info]
    return "\n".join(str(p) for p in parts)


def test_example_confirm_run_and_stale_result_hidden(app_env):
    at = run(AppTest.from_file(APP))
    assert button(at, "btn_run").disabled  # nothing confirmed yet

    button(at, "btn_example_ps").click()
    run(at)
    assert at.session_state["sec_mode"] == "synthetic"
    assert button(at, "btn_run").disabled

    button(at, "btn_confirm").click()
    run(at)
    assert "已确认" in texts(at)
    assert not button(at, "btn_run").disabled

    button(at, "btn_run").click()
    run(at)
    result = at.session_state["result"]
    assert result.status.value == "evaluated"
    assert result.verdict.label == "partially_supported"
    assert any("部分支持" in m.value for m in at.markdown)  # verdict banner
    assert any(df.value.shape[0] >= 5 for df in at.dataframe)  # calculations table rendered
    assert result.probability is not None and result.probability.status == "ok"  # P/S example asks for it

    # edit an input: confirmation becomes stale, old result is hidden, run disabled
    at.text_input(key="f_target_price").set_value("120")
    run(at)
    page = texts(at)
    assert "原确认失效" in page and "已隐藏" in page
    assert button(at, "btn_run").disabled
    assert not at.dataframe


def test_data_failure_shows_error_and_keeps_calculations(app_env):
    at = run(AppTest.from_file(APP))
    button(at, "btn_example_ps").click()
    run(at)
    at.radio(key="sec_mode").set_value("replay")  # empty snapshot dir -> every provider fails, no network
    run(at)
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    result = at.session_state["result"]
    assert result.status.value == "evaluated_with_provider_errors"
    assert result.evidence_records == [] and result.reported_facts is None and result.market is None
    assert result.verdict.label == "insufficiently_specified"
    page = texts(at)
    assert "snapshot_missing" in page and "本次未能从" in page  # Chinese is the default language
    assert any(df.value.shape[0] >= 5 for df in at.dataframe)  # calculations still shown


def test_prefill_fills_reference_values_with_sources(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="sec_mode").set_value("synthetic")
    at.text_input(key="f_ticker").set_value("SYNT")
    run(at)
    button(at, "btn_prefill").click()
    run(at)
    assert at.session_state["f_reference_price"] == "50"
    assert at.session_state["f_current_shares"] == "95000000"
    assert at.session_state["f_base_annual_metric"] == "200000000"  # P/S -> revenue
    # empty assumptions get visible defaults: the reported share trend and today's multiple
    assert at.session_state["f_share_mode"] == "trend"
    assert at.session_state["f_share_rate_pct"] == "1.04"  # 92.1M (2023-07-28) -> 95.0M (2026-07-24)
    assert at.session_state["f_valuation_multiple"] == "23.75"  # 50 x 95M / 200M
    at.selectbox(key="f_valuation_method").set_value("price_to_earnings")
    run(at)
    assert at.session_state["f_base_annual_metric"] == "40000000"  # switched to net income
    assert at.session_state["f_valuation_multiple"] == "118.75"  # today's P/E
    assert "已补全" in texts(at) and "默认假设" in texts(at)


def test_claim_only_flow_reaches_a_verdict(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="sec_mode").set_value("synthetic")
    at.text_area(key="f_claim_text").set_value("SYNT reaches $100 by 2031")
    at.text_input(key="f_ticker").set_value("SYNT")
    at.text_input(key="f_target_price").set_value("100")
    at.text_input(key="f_horizon_years").set_value("5")
    run(at)
    button(at, "btn_prefill").click()
    run(at)
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    result = at.session_state["result"]
    assert result.verdict is not None and result.sensitivity is not None
    prov = result.confirmed_claim.value_provenance
    assert prov["valuation_multiple"].startswith("default_assumption:")
    assert prov["reference_price"].startswith("public_data:")
    assert prov["target_price"] == "user_input:claim"


def test_user_assumption_is_not_overwritten_by_prefill(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="sec_mode").set_value("synthetic")
    at.text_input(key="f_ticker").set_value("SYNT")
    at.text_input(key="f_valuation_multiple").set_value("30")
    run(at)
    button(at, "btn_prefill").click()
    run(at)
    assert at.session_state["f_valuation_multiple"] == "30"


def test_english_switch_and_history_view(app_env):
    at = run(AppTest.from_file(APP))
    button(at, "btn_example_ps").click()
    run(at)
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    at.radio(key="lang").set_value("en")
    run(at)
    assert any("Partially Supported" in m.value for m in at.markdown)
    assert any("Confirmed" in s.value for s in at.success)
    at.radio(key="view").set_value("history")
    run(at)
    assert at.dataframe and at.dataframe[0].value.shape[0] >= 1


def test_sec_contact_form_saves_to_env(app_env, monkeypatch):
    import tickercase.config as config

    monkeypatch.setattr(config, "REPO_ROOT", app_env)  # never touch the real .env
    at = run(AppTest.from_file(APP))
    assert at.radio(key="sec_mode").value == "live"
    at.text_input(key="sec_email_input").set_value("not-an-email")
    run(at)
    button(at, "btn_sec_save").click()
    run(at)
    assert not (app_env / ".env").exists()
    at.text_input(key="sec_email_input").set_value("me@example.org")
    run(at)
    button(at, "btn_sec_save").click()
    run(at)
    assert (app_env / ".env").read_text(encoding="utf-8") == 'SEC_USER_AGENT="TickerCase/0.2 me@example.org"\n'


def test_pe_example_not_supported(app_env):
    at = run(AppTest.from_file(APP))
    button(at, "btn_example_pe").click()
    run(at)
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    result = at.session_state["result"]
    assert result.verdict.label == "not_supported_today"
    assert result.probability is None


def test_invalid_input_cannot_be_confirmed(app_env):
    at = run(AppTest.from_file(APP))
    button(at, "btn_example_pe").click()
    run(at)
    at.text_input(key="f_reference_price").set_value("NaN")
    run(at)
    assert "不能是 NaN" in texts(at)
    button(at, "btn_confirm").click()
    run(at)
    assert "未确认" in texts(at)
    assert button(at, "btn_run").disabled


def test_example_values_are_replaced_by_prefill_and_custom_choices_are_kept(app_env):
    at = run(AppTest.from_file(APP))
    button(at, "btn_example_pe").click()  # loads an absolute target share count of 100M
    run(at)
    assert at.session_state["f_share_mode"] == "absolute"
    button(at, "btn_prefill").click()
    run(at)
    assert at.session_state["f_share_mode"] == "trend" and at.session_state["f_target_assumed_shares"] == ""
    assert at.session_state["f_share_rate_pct"] == "1.04"
    # a choice the user made stays
    at.selectbox(key="f_share_mode").set_value("rate")
    run(at)
    at.text_input(key="f_share_rate_pct").set_value("-2")
    run(at)
    button(at, "btn_prefill").click()
    run(at)
    assert at.session_state["f_share_mode"] == "rate" and at.session_state["f_share_rate_pct"] == "-2"
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    claim = at.session_state["result"].confirmed_claim
    assert claim.values.share_change_rate == Decimal("-0.02") and claim.values.target_shares_derived
    assert claim.value_provenance["target_assumed_shares"].startswith("derived:")


def test_one_sentence_is_enough(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="sec_mode").set_value("synthetic")
    at.text_area(key="f_claim_text").set_value("SYNT 五年后股价翻倍")
    run(at)
    button(at, "btn_prefill").click()
    run(at)
    assert at.session_state["f_ticker"] == "SYNT"
    assert at.session_state["f_horizon_years"] == "5"
    assert at.session_state["f_target_price"] == "100"  # "翻倍" x reference price 50
    assert "从原文识别" in texts(at)
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    result = at.session_state["result"]
    assert result.report is not None and len(result.report.layers) == 5
    assert result.oracle is not None and result.oracle.low is not None
    assert result.confirmed_claim.value_provenance["ticker"].startswith("claim_text:")
    page = " ".join(m.value for m in at.markdown)
    assert "第 1 层" in page and "情景推演" in page and "需要关注的信号" in page


def test_touch_claim_and_scenario_panel(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="sec_mode").set_value("synthetic")
    at.text_area(key="f_claim_text").set_value("SYNT 3年内冲到100")
    run(at)
    button(at, "btn_prefill").click()
    run(at)
    assert at.session_state["f_price_condition"] == "touch"
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    result = at.session_state["result"]
    assert result.oracle.condition == "touch" and result.confirmed_claim.value_provenance["price_condition"].startswith("claim_text:")
    page = " ".join(m.value for m in at.markdown)
    assert "任意时点触及" in page and "要让概率达到 50%" in page or "即使" in page
    assert at.slider(key="sc_p_current").value == 50
    at.slider(key="sc_p_current").set_value(90)
    run(at)
    assert at.slider(key="sc_p_current").value == 90


def _cjk(text: str) -> list[str]:
    return [ch for ch in text if "一" <= ch <= "鿿"]


def test_english_page_shows_no_chinese(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="lang").set_value("en")
    at.radio(key="sec_mode").set_value("synthetic")
    at.text_area(key="f_claim_text").set_value("SYNT will hit $100 within 3 years")
    run(at)
    button(at, "btn_prefill").click()
    run(at)
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    shown = [m.value for m in at.markdown] + [c.value for c in at.caption] + [b.label for b in at.button] + [str(x.value) for x in at.info]
    shown += [x.label for x in at.radio] + [x.label for x in at.text_input] + [x.label for x in at.slider] + [x.label for x in at.expander]
    shown += [o for x in at.radio if x.key != "lang" for o in x.options]  # the language switch names each language in itself
    leaks = [s for s in shown if _cjk(s)]
    assert not leaks, leaks[:5]


def test_narrative_shows_current_language_and_reuse(app_env):
    from datetime import datetime, timezone

    from tickercase.models import Narrative, NarrativeSentence

    at = run(AppTest.from_file(APP))
    button(at, "btn_example_ps").click()
    run(at)
    button(at, "btn_confirm").click()
    run(at)
    button(at, "btn_run").click()
    run(at)
    assert any(b.key == "btn_narrate_current" for b in at.button)  # nothing written yet: the write button
    when = datetime(2026, 10, 6, tzinfo=timezone.utc)
    zh_n = Narrative(status="ok", model="claude-opus-5-5", effort="medium", created_at=when, language="zh", total=1, verified=1,
                     sections={"conclusion": [NarrativeSentence(zh="中文结论句。")]}, reused_from="a" * 32,
                     usage={"input_tokens": 4000, "output_tokens": 1500})
    at.session_state["result"].narratives["zh"] = zh_n
    run(at)
    page = "\n".join(str(m.value) for m in at.markdown) + "\n".join(str(c.value) for c in at.caption)
    assert "中文结论句" in page and "复用案例 aaaaaaaa" in page
