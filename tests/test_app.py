"""Page-level tests with Streamlit's AppTest (no browser, no network)."""

from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from conftest import ROOT

APP = str(ROOT / "app.py")
REPORT_PAGES = ("answer", "methods", "market", "events", "fund", "nar", "data")


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    monkeypatch.setenv("TICKERCASE_SNAPSHOT_DIR", str(tmp_path / "snap"))
    monkeypatch.setenv("TICKERCASE_CASE_DIR", str(tmp_path / "cases"))
    monkeypatch.setenv("TICKERCASE_SEC_MODE", "live")
    monkeypatch.setenv("TICKERCASE_BOOT_SCREEN", "0")  # these tests start on the console; the start-up screen has its own tests
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


def click(at, key):
    button(at, key).click()
    return run(at)


def keys(at):
    return {b.key for b in at.button}


def texts(at):
    parts = [e.value for e in at.error] + [w.value for w in at.warning] + [s.value for s in at.success] + [i.value for i in at.info]
    return "\n".join(str(p) for p in parts)


def markdown(at):
    return " ".join(str(m.value) for m in at.markdown)


def synthetic(at):
    """On ① Claim: switch the data source to the synthetic example data."""
    at.radio(key="sec_mode").set_value("synthetic")
    return run(at)


def read(at, sentence):
    """Write the sentence on ① Claim and press READ (which opens ② Check when a ticker was found)."""
    if at.session_state["page"] != "claim":
        click(at, "nav_claim")
    at.text_area(key="f_claim_text").set_value(sentence)
    run(at)
    return click(at, "btn_prefill")


def confirm_and_run(at):
    click(at, "btn_confirm")
    return click(at, "btn_run")


def test_example_confirm_run_and_stale_result_hidden(app_env):
    at = run(AppTest.from_file(APP))
    assert at.session_state["page"] == "claim"
    assert button(at, "nav_run").disabled and button(at, "nav_answer").disabled  # nothing confirmed, no report

    click(at, "btn_example_ps")
    assert at.session_state["sec_mode"] == "synthetic" and at.session_state["page"] == "check"  # an example goes straight to checking
    assert button(at, "btn_run").disabled

    click(at, "btn_confirm")
    assert "已确认" in texts(at)
    assert not button(at, "btn_run").disabled

    click(at, "btn_run")
    result = at.session_state["result"]
    assert result.status.value == "evaluated"
    assert at.session_state["page"] == "result" and at.session_state["rpage"] == "answer"
    assert result.verdict.label == "partially_supported"
    assert result.probability is not None and result.probability.status == "ok"  # P/S example asks for it
    click(at, "nav_fund")
    assert "部分支持" in markdown(at)  # the FND panel
    assert any(df.value.shape[0] >= 5 for df in at.dataframe)  # calculations table

    # back on ② Check, edit an input: the confirmation becomes stale, the old report is hidden, RUN is off
    click(at, "nav_check")
    assert "已确认" in texts(at) and "btn_open" in keys(at)  # the report is still current
    at.text_input(key="f_target_price").set_value("120")
    run(at)
    page = texts(at)
    assert "原确认失效" in page and "已隐藏" in page
    assert button(at, "btn_run").disabled and button(at, "nav_answer").disabled
    assert not at.dataframe


def test_steps_move_with_keys_and_back_next(app_env):
    at = run(AppTest.from_file(APP))
    click(at, "nav_check")
    assert at.session_state["page"] == "check" and "btn_confirm" in keys(at) and "btn_prefill" not in keys(at)
    click(at, "nav_prev")
    assert at.session_state["page"] == "claim"
    click(at, "btn_example_ps")
    confirm_and_run(at)
    assert at.query_params["p"] == ["answer"] and at.query_params["case"] == [at.session_state["result"].case_id]
    for page in REPORT_PAGES[1:]:  # next ▶ walks the seven pages in order
        click(at, "nav_next")
        assert at.session_state["rpage"] == page and at.query_params["p"] == [page]
    assert "nav_next" not in keys(at)
    click(at, "nav_market")  # any page by its key
    assert at.session_state["rpage"] == "market" and "大盘" in markdown(at)
    click(at, "nav_answer")
    click(at, "nav_prev")  # back from ① Answer is ② Check
    assert at.session_state["page"] == "check" and "btn_open" in keys(at)
    click(at, "btn_open")
    assert at.session_state["page"] == "result" and at.session_state["rpage"] == "answer"


def test_reload_reopens_the_same_report_page(app_env, monkeypatch):
    at = run(AppTest.from_file(APP))
    click(at, "btn_example_ps")
    confirm_and_run(at)
    case_id = at.session_state["result"].case_id
    monkeypatch.setenv("TICKERCASE_BOOT_SCREEN", "1")  # a reload is a new session; the start-up screen is skipped for a report address
    import streamlit as st

    st.cache_resource.clear()
    again = AppTest.from_file(APP)
    again.query_params["case"] = case_id
    again.query_params["p"] = "events"
    run(again)
    assert again.session_state["view"] == "history" and again.session_state["rpage"] == "events"
    assert "btn_boot_enter" not in keys(again) and "情景推演" in markdown(again)
    bad = AppTest.from_file(APP)
    bad.query_params["case"] = "not-a-case"
    run(bad)
    assert "btn_boot_enter" in keys(bad)  # nothing to reopen: the normal start


def test_data_failure_shows_error_and_keeps_calculations(app_env):
    at = run(AppTest.from_file(APP))
    click(at, "btn_example_ps")
    click(at, "nav_claim")
    at.radio(key="sec_mode").set_value("replay")  # empty snapshot dir -> every provider fails, no network
    run(at)
    click(at, "nav_check")
    confirm_and_run(at)
    result = at.session_state["result"]
    assert result.status.value == "evaluated_with_provider_errors"
    assert result.evidence_records == [] and result.reported_facts is None and result.market is None
    assert result.verdict.label == "insufficiently_specified"
    assert "● " in button(at, "nav_data").label  # the data page key carries the warning count
    click(at, "nav_data")
    page = texts(at)
    assert "snapshot_missing" in page and "本次未能从" in page  # Chinese is the default language
    click(at, "nav_fund")
    assert any(df.value.shape[0] >= 5 for df in at.dataframe)  # calculations still shown


def test_prefill_fills_reference_values_with_sources(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    click(at, "nav_check")
    at.text_input(key="f_ticker").set_value("SYNT")
    run(at)
    click(at, "btn_refill")
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
    assert any("◇ 默认假设" in e.label for e in at.expander)  # the folded inputs say how many defaults they hold


def test_claim_only_flow_reaches_a_verdict(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    click(at, "nav_check")  # typed by hand first, then the sentence
    at.text_input(key="f_ticker").set_value("SYNT")
    at.text_input(key="f_target_price").set_value("100")
    at.text_input(key="f_horizon_years").set_value("5")
    run(at)
    read(at, "SYNT reaches $100 by 2031")
    assert at.session_state["page"] == "check"
    confirm_and_run(at)
    result = at.session_state["result"]
    assert result.verdict is not None and result.sensitivity is not None
    prov = result.confirmed_claim.value_provenance
    assert prov["valuation_multiple"].startswith("default_assumption:")
    assert prov["reference_price"].startswith("public_data:")
    assert prov["target_price"] == "user_input:claim"


def test_out_of_range_horizon_shows_the_limit(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    click(at, "nav_check")
    at.text_input(key="f_ticker").set_value("SYNT")
    at.text_input(key="f_target_price").set_value("100")
    at.text_input(key="f_horizon_years").set_value("0.003")
    run(at)
    read(at, "SYNT reaches $100 in one day")
    assert "太短，最短约 1 个月" in texts(at)
    at.text_input(key="f_horizon_years").set_value("12")
    run(at)
    assert "太长，最长 10 年" in texts(at)


def _claim_fields(at):
    return {k: at.session_state[f"f_{k}"] for k in ("claim_text", "ticker", "target_price", "horizon_years")}


def test_new_claim_after_a_finished_case_starts_empty(app_env):
    # ◀ NEW used to keep the old case's inputs, so a hand-edited horizon silently beat the new sentence
    at = synthetic(run(AppTest.from_file(APP)))
    read(at, "SYNT reaches $100 by 2031")
    at.text_input(key="f_horizon_years").set_value("4")
    run(at)
    confirm_and_run(at)
    assert at.session_state["page"] == "result"
    click(at, "btn_new")
    assert at.session_state["page"] == "claim" and at.session_state["result"] is None
    assert set(_claim_fields(at).values()) == {""} and at.session_state["sec_mode"] == "synthetic"
    assert "已隐藏" not in texts(at)
    read(at, "SYNT will trade at $600 in two years.")
    assert _claim_fields(at) | {"claim_text": ""} == {"claim_text": "", "ticker": "SYNT", "target_price": "600", "horizon_years": "2"}


def test_replacing_the_sentence_drops_old_values_and_names_kept_ones(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    read(at, "SYNT will trade at $100 in five years.")
    assert at.session_state["f_horizon_years"] == "5"
    # a sentence without a time frame: the horizon read from the old sentence is cleared, not carried over
    read(at, "SYNT will trade at $120.")
    assert at.session_state["f_horizon_years"] == "" and at.session_state["f_target_price"] == "120"
    # a value the user typed stays, and the message says what the sentence would have given
    at.text_input(key="f_horizon_years").set_value("4")
    run(at)
    read(at, "SYNT will trade at $150 in two years.")
    assert at.session_state["f_horizon_years"] == "4" and at.session_state["f_target_price"] == "150"
    assert "保留了你手动填写的" in texts(at) and "原文是 2" in texts(at)


def test_user_assumption_is_not_overwritten_by_prefill(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    click(at, "nav_check")
    at.text_input(key="f_ticker").set_value("SYNT")
    at.text_input(key="f_valuation_multiple").set_value("30")
    run(at)
    click(at, "btn_refill")
    assert at.session_state["f_valuation_multiple"] == "30"


def test_read_without_a_ticker_stays_on_the_claim(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    click(at, "btn_prefill")
    assert at.session_state["page"] == "claim" and "先写一句观点" in texts(at)


def test_english_switch_and_history_view(app_env):
    at = run(AppTest.from_file(APP))
    click(at, "btn_example_ps")
    confirm_and_run(at)
    at.radio(key="lang").set_value("en")
    run(at)
    click(at, "nav_fund")
    assert "Partially Supported" in markdown(at)
    click(at, "nav_check")  # back to the inputs: the confirmation is shown there
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
    click(at, "btn_sec_save")
    assert not (app_env / ".env").exists()
    at.text_input(key="sec_email_input").set_value("me@example.org")
    run(at)
    click(at, "btn_sec_save")
    assert (app_env / ".env").read_text(encoding="utf-8") == 'SEC_USER_AGENT="TickerCase/0.2 me@example.org"\n'


def test_setup_page_holds_the_settings_and_the_guide(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="view").set_value("sys")
    run(at)
    page = markdown(at)
    assert "CH-10 NEWS" in page and "M4" in page  # the ten sources and the four methods
    assert at.radio(key="sec_mode") and "sec_email_input" in {x.key for x in at.text_input}
    assert at.query_params["v"] == ["sys"]


def test_pe_example_not_supported(app_env):
    at = run(AppTest.from_file(APP))
    click(at, "btn_example_pe")
    confirm_and_run(at)
    result = at.session_state["result"]
    assert result.verdict.label == "not_supported_today"
    assert result.probability is None


def test_invalid_input_cannot_be_confirmed(app_env):
    at = run(AppTest.from_file(APP))
    click(at, "btn_example_pe")
    at.text_input(key="f_reference_price").set_value("NaN")
    run(at)
    assert "不能是 NaN" in texts(at)
    click(at, "btn_confirm")
    assert "未确认" in texts(at)
    assert button(at, "btn_run").disabled and button(at, "nav_run").disabled


def test_example_values_are_replaced_by_prefill_and_custom_choices_are_kept(app_env):
    at = run(AppTest.from_file(APP))
    click(at, "btn_example_pe")  # loads an absolute target share count of 100M
    assert at.session_state["f_share_mode"] == "absolute"
    click(at, "btn_refill")
    assert at.session_state["f_share_mode"] == "trend" and at.session_state["f_target_assumed_shares"] == ""
    assert at.session_state["f_share_rate_pct"] == "1.04"
    # a choice the user made stays
    at.selectbox(key="f_share_mode").set_value("rate")
    run(at)
    at.text_input(key="f_share_rate_pct").set_value("-2")
    run(at)
    click(at, "btn_refill")
    assert at.session_state["f_share_mode"] == "rate" and at.session_state["f_share_rate_pct"] == "-2"
    confirm_and_run(at)
    claim = at.session_state["result"].confirmed_claim
    assert claim.values.share_change_rate == Decimal("-0.02") and claim.values.target_shares_derived
    assert claim.value_provenance["target_assumed_shares"].startswith("derived:")


def test_one_sentence_is_enough(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    read(at, "SYNT 五年后股价翻倍")
    assert at.session_state["page"] == "check"
    assert at.session_state["f_ticker"] == "SYNT"
    assert at.session_state["f_horizon_years"] == "5"
    assert at.session_state["f_target_price"] == "100"  # "翻倍" x reference price 50
    assert "从原文识别" in texts(at)
    confirm_and_run(at)
    result = at.session_state["result"]
    assert result.report is not None and len(result.report.layers) == 5
    assert result.oracle is not None and result.oracle.low is not None
    assert result.confirmed_claim.value_provenance["ticker"].startswith("claim_text:")
    click(at, "nav_nar")
    page = markdown(at)
    assert "第 1 层" in page and "情景推演" in page and "需要关注的信号" in page


def test_touch_claim_and_scenario_panel(app_env):
    at = synthetic(run(AppTest.from_file(APP)))
    read(at, "SYNT 3年内冲到100")
    assert at.session_state["f_price_condition"] == "touch"
    confirm_and_run(at)
    result = at.session_state["result"]
    assert result.oracle.condition == "touch" and result.confirmed_claim.value_provenance["price_condition"].startswith("claim_text:")
    assert "任意时点触及" in markdown(at)  # ① Answer
    click(at, "nav_events")
    page = markdown(at)
    assert "要让概率达到 50%" in page or "即使" in page
    assert at.slider(key="sc_p_current").value == 50
    at.slider(key="sc_p_current").set_value(90)
    run(at)
    assert at.slider(key="sc_p_current").value == 90


def _cjk(text: str) -> list[str]:
    return [ch for ch in text if "一" <= ch <= "鿿"]


def _shown(at) -> list[str]:
    shown = [m.value for m in at.markdown] + [c.value for c in at.caption] + [b.label for b in at.button] + [str(x.value) for x in at.info]
    shown += [str(x.value) for x in at.warning] + [x.label for x in at.radio] + [x.label for x in at.text_input] + [x.label for x in at.selectbox]
    shown += [x.label for x in at.slider] + [x.label for x in at.expander]
    shown += [o for x in at.radio if x.key != "lang" for o in x.options] + [o for x in at.selectbox for o in x.options]  # the language switch names each language in itself
    return shown


def test_english_pages_show_no_chinese(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="lang").set_value("en")
    synthetic(run(at))
    read(at, "SYNT will hit $100 within 3 years")
    confirm_and_run(at)
    for page in REPORT_PAGES:
        click(at, f"nav_{page}")
        leaks = [s for s in _shown(at) if _cjk(s)]
        assert not leaks, (page, leaks[:5])


def test_console_in_english_shows_no_chinese(app_env):
    at = run(AppTest.from_file(APP))
    at.radio(key="lang").set_value("en")
    run(at)
    for step in ("claim", "example", "sys"):
        if step == "example":
            click(at, "btn_example_ps")
        elif step == "sys":
            at.radio(key="view").set_value("sys")
            run(at)
        leaks = [s for s in _shown(at) if _cjk(s)]
        assert not leaks, (step, leaks[:5])


def test_tape_on_the_console_opens_its_report(app_env):
    at = run(AppTest.from_file(APP))
    click(at, "btn_example_ps")
    confirm_and_run(at)
    case_id = at.session_state["result"].case_id
    click(at, "btn_new")
    click(at, f"btn_tape_{case_id}")
    assert at.session_state["view"] == "history" and at.session_state["rpage"] == "answer"
    assert "nav_check" not in keys(at)  # a stored tape has report pages only
    click(at, "nav_fund")
    assert "部分支持" in markdown(at)
    click(at, "nav_nar")
    assert f"btn_narrate_{case_id}" in keys(at)


def test_start_up_screen_comes_first_and_enter_opens_the_console(app_env, monkeypatch):
    monkeypatch.setenv("TICKERCASE_BOOT_SCREEN", "1")
    at = run(AppTest.from_file(APP))
    assert "btn_boot_enter" in keys(at) and "btn_prefill" not in keys(at)
    page = markdown(at)
    assert "TICKERCASE" in page and "自检" in page and "0 盘" in page  # empty case store in the test folder
    assert "未设置" in page and "受限" in page and "tc-l-red" in page  # live mode without an SEC email: the warning lamp is red
    click(at, "btn_boot_replay")
    assert at.session_state["boot_n"] == 1 and "btn_boot_enter" in keys(at)
    click(at, "btn_boot_enter")
    assert "btn_prefill" in keys(at) and "btn_boot_enter" not in keys(at)
    run(at)  # the start-up screen shows once per session
    assert "btn_boot_enter" not in keys(at)


def test_start_up_screen_in_english(app_env, monkeypatch):
    monkeypatch.setenv("TICKERCASE_BOOT_SCREEN", "1")
    at = run(AppTest.from_file(APP))
    at.radio(key="lang").set_value("en")
    run(at)
    shown = [m.value for m in at.markdown] + [b.label for b in at.button] + [at.radio(key="lang").label]
    assert any("SELF-TEST" in s for s in shown)
    leaks = [s for s in shown if _cjk(s)]
    assert not leaks, leaks[:3]


def test_narrative_shows_current_language_and_reuse(app_env):
    from datetime import datetime, timezone

    from tickercase.models import Narrative, NarrativeSentence

    at = run(AppTest.from_file(APP))
    click(at, "btn_example_ps")
    confirm_and_run(at)
    click(at, "nav_nar")
    assert "btn_narrate_current" in keys(at)  # nothing written yet: the write button
    when = datetime(2026, 10, 6, tzinfo=timezone.utc)
    zh_n = Narrative(status="ok", model="claude-opus-5-5", effort="medium", created_at=when, language="zh", total=1, verified=1,
                     sections={"conclusion": [NarrativeSentence(zh="中文结论句。")]}, reused_from="a" * 32,
                     usage={"input_tokens": 4000, "output_tokens": 1500})
    at.session_state["result"].narratives["zh"] = zh_n
    run(at)
    page = "\n".join(str(m.value) for m in at.markdown) + "\n".join(str(c.value) for c in at.caption)
    assert "中文结论句" in page and "复用案例 aaaaaaaa" in page


def test_stored_narrative_is_shown_with_todays_verdicts(app_env):
    from datetime import datetime, timezone

    from tickercase.models import Fact, Narrative, NarrativeSentence

    at = run(AppTest.from_file(APP))
    click(at, "btn_example_ps")
    confirm_and_run(at)
    click(at, "nav_nar")
    facts = [Fact(id="F01", label_en="insider open-market sale value", label_zh="内部人卖出金额", value="1430996244.02", unit="USD")]
    old = Narrative(status="ok", model="claude-opus-5-5", created_at=datetime(2026, 10, 5, tzinfo=timezone.utc), language="zh",
                    sections={"divergences": [NarrativeSentence(zh="内部人卖出约 14.3 亿美元。", fact_ids=["F01"], status="unsupported",
                                                                problems=["old rules"])]},
                    facts=facts, total=1, verified=0, unsupported=1)
    at.session_state["result"].narratives["zh"] = old
    run(at)
    assert "1/1 句通过核对 · 0 句含无出处数字" in markdown(at) and "已按当前核对规则重新核对（写成时为 0/1）" in markdown(at)
