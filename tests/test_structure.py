"""Market structure and probability change (v0.11): the factor split, the scenario at another price, the Shapley change."""

import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from tickercase.models import ClaimDraft, DataMode, FilingRecord
from tickercase.oracle import p_end_above
from tickercase.providers.peers import parse_similar
from tickercase.providers.base import ProviderError
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.structure import (ESTIMATION_DAYS, WINDOW_DAYS, StructureInputs, build_structure, carino, find_previous,
                                  probability_change, shapley)
from tickercase.ui import cockpit as ck
from tickercase.validation import confirm, fingerprint, normalized_draft, validate_draft

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, ps_draft

N = ESTIMATION_DAYS + WINDOW_DAYS + 1


def _days(n: int, end: date = date(2026, 9, 30)) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _prices(returns: list[float], start: float = 100.0) -> list[float]:
    out = [start]
    for r in returns:
        out.append(out[-1] * math.exp(r))
    return out


def _world(seed: int = 7, shock_day: int = N - 6, shock: float = -0.12):
    """Market m, a sector factor s, and a stock r = 1.5 m + 0.8 s + noise with one stock-specific shock."""
    rng = random.Random(seed)
    m = [rng.gauss(0.0004, 0.01) for _ in range(N - 1)]
    sec = [rng.gauss(0.0, 0.015) for _ in range(N - 1)]
    peer_a = [0.9 * x + y + rng.gauss(0, 0.006) for x, y in zip(m, sec)]
    peer_b = [1.1 * x + y + rng.gauss(0, 0.008) for x, y in zip(m, sec)]
    noise = [rng.gauss(0, 0.04) for _ in range(N - 1)]
    stock = [1.5 * x + 0.8 * y + rng.gauss(0, 0.008) for x, y in zip(m, sec)]
    stock[shock_day - 1] += shock
    days = _days(N)
    as_series = lambda rets, start=100.0: list(zip(days, _prices(rets, start)))  # noqa: E731
    return days, StructureInputs(symbol="TEST", stock=as_series(stock, 50.0), market=as_series(m, 600.0),
                                 peers={"PA": as_series(peer_a), "PB": as_series(peer_b), "PN": as_series(noise)},
                                 regime={"^VIX": as_series([0.0] * (N - 1), 16.0), "QQQ": as_series(m, 500.0)},
                                 peer_source="default_assumption:test", retrieved_at=FIXED_NOW, data_mode=DataMode.SYNTHETIC)


def test_parts_add_up_to_the_window_return_and_betas_are_recovered():
    _, inp = _world()
    st, fit = build_structure(inp)
    assert st.market_part + st.peer_part + st.own_part == pytest.approx(st.total, abs=1e-12)
    assert st.total == pytest.approx(inp.stock[-1][1] / inp.stock[-1 - WINDOW_DAYS][1] - 1, abs=1e-12)
    assert st.beta_market == pytest.approx(1.5 + 0.8 * 0, abs=0.45)  # the sector is independent of the market
    assert st.beta_peers == pytest.approx(0.8, abs=0.25)
    assert st.r_squared > 0.5 and st.window_days == WINDOW_DAYS and len(st.days) == WINDOW_DAYS
    assert {p.symbol: p.used for p in st.peers} == {"PA": True, "PB": True, "PN": False}
    assert next(p for p in st.peers if p.symbol == "PN").note is not None


def test_the_stock_specific_shock_is_flagged_and_carries_its_filing():
    days, inp = _world()
    shock_day = days[N - 6]
    filing = FilingRecord(provider_id="sec", ticker="TEST", cik="1", accession_number="0000000001-26-000001", form_type="8-K", is_amendment=False,
                          filing_date=shock_day - timedelta(days=1) if shock_day.weekday() > 0 else shock_day - timedelta(days=3),
                          items="7.01,8.01,9.01", filing_index_url="https://www.sec.gov/x", source_response_url="https://data.sec.gov/x",
                          retrieved_at=FIXED_NOW, data_mode=DataMode.SYNTHETIC)
    st, _ = build_structure(inp, [filing])
    flagged = [d for d in st.days if d.flagged]
    assert shock_day in {d.day for d in flagged}
    hit = next(d for d in st.days if d.day == shock_day)
    assert hit.own < -0.08
    # the filing belongs to the trading day it was filed on; the flagged day after it lists it as "the day before" on the page
    before = st.days[[d.day for d in st.days].index(shock_day) - 1]
    assert before.filings == [f"8-K {filing.filing_date} (7.01, 8.01)"]
    html = ck.mkt_unit(_case_with(st), True, "UNIT 04", anim=False)
    assert "前一交易日：8-K" in html and "Reg FD 披露" in html and "◆" in html


def test_without_peers_the_sector_stays_in_the_own_part():
    _, inp = _world()
    inp.peers = {}
    st, fit = build_structure(inp)
    assert st.beta_peers is None and st.peer_part == 0 and fit.p is None
    assert any("同类篮子" in n.zh for n in st.notes)
    assert st.market_part + st.own_part == pytest.approx(st.total, abs=1e-12)


def test_short_history_is_an_error_not_a_guess():
    _, inp = _world()
    inp.stock = inp.stock[-50:]
    with pytest.raises(ValueError):
        build_structure(inp)


def test_carino_scale():
    assert carino(0.0) == 1.0
    for x in (-0.4, -0.01, 0.2):
        assert carino(x) * x == pytest.approx(math.expm1(x))


def test_shapley_parts_add_up_and_do_not_depend_on_order():
    f = lambda s: (2 if "a" in s else 1) * (3 if "b" in s else 1) + (5 if "c" in s else 0)  # noqa: E731
    phi = shapley(["a", "b", "c"], f)
    assert sum(phi.values()) == pytest.approx(f(frozenset("abc")) - f(frozenset()))
    assert phi == pytest.approx(shapley(["c", "b", "a"], f))
    assert phi["c"] == pytest.approx(5)


def test_parse_similar_and_empty_list():
    payload = {"finance": {"result": [{"symbol": "RKLB", "recommendedSymbols": [{"symbol": "ASTS"}, {"symbol": "ionq"}, {"symbol": "ASTS"}]}]}}
    assert parse_similar(payload, "u") == ["ASTS", "IONQ"]
    with pytest.raises(ProviderError):
        parse_similar({"finance": {"result": [{"symbol": "X", "recommendedSymbols": []}]}}, "u")


# ---------------------------------------------------------------- validation


def test_peer_tickers_are_cleaned_and_checked():
    v = validate_draft(ps_draft(peer_tickers="synb, $SYNA，syna、SYNT"), today=FIXED_TODAY)
    assert v.ok and v.claim.peer_tickers == ["SYNB", "SYNA"]  # upper case, de-duplicated, the claim's own ticker left out
    bad = validate_draft(ps_draft(peer_tickers="ASTS, not a ticker!"), today=FIXED_TODAY)
    assert not bad.ok and {i.field for i in bad.issues} == {"peer_tickers"}
    many = validate_draft(ps_draft(peer_tickers=",".join(f"P{i}" for i in range(10))), today=FIXED_TODAY)
    assert any(i.code == "out_of_range" for i in many.issues)


def test_an_empty_peer_basket_keeps_earlier_fingerprints():
    d = ps_draft()
    assert "peer_tickers" not in normalized_draft(d)
    assert fingerprint(d) == fingerprint(ps_draft(peer_tickers="  "))
    assert fingerprint(ps_draft(peer_tickers="asts, ionq")) == fingerprint(ps_draft(peer_tickers="ASTS,IONQ"))
    assert fingerprint(ps_draft(peer_tickers="ASTS")) != fingerprint(d)


# ---------------------------------------------------------------- the pipeline (synthetic data)


def _svc(tmp_path, now=FIXED_NOW, today=FIXED_TODAY):
    return CaseService(make_settings(tmp_path), store=CaseStore(tmp_path / "cases"), now=lambda: now, today=lambda: today)


def _run(svc, draft):
    return svc.evaluate(draft, confirm(draft, today=svc.today(), now=svc.now), sec_mode="synthetic")


def test_synthetic_case_has_the_split_the_scenario_and_no_change_on_first_run(tmp_path):
    r = _run(_svc(tmp_path), ps_draft(peer_tickers="SYNA, SYNB, SYNC"))
    st = r.structure
    assert r.data_steps["market_structure"] == "ok" and st.data_mode is DataMode.SYNTHETIC
    assert {p.symbol: p.used for p in st.peers} == {"SYNA": True, "SYNB": True, "SYNC": False}
    assert {x.symbol for x in st.regime} == {"SPY", "QQQ", "IWM", "^VIX", "^VIX3M"}
    assert st.market_part + st.peer_part + st.own_part == pytest.approx(st.total, abs=1e-12)
    sh = st.shifted
    assert sh.spot_to == pytest.approx(50.0 * math.exp(-(st.log_market + st.log_peers)))
    assert sh.low <= sh.high and set(sh.methods) <= {"M1", "M2", "M3", "M4"}
    assert r.change is None  # nothing earlier in the store


def test_prefill_offers_the_similar_stocks_as_a_default(tmp_path):
    snap = _svc(tmp_path).reference_snapshot("SYNT", mode="synthetic")
    sug = snap.assumption_suggestions["peer_tickers"]
    assert sug.value == "SYNA, SYNB, SYNC" and sug.source.startswith("default_assumption:Yahoo")


def test_a_rerun_of_the_same_claim_explains_the_change(tmp_path):
    svc = _svc(tmp_path)
    draft = ps_draft(peer_tickers="SYNA, SYNB", price_condition="touch")
    first = _run(svc, draft)
    # the same claim run earlier, on older prices: shift its market date and spot back
    old = first.model_copy(deep=True)
    old.case_id = "0" * 32
    old.created_at = FIXED_NOW - timedelta(days=14)
    old.market.last_date = first.structure.days[9].day
    old.oracle.spot = 58.0
    old.oracle.base_volatility = first.oracle.base_volatility * 0.9
    svc.store.save(old)
    r = _run(svc, draft)
    ch = r.change
    assert ch is not None and ch.previous_case_id == old.case_id and ch.price_split
    for mc in ch.methods:
        assert sum(p.delta for p in mc.parts) == pytest.approx(mc.after - mc.before, abs=1e-12)
        assert {p.key for p in mc.parts} == {"market", "peers", "own", "volatility", "time", "rate"}
    m2 = next(mc for mc in ch.methods if mc.method == "M2")
    now_m2 = next(m for m in r.oracle.methods if m.id == "M2")
    assert m2.after == pytest.approx(now_m2.touch_probability)
    vol_part = next(p for p in m2.parts if p.key == "volatility")
    assert vol_part.delta > 0  # higher volatility helps a touch claim
    html = ck.mkt_unit(r, False, "UNIT 04", anim=False)
    assert "since the last run" in html and "Shapley" in html
    assert "last run" in ck.status_bar(r, False, [])


def test_find_previous_needs_the_same_claim_on_older_prices(tmp_path):
    svc = _svc(tmp_path)
    draft = ps_draft(peer_tickers="SYNA")
    r = _run(svc, draft)
    claim, o = r.confirmed_claim.values, r.oracle
    assert find_previous([r], claim, o, market_date=r.market.last_date, exclude="other") is None  # same prices: nothing to explain
    older = r.model_copy(deep=True)
    older.case_id = "1" * 32
    older.market.last_date = r.market.last_date - timedelta(days=7)
    assert find_previous([older], claim, o, market_date=r.market.last_date, exclude=r.case_id) is older
    other_target = older.model_copy(deep=True)
    other_target.confirmed_claim.values.target_price = claim.target_price + 1
    assert find_previous([other_target], claim, o, market_date=r.market.last_date, exclude=r.case_id) is None


def test_change_without_daily_data_keeps_the_price_in_one_part(tmp_path):
    svc = _svc(tmp_path)
    r = _run(svc, ps_draft(peer_tickers="SYNA"))
    prev = r.model_copy(deep=True)
    prev.market.last_date = date(2024, 1, 2)  # before the factor window
    prev.oracle.spot = 40.0
    ch = probability_change(prev, oracle=r.oracle, fit=None, today=FIXED_TODAY)
    assert not ch.price_split
    for mc in ch.methods:
        assert {p.key for p in mc.parts} == {"price", "volatility", "time", "rate"}
        assert sum(p.delta for p in mc.parts) == pytest.approx(mc.after - mc.before, abs=1e-12)
        vol = r.oracle.implied_volatility if mc.method == "M1" else r.oracle.base_volatility
        years = max((r.oracle.target_date - FIXED_TODAY).days / 365.25, 1 / 365.25)
        assert mc.after == pytest.approx(p_end_above(r.oracle.spot, float(r.oracle.target_price), vol, years, float(r.oracle.risk_free_rate)))


def test_sec_items_are_read_and_old_cases_render(tmp_path):
    r = _run(_svc(tmp_path), ps_draft())
    eight_k = [x for x in r.evidence_records if x.form_type == "8-K"]
    assert eight_k and all(x.items == "2.02,9.01" for x in eight_k)
    assert ck.filing_text("8-K 2026-10-14 (2.02)", False) == "8-K 2026-10-14 (2.02 results)"
    old = r.model_copy(update={"structure": None, "change": None})
    assert "before v0.11" in ck.mkt_unit(old, False, "UNIT 04", anim=False)
    assert ck.shifted_line(old, True) == ""


def _case_with(structure):
    """A minimal finished case carrying only a structure, for rendering."""
    from tickercase.models import CaseResult, CaseStatus

    return CaseResult(case_id="a" * 32, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), status=CaseStatus.EVALUATED,
                      input_fingerprint="0" * 64, structure=structure)


def test_draft_field_is_known_to_the_draft_model():
    assert "peer_tickers" in ClaimDraft.model_fields
