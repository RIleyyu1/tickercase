"""Touch claims, the option term structure's event move, and the user's event scenario. All data SYNTHETIC."""

from datetime import date
from decimal import Decimal

import pytest

from tickercase.extract import extract_claim
from tickercase.models import TermPoint
from tickercase.oracle import event_move, p_end_above, p_touch, scenario
from tickercase.service import CaseService
from tickercase.validation import confirm, validate_draft

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, ps_draft


@pytest.mark.parametrize("text, condition", [
    ("RKLB 3年内股价达到300", "touch"),
    ("RKLB neutron发射后脉冲到200", "touch"),
    ("NVDA will hit $300 in 3 years", "touch"),
    ("TSLA closes above 500 by 2027", "end"),
    ("特斯拉2027年底收在500以上", "end"),
    ("特斯拉2030年涨到1000", None),
])
def test_condition_is_read_from_the_wording(text, condition):
    ex = extract_claim(text, today=FIXED_TODAY)
    assert ex.condition == condition
    assert ex.target_price is not None


def test_inferred_touch_is_explained():
    ex = extract_claim("RKLB 3年内股价达到300", today=FIXED_TODAY)
    assert ex.condition_text == "3年内" and any("3年内" in n for n in ex.notes_zh)


def test_price_condition_is_validated():
    assert validate_draft(ps_draft(price_condition="touch"), today=FIXED_TODAY).claim.price_condition == "touch"
    assert validate_draft(ps_draft(), today=FIXED_TODAY).claim.price_condition == "end"
    bad = validate_draft(ps_draft(price_condition="sometimes"), today=FIXED_TODAY)
    assert not bad.ok and bad.issues[0].field == "price_condition"


# ------------------------------------------------------------------ event move

TERM = [TermPoint(expiry=d, atm_iv=iv) for d, iv in (
    (date(2026, 11, 20), 0.45), (date(2026, 12, 18), 0.45), (date(2027, 1, 15), 0.45),
    (date(2027, 3, 19), 0.53), (date(2027, 6, 17), 0.50))]


def test_event_move_finds_the_priced_jump():
    ev = event_move(TERM, date(2027, 2, 15), FIXED_TODAY)
    assert ev.status == "ok" and ev.before_expiry == date(2027, 1, 15) and ev.after_expiry == date(2027, 3, 19)
    # extra variance = var(Mar) - var(Jan) - 0.45^2 * gap, with t in years from FIXED_TODAY
    t1, t2 = (date(2027, 1, 15) - FIXED_TODAY).days / 365.25, (date(2027, 3, 19) - FIXED_TODAY).days / 365.25
    expected = (0.53 ** 2 * t2 - 0.45 ** 2 * t1 - 0.45 ** 2 * (t2 - t1)) ** 0.5
    assert ev.move == pytest.approx(expected, rel=1e-6)


def test_event_move_flat_and_out_of_range():
    assert event_move(TERM, date(2026, 12, 1), FIXED_TODAY).status == "not_priced"
    assert event_move(TERM, date(2028, 6, 1), FIXED_TODAY).status == "not_computable"
    assert event_move(TERM, date(2026, 9, 1), FIXED_TODAY).status == "not_computable"
    assert event_move(TERM[:1], date(2026, 11, 1), FIXED_TODAY).status == "not_computable"


# ------------------------------------------------------------------ scenario

def test_scenario_without_a_jump_is_the_plain_model():
    end = scenario(spot=70, target=200, vol=0.8, rate=0.05, years=3, event_years=0.5, up=0, down=0, p_success=0.5, touch=False)
    assert end.probability == pytest.approx(p_end_above(70, 200, 0.8, 3, 0.05))
    touch = scenario(spot=70, target=200, vol=0.8, rate=0.05, years=3, event_years=0, up=0, down=0, p_success=0.5, touch=True)
    assert touch.probability == pytest.approx(p_touch(70, 200, 0.8, 3, 0.05))


@pytest.mark.parametrize("touch", [False, True])
def test_scenario_break_even_and_market_implied(touch):
    kw = dict(spot=70, target=200, vol=0.6, rate=0.05, years=3, event_years=0.5, up=0.8, down=-0.35, touch=touch)
    r = scenario(p_success=0.4, market_p=0.2, **kw)
    assert r.p_if_failure < r.probability < r.p_if_success
    assert scenario(p_success=0.0, **kw).probability == pytest.approx(r.p_if_failure)
    if r.break_even is not None:
        assert scenario(p_success=r.break_even, **kw).probability == pytest.approx(0.5)
    if r.market_implied is not None:
        assert scenario(p_success=r.market_implied, **kw).probability == pytest.approx(0.2)


def test_scenario_out_of_reach_has_no_break_even():
    r = scenario(spot=70, target=2000, vol=0.3, rate=0.04, years=1, event_years=0.5, up=0.5, down=-0.3, p_success=1, touch=True)
    assert r.p_if_success < 0.5 and r.break_even is None


# ------------------------------------------------------------------ end to end (synthetic)

@pytest.fixture
def svc(tmp_path):
    return CaseService(make_settings(tmp_path), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)


def test_touch_claim_uses_touch_probabilities(svc):
    end_draft = ps_draft(ticker="SYNT", current_shares="95000000")
    touch_draft = ps_draft(ticker="SYNT", current_shares="95000000", price_condition="touch")
    end = svc.evaluate(end_draft, confirm(end_draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")
    touch = svc.evaluate(touch_draft, confirm(touch_draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")
    assert end.oracle.condition == "end" and touch.oracle.condition == "touch"
    m1 = next(m for m in touch.oracle.methods if m.id == "M1")
    assert touch.oracle.high >= m1.touch_probability > m1.probability
    assert touch.oracle.low > end.oracle.low
    assert "任意时点触及" in touch.report.headline.zh
    m3 = next(m for m in touch.oracle.methods if m.id == "M3")
    assert any("只作参考" in x.zh for x in m3.limitations)
    assert all(r.options_touch is None or r.options_touch >= r.options_p for r in touch.oracle.ladder)


def test_options_term_structure_is_read(svc):
    draft = ps_draft(ticker="SYNT", current_shares="95000000")
    result = svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")
    term = result.options.term
    assert len(term) >= 5 and term == sorted(term, key=lambda p: p.expiry)
    ev = event_move(term, date(2027, 2, 15), FIXED_TODAY)
    assert ev.status == "ok" and Decimal(str(ev.move)) > Decimal("0.1")
