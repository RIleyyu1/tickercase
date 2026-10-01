"""Page-level tests with Streamlit's AppTest (no browser, no network)."""

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
    # empty assumptions get visible defaults: today's share count and today's multiple
    assert at.session_state["f_target_assumed_shares"] == "95000000"
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
