"""Claim extraction rules and the plain-language report. All data here is SYNTHETIC."""

from datetime import date
from decimal import Decimal

import pytest

from tickercase.extract import cn_to_number, extract_claim
from tickercase.service import CaseService
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, pe_draft, ps_draft

TODAY = date(2026, 10, 2)
KNOWN = {"TSLA", "SYNT", "NVDA", "AAPL", "RKLB", "BRK-B"}


def ex(text):
    return extract_claim(text, today=TODAY, is_known_ticker=lambda s: s in KNOWN or s.replace(".", "-") in KNOWN)


@pytest.mark.parametrize("text,ticker,target,multiple,years", [
    ("TSLA 在2030年股价达到1000一股", "TSLA", "1000", None, "4.25"),
    ("SYNT will trade at $100 in five years.", "SYNT", "100", None, "5"),
    ("特斯拉五年后翻倍", "TSLA", None, "2", "5"),
    ("Apple to $250 by end of 2027", "AAPL", "250", None, "1.25"),
    ("I think $NVDA will hit 300 in 3 years", "NVDA", "300", None, "3"),
    ("英伟达明年涨到两百美元", "NVDA", "200", None, "1.25"),
    ("苹果三年内涨50%", "AAPL", None, "1.5", "3"),
    ("NVDA 18个月后到 250", "NVDA", "250", None, "1.5"),
    ("SYNT 一年半后 120 美元", "SYNT", "120", None, "1.5"),
    ("BRK.B to $600 within 2 years", "BRK.B", "600", None, "2"),
    ("RKLB stock will reach $1.5k in 4 years", "RKLB", "1500", None, "4"),
])
def test_extraction(text, ticker, target, multiple, years):
    r = ex(text)
    assert r.ticker == ticker
    assert r.target_price == (Decimal(target) if target else None)
    assert r.target_multiple == (Decimal(multiple) if multiple else None)
    assert r.horizon_years == Decimal(years)
    assert r.ticker_text and (r.target_text or not (target or multiple))


def test_missing_parts_are_reported_not_guessed():
    r = ex("RKLB stock will reach $200")
    assert r.horizon_years is None and any("time" in n for n in r.notes_en)
    r = ex("I like USD and the CEO")
    assert r.ticker is None and r.target_price is None
    assert len(r.notes_zh) == 3


def test_unknown_uppercase_word_is_not_a_ticker_when_the_list_is_known():
    assert ex("ZZZZ to $10 in 2 years").ticker is None
    # without a ticker list, an uppercase token is still offered for review
    assert extract_claim("ZZZZ to $10 in 2 years", today=TODAY).ticker == "ZZZZ"


def test_past_year_is_flagged():
    r = ex("TSLA 在2020年股价达到1000一股")
    assert r.horizon_years is None and r.target_date == date(2020, 12, 31)
    assert any("已经过去" in n for n in r.notes_zh)


def test_chinese_numbers():
    assert cn_to_number("一千") == 1000
    assert cn_to_number("两千五百") == 2500
    assert cn_to_number("十") == 10
    assert cn_to_number("三万") == 30000
    assert cn_to_number("abc") is None


def _run(draft):
    svc = CaseService(make_settings_tmp(), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    return svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")


def make_settings_tmp():
    import tempfile
    from pathlib import Path

    return make_settings(Path(tempfile.mkdtemp()))


def test_report_layers_and_scenarios_ps():
    r = _run(ps_draft(ticker="SYNT", current_shares="95000000"))
    rep = r.report
    assert [layer.title.zh[:5] for layer in rep.layers] == ["第 1 层", "第 2 层", "第 3 层", "第 4 层", "第 5 层"]
    assert "概率约" in rep.headline.zh and "Probability that" in rep.headline.en
    # the claim's own path reproduces the target price
    claim_path = rep.scenarios[-1]
    assert Decimal(claim_path.price.replace(",", "")) == pytest.approx(Decimal(100), abs=Decimal("0.01"))
    assert rep.scenario_note and "不是预测" in rep.scenario_note.zh
    assert rep.monitor and rep.upside and rep.downside


def test_report_not_supported_pe_explains_the_divergence():
    r = _run(pe_draft(ticker="SYNT", base_annual_metric="40000000", current_shares="95000000"))
    rep = r.report
    assert "<0.1%" in rep.headline.zh  # no similar company grew net income that fast
    assert any("问题不在估值" in d.zh or "期权市场" in d.zh for d in rep.divergences)
    assert any(row.tone == "bad" for row in rep.layers[0].rows)


def test_report_without_public_data_says_so():
    from tickercase.http_client import FakeHttpClient
    from tickercase.models import DataMode

    svc = CaseService(make_settings_tmp(), fetcher_factory=lambda m, s: FakeHttpClient({}, data_mode=DataMode.LIVE),
                      now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    draft = ps_draft()
    r = svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="live")
    assert r.verdict.label == "insufficiently_specified"
    assert "数据不足" in r.report.headline.zh
    assert r.report.scenarios == []
