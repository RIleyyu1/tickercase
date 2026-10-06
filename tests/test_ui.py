"""Cockpit page parts (v0.9): HTML built from a finished synthetic case in both languages, and the K-line page."""

import json
import re
from datetime import date, timedelta

import pytest

from tickercase.models import Candle, KLine, Narrative, NarrativeSentence
from tickercase.service import CaseService
from tickercase.ui import cockpit as ck
from tickercase.ui.kline import kline_html
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, ps_draft

STEPS = {"options": ("期权链", "Option chain"), "price_history": ("日线行情", "Daily prices")}


def _cjk(text: str) -> list[str]:
    return [ch for ch in text if "一" <= ch <= "鿿"]


def _text(markup: str) -> str:
    return re.sub(r"<[^>]+>", " ", markup)


@pytest.fixture
def case(tmp_path):
    svc = CaseService(make_settings(tmp_path), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    draft = ps_draft(ticker="SYNT", current_shares="95000000", price_condition="touch", claim_text="SYNT will hit $100 within 3 years")
    return svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")


def _all_parts(case, zh: bool) -> str:
    return "".join([
        ck.bridge(zh, sys_on=True, link="green", warn=0, chips=["TC 2026-10-06"]),
        ck.status_bar(case, zh, [("u-clm", "CLM")]),
        ck.claim_unit(case, zh, "UNIT 01"),
        ck.prb_unit(case, zh, "UNIT 03", anim=True),
        ck.feeds_unit(case, zh, "UNIT 04", anim=False),
        ck.sig_unit(case, zh, "UNIT 06", notes=["a note"]),
        ck.fnd_unit(case, zh, "FND", "CH4"),
        ck.pipeline(case.data_steps, zh, STEPS),
        ck.keyswitch(True, zh),
        ck.tape(case, zh),
        ck.hero(zh),
        ck.feed_info(zh, "UNIT 03"),
        ck.methods_info(zh, "UNIT 04"),
        ck.footer(zh),
        ck.src_line("def", "today's P/S", zh),
    ])


def test_units_render_in_both_languages_and_english_has_no_chinese(case):
    zh = _all_parts(case, True)
    assert "期间任意时点触及" in zh and "TC-8 型" in zh and case.case_id[:8].upper() in zh
    en = _all_parts(case, False)
    assert "Touch at any time" in en
    leaks = _cjk(_text(en))
    assert not leaks, leaks[:10]


def test_probability_unit_lights_the_range_and_keeps_m3_as_context_for_touch(case):
    o = case.oracle
    assert o.condition == "touch" and o.low is not None
    html = ck.prb_unit(case, True, "PRB", anim=False)
    assert f"{o.low * 100:.0f}%–{o.high * 100:.0f}%" in html
    assert " lit" in html and "tc-boot" not in html
    used, ref = ck.split_methods(case)
    assert used and all(o.low - 1e-12 <= p <= o.high + 1e-12 for _, p in used)
    assert "M3" not in {m.id for m, _ in used}  # peer base rate does not measure a touch
    m3 = next(m for m in o.methods if m.id == "M3")
    strip = html.split(">M3<", 1)[1].split("tc-basis", 1)[0]
    assert ("参考" in strip) if m3.status == "ok" else ("缺数据" in strip)
    assert ck.sqrt_x(0.25) == 50 and ck.sqrt_x(-1) == 0 and ck.sqrt_x(2) == 100


def test_the_other_condition_comes_from_the_market_methods(case):
    rng, detail = ck.other_side(case, False)
    m1 = next(m for m in case.oracle.methods if m.id == "M1")
    if m1.status == "ok":
        assert rng.endswith("%") and "options" in detail
    touch_text, end_text = ck.kline_notes(case, True)
    assert touch_text.startswith("期间触及") and ck.tr(case.oracle.tier_label, True) in touch_text
    assert end_text == "" or end_text.startswith("到期收在")


def test_warnings_are_data_problems_only(case):
    assert ck.warnings_of(case, True) == []  # synthetic data: every source answered
    assert 'WARN 0' in ck.status_bar(case, False, [])
    sig = ck.sig_unit(case, False, "SIG", notes=["SEC XBRL: two concepts merged"])
    assert "tc-l-red" not in sig and "NOTE" in sig and "WARN 0" in sig


def test_dollar_amounts_cannot_turn_into_inline_math(case):
    # Streamlit's markdown reads "$...$" as math even inside an HTML block; two amounts in one unit broke the FEED cards
    assert ck.esc("sold about $1.43B / close above $240") == "sold about &#36;1.43B / close above &#36;240"
    assert "$" not in ck.feeds_unit(case, False, "FEED", anim=False) and "$" not in _all_parts(case, False)


def test_formats():
    assert ck.amount(215_938_000_000, True) == "2,159.38 亿" and ck.amount(215_938_000_000, False) == "215.94B"
    assert ck.amount(None, True) == "—" and ck.amount(1_000_000_000_000, False) == "1T"
    assert ck.pct(0.7263, 0) == "73%" and ck.pct(None) == "—"
    css = ck.page_css([("u-b-clm", 1), ("u-c-kln", 2)], flowing=True)
    assert '.st-key-u-b-clm::after{content:"1"}' in css and "tc-fdown" in css


def test_printer_paper_stamps_every_sentence():
    n = Narrative(status="ok", model="m", created_at=FIXED_NOW, language="zh", sections={
        "conclusion": [NarrativeSentence(zh="结论句。", status="verified", fact_ids=["F1"]), NarrativeSentence(zh="没出处的 42%。", status="unsupported")]})
    zh = ck.nar_paper(n, True, anim=True)
    assert "结论" in zh and "结论句。" in zh and ">核<" in zh and "tc-seal-bad" in zh and "[F1]" in zh and "tc-feed" in zh
    en = ck.nar_paper(n.model_copy(update={"sections": {"conclusion": [NarrativeSentence(en="A sentence.", status="verified")]}}), False, anim=False)
    assert ">OK<" in en and "Conclusion" in en and not _cjk(_text(en))


def _cfg(page: str) -> dict:
    return json.loads(re.search(r"const C = (.*);\nconst L = C\.L;", page).group(1))


def test_kline_page_draws_a_close_line_when_the_source_has_closes_only(case):
    m = case.market
    assert m.kline is None  # synthetic data has closes only
    page = kline_html(m, target=100.0, deadline=case.oracle.target_date, zh=False, ticker="SYNT", last_day=m.last_date)
    cfg = _cfg(page)
    assert cfg["series"]["mode"] == "line" and cfg["series"]["data"]["L"][-1][0] == m.last_date.isoformat()
    assert cfg["years"] > 0 and not _cjk(json.dumps(cfg["L"], ensure_ascii=False))
    assert '<html lang="en">' in page and "--ph:#FFB23E" not in page


def test_kline_page_with_candles_escapes_data_and_follows_the_tube(case):
    first = date(2026, 1, 2)
    bars = [Candle(day=first + timedelta(days=i), open=10 + i, high=12 + i, low=9 + i, close=11 + i, volume=1000) for i in range(30)]
    m = case.market.model_copy(update={"kline": KLine(daily=bars, weekly=bars[-5:], monthly=[bars[10], bars[19]])})
    page = kline_html(m, target=100.0, deadline=date(2029, 1, 1), zh=True, ticker="SYNT", last_day=bars[19].day,
                      touch_text="</script><b>x", phosphor="amb")
    assert "</script><b>" not in page  # data cannot close the script block
    cfg = _cfg(page)
    assert cfg["series"]["mode"] == "candles" and set(cfg["series"]["data"]) == {"D", "M"}  # bars after last_day are dropped
    assert cfg["series"]["data"]["D"][-1] == [bars[19].day.isoformat(), 29, 31, 28, 30, 1000]
    assert cfg["touch"] == "</script><b>x" and "--ph:#FFB23E" in page and '<html lang="zh-CN">' in page
