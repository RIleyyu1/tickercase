"""The two acquisition modes of A2 (docs/decisions/0001-acquisition-mode.md): the same record either way, the lanes
really side by side, and one lane's failure costs only that lane."""

import threading
import time

import pytest

from tickercase.acquisition import SourceGuard
from tickercase.config import load_settings
from tickercase.http_client import FetchError
from tickercase.providers.base import ProviderError
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, offline_factory, ps_draft


def service(tmp_path, mode, wrap=None):
    settings = make_settings(tmp_path, acquisition_mode=mode)
    base = offline_factory(settings)
    factory = (lambda m, source: wrap(source, base(m, source))) if wrap else base
    return CaseService(settings, fetcher_factory=factory, store=CaseStore(tmp_path / mode), now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)


def run(svc, progress=None, **overrides):
    draft = ps_draft(**overrides)
    return svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic", progress=progress)


class Wrapped:
    """A fetcher that notes who asked and when, can be slow, and can refuse every request."""

    def __init__(self, source, inner, log, delay=0.0, broken=()):
        self.source, self.inner, self.log, self.delay, self.broken = source, inner, log, delay, broken

    def get_json(self, url):
        start = time.perf_counter()
        if self.delay:
            time.sleep(self.delay)
        self.log.append((self.source, threading.current_thread().name, start, time.perf_counter()))
        if self.source in self.broken:
            raise FetchError("http_error", f"{self.source} is down", url=url, http_status=503)
        return self.inner.get_json(url)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def comparable(result):
    """The case without what differs between any two runs: its id and the clock times of the fetches."""
    clock = ("case_id", "created_at", "retrieved_at")

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in clock}
        return [strip(v) for v in x] if isinstance(x, list) else x

    return strip(result.model_dump(mode="json"))


def test_both_modes_write_the_same_case(tmp_path):
    a = run(service(tmp_path, "sequential"))
    b = run(service(tmp_path, "parallel"))
    assert a.oracle is not None and a.oracle.low is not None
    assert comparable(a) == comparable(b)
    assert a.data_steps == b.data_steps and all(v == "ok" for v in b.data_steps.values())


def test_the_evidence_query_reads_the_same_in_both_modes(tmp_path):
    queries = {}
    for mode in ("sequential", "parallel"):
        svc = service(tmp_path, mode, wrap=lambda source, inner: Wrapped(source, inner, [], delay=0.002))
        draft = ps_draft()
        status, acquired = svc.acquire(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")
        queries[mode] = list(acquired.evidence_query)
    assert queries["sequential"] and queries["sequential"] == queries["parallel"]


def test_the_two_lanes_run_side_by_side_and_the_page_is_told_from_the_calling_thread(tmp_path):
    log, told = [], []
    svc = service(tmp_path, "parallel", wrap=lambda source, inner: Wrapped(source, inner, log, delay=0.01))
    result = run(svc, progress=lambda step, state: told.append((step, state, threading.current_thread().name)))
    assert result.oracle is not None
    sec = [(a, b) for source, thread, a, b in log if source == "sec" and thread.startswith("tc-a2")]
    market = [(a, b) for source, thread, a, b in log if source in ("market", "options") and thread.startswith("tc-a2")]
    assert sec and market
    assert {thread for source, thread, _, _ in log if source == "sec" and thread.startswith("tc-a2")}.isdisjoint(
        {thread for source, thread, _, _ in log if source in ("market", "options") and thread.startswith("tc-a2")})   # one lane, one thread
    assert min(a for a, _ in market) < max(b for _, b in sec) and min(a for a, _ in sec) < max(b for _, b in market)   # they overlap in time
    me = threading.current_thread().name
    assert told and all(thread == me for _, _, thread in told)        # the page is never drawn from a lane
    for step in ("sec_filings", "price_history"):
        states = [state for name, state, _ in told if name == step]
        assert states[0] == "running" and states[-1] in ("ok", "failed")


def test_sequential_mode_stays_on_one_thread(tmp_path):
    log = []
    run(service(tmp_path, "sequential", wrap=lambda source, inner: Wrapped(source, inner, log)))
    assert log and {thread for _, thread, _, _ in log} == {threading.current_thread().name}


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
@pytest.mark.parametrize("down", ["sec", "market"])
def test_a_source_that_is_down_costs_only_its_own_steps(tmp_path, mode, down):
    broken = ("sec",) if down == "sec" else ("market", "options")
    svc = service(tmp_path, mode, wrap=lambda source, inner: Wrapped(source, inner, [], broken=broken))
    result = run(svc)                                      # the run ends: no exception leaves a lane
    steps = result.data_steps
    sec_steps, market_steps = ("sec_filings",), ("price_history", "options")
    lost, kept = (sec_steps, market_steps) if down == "sec" else (market_steps, sec_steps)
    assert all(steps.get(s) == "failed" for s in lost), steps
    assert all(steps.get(s) == "ok" for s in kept), steps
    assert result.provider_errors and all(e.code == "http_error" for e in result.provider_errors if e.http_status == 503)
    failed_providers = {e.provider_id for e in result.provider_errors}
    if down == "sec":
        assert not any("yahoo" in p.lower() for p in failed_providers), failed_providers
    else:
        assert not any(p.lower().startswith("sec") for p in failed_providers), failed_providers


def test_the_same_failures_are_recorded_in_both_modes(tmp_path):
    seen = {}
    for mode in ("sequential", "parallel"):
        svc = service(tmp_path, mode, wrap=lambda source, inner: Wrapped(source, inner, [], broken=("market", "options")))
        result = run(svc)
        seen[mode] = (result.data_steps, sorted((e.provider_id, e.code) for e in result.provider_errors), result.status)
    assert seen["sequential"] == seen["parallel"]


def test_a_step_counts_only_its_own_failures_and_settle_takes_back_only_its_own():
    guard = SourceGuard("synthetic")
    gate, other_failed = threading.Event(), threading.Event()

    def slow_and_fine():
        other_failed.wait(2)          # the other lane fails while this request is still out
        return "value"

    def fails():
        raise ProviderError("no_earnings_date", "nothing listed")

    def lane_one():
        gate.wait(2)
        guard.step("price_history", "yahoo_chart", slow_and_fine)

    t = threading.Thread(target=lane_one)
    t.start()
    gate.set()
    assert guard.step("sec_filings", "sec_edgar", lambda: (_ for _ in ()).throw(FetchError("http_error", "down", http_status=503))) is None
    other_failed.set()
    t.join(3)
    assert guard.steps == {"sec_filings": "failed", "price_history": "ok"}      # the failure next door is not counted here
    assert guard.step("earnings_date", "yahoo_calendar", fails) is None
    assert [e.code for e in guard.failures("earnings_date")] == ["no_earnings_date"]
    guard.settle("earnings_date")
    assert guard.steps["earnings_date"] == "ok" and [e.code for e in guard.source_error_records] == ["http_error"]


def test_the_setting_is_read_from_the_environment_and_checked():
    base = {"SEC_USER_AGENT": ""}
    assert load_settings(dict(base)).acquisition_mode in ("sequential", "parallel")
    assert load_settings(dict(base, TICKERCASE_ACQUISITION="Sequential ")).acquisition_mode == "sequential"
    assert load_settings(dict(base, TICKERCASE_ACQUISITION="parallel")).acquisition_mode == "parallel"
    with pytest.raises(ValueError):
        load_settings(dict(base, TICKERCASE_ACQUISITION="fast"))
