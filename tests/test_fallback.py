"""The fallback of risk R.2 (docs/risks.md): the market source does not answer, the case still ends, and it says how
old its market data is. These tests take the failure path: the source is down, the copy is too old, the copy was
tampered with, the request itself is wrong."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tickercase import plain
from tickercase.acquisition import _with_fallback, build_fetcher
from tickercase.http_client import ORIGIN_LAST_GOOD, FetchError, FetchedJson, LastGoodHttpClient, snapshot_filename
from tickercase.models import CaseStatus, DataMode
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, offline_factory, ps_draft

URL = "https://query1.finance.yahoo.com/v8/finance/chart/SYNT?interval=1d"
T0 = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)


class Source:
    """A live source that can be switched off, slowed into a timeout, or made to refuse the request itself."""

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {"price": 100}
        self.failure = None
        self.calls = 0

    def get_json(self, url):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return FetchedJson(url=url, payload=self.payload, retrieved_at=clock["now"], data_mode=DataMode.LIVE)


clock = {"now": T0}


def client(tmp_path, source, hours=72.0):
    clock["now"] = T0
    return LastGoodHttpClient(source, tmp_path / "last_good", max_age_seconds=hours * 3600, now=lambda: clock["now"])


def down(code="server_error", status=503):
    return FetchError(code, f"{status} from the source", url=URL, http_status=status, data_mode=DataMode.LIVE)


def test_a_good_answer_is_passed_on_and_kept(tmp_path):
    c = client(tmp_path, Source())
    got = c.get_json(URL)
    assert got.payload == {"price": 100} and got.snapshot_origin is None and got.source_captured_at is None
    assert (tmp_path / "last_good" / snapshot_filename(URL)).is_file() and c.used == []


@pytest.mark.parametrize("failure", [down(), down("timeout", None), down("forbidden", 403), down("http_error", 429), down("invalid_json", None)])
def test_when_the_source_does_not_answer_the_last_good_answer_is_served_and_dated(tmp_path, failure):
    source = Source()
    c = client(tmp_path, source)
    c.get_json(URL)
    source.failure = failure
    clock["now"] = T0 + timedelta(hours=5)
    got = c.get_json(URL)
    assert got.payload == {"price": 100}
    assert got.snapshot_origin == ORIGIN_LAST_GOOD and got.source_captured_at == T0 and got.retrieved_at == T0 + timedelta(hours=5)
    assert c.used == [(URL, T0)]


def test_an_answer_older_than_the_limit_is_refused_and_the_message_says_its_age(tmp_path):
    source = Source()
    c = client(tmp_path, source, hours=72)
    c.get_json(URL)
    source.failure = down()
    clock["now"] = T0 + timedelta(hours=72, minutes=1)
    with pytest.raises(FetchError) as caught:
        c.get_json(URL)
    assert caught.value.code == "server_error" and caught.value.http_status == 503
    assert "72 hours old" in caught.value.message and "was not used" in caught.value.message
    assert c.used == []
    clock["now"] = T0 + timedelta(hours=71, minutes=59)      # just inside the limit it is used
    assert c.get_json(URL).snapshot_origin == ORIGIN_LAST_GOOD


def test_with_nothing_stored_the_failure_is_raised_as_before(tmp_path):
    source = Source()
    source.failure = down()
    with pytest.raises(FetchError) as caught:
        client(tmp_path, source).get_json(URL)
    assert caught.value.code == "server_error" and "was not used" not in caught.value.message


@pytest.mark.parametrize("code", ["missing_user_agent", "invalid_url"])
def test_a_wrong_request_is_never_hidden_by_a_stored_answer(tmp_path, code):
    source = Source()
    c = client(tmp_path, source)
    c.get_json(URL)
    source.failure = FetchError(code, "the request itself is wrong", url=URL, data_mode=DataMode.LIVE)
    with pytest.raises(FetchError) as caught:
        c.get_json(URL)
    assert caught.value.code == code and c.used == []


def test_a_stored_answer_for_another_request_is_not_used(tmp_path):
    source = Source()
    c = client(tmp_path, source)
    c.get_json(URL)
    source.failure = down()
    with pytest.raises(FetchError):
        c.get_json(URL.replace("SYNT", "OTHR"))
    assert c.used == []


@pytest.mark.parametrize("damage", ["truncate", "other_url", "no_time", "future", "not_json", "wrong_schema"])
def test_a_damaged_or_planted_copy_is_not_served(tmp_path, damage):
    """What could get past the fallback: a file in the folder that is not an honest earlier answer to this request."""
    source = Source()
    c = client(tmp_path, source)
    c.get_json(URL)
    path = tmp_path / "last_good" / snapshot_filename(URL)
    body = json.loads(path.read_text(encoding="utf-8"))
    if damage == "truncate":
        path.write_text(path.read_text(encoding="utf-8")[:40], encoding="utf-8")
    elif damage == "not_json":
        path.write_text("price: 1000000", encoding="utf-8")
    else:
        if damage == "other_url":
            body["request"]["url"] = URL.replace("SYNT", "EVIL")
        elif damage == "no_time":
            body.pop("captured_at")
        elif damage == "future":
            body["captured_at"] = (T0 + timedelta(days=30)).isoformat()
        elif damage == "wrong_schema":
            body["schema"] = "something-else"
        path.write_text(json.dumps(body), encoding="utf-8")
    source.failure = down()
    clock["now"] = T0 + timedelta(hours=1)
    with pytest.raises(FetchError) as caught:
        c.get_json(URL)
    assert caught.value.code == "server_error" and c.used == []


def test_a_failed_write_does_not_lose_the_answer_in_hand(tmp_path):
    (tmp_path / "last_good").write_text("a file where the folder should be", encoding="utf-8")
    got = client(tmp_path, Source()).get_json(URL)
    assert got.payload == {"price": 100}


def test_only_the_market_source_in_live_mode_gets_the_fallback_and_zero_hours_switch_it_off(tmp_path):
    settings = make_settings(tmp_path, sec_user_agent="TickerCase test contact@example.com")
    assert isinstance(build_fetcher("live", settings, "market"), LastGoodHttpClient)
    assert isinstance(build_fetcher("live", settings, "options"), LastGoodHttpClient)
    assert not isinstance(build_fetcher("live", settings, "sec"), LastGoodHttpClient)
    assert not isinstance(build_fetcher("live", settings, "web"), LastGoodHttpClient)
    assert not isinstance(build_fetcher("synthetic", settings, "market"), LastGoodHttpClient)
    assert build_fetcher("live", settings, "market").directory == settings.snapshot_dir.parent / "last_good"
    off = make_settings(tmp_path, fallback_max_age_hours=0.0)
    live = object()
    assert _with_fallback(live, off) is live


# ---------------------------------------------------------------- the whole case


class Flaky:
    """The market source of the offline tests, with a switch."""

    def __init__(self, inner, state):
        self.inner, self.state = inner, state

    def get_json(self, url):
        if self.state["down"]:
            raise FetchError("server_error", "503 from the market source", url=url, http_status=503, data_mode=DataMode.LIVE)
        got = self.inner.get_json(url)
        return FetchedJson(url=url, payload=got.payload, retrieved_at=self.state["now"], data_mode=got.data_mode)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def cased(tmp_path, hours=72.0):
    settings = make_settings(tmp_path, fallback_max_age_hours=hours)
    base = offline_factory(settings)
    state = {"down": False, "now": FIXED_NOW}

    def factory(mode, source):
        fetcher = base(mode, source)
        if source in ("market", "options"):
            return LastGoodHttpClient(Flaky(fetcher, state), tmp_path / "last_good", max_age_seconds=hours * 3600, now=lambda: state["now"])
        return fetcher

    svc = CaseService(settings, fetcher_factory=factory, store=CaseStore(tmp_path / "cases"), now=lambda: state["now"], today=lambda: FIXED_TODAY)
    return svc, state


def run(svc, **overrides):
    draft = ps_draft(price_condition="touch", **overrides)
    return svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")


def test_the_market_source_goes_down_and_the_case_still_gives_its_answer_and_says_how_old_the_data_is(tmp_path):
    svc, state = cased(tmp_path)
    first = run(svc)
    assert first.oracle.low is not None and first.stale_sources == [] and first.status is CaseStatus.EVALUATED
    state["down"] = True
    state["now"] = FIXED_NOW + timedelta(hours=20)
    second = run(svc)
    assert second.status is CaseStatus.EVALUATED and not second.provider_errors             # the outage did not become a failed case
    assert second.data_steps == first.data_steps
    assert second.oracle.low == first.oracle.low                                            # the same data, so the same number
    # the market fetcher also carries the headlines and the prediction markets, so they fall back with it
    assert {s.source for s in second.stale_sources} >= {"Yahoo Finance"} and all(s.age_hours == 20.0 for s in second.stale_sources)
    stale = next(s for s in second.stale_sources if s.source == "Yahoo Finance")
    assert stale.answers >= 3
    assert any("did not answer during this run" in w and "20 hours before this run" in w for w in second.warnings)
    assert any("这次没有应答" in w for w in second.warnings_zh) and len(second.warnings) == len(second.warnings_zh)
    note = plain.stale_note(second)
    assert note is not None and "20 hours" in note[0] and "20 小时" in note[1] and "Yahoo Finance" in note[0]
    assert plain.stale_note(first) is None
    # the stored case keeps the fact
    again = svc.store.load(second.case_id)
    assert again.stale_sources == second.stale_sources


def test_past_the_limit_the_old_data_is_refused_and_the_market_steps_are_reported_as_failed(tmp_path):
    svc, state = cased(tmp_path, hours=72)
    run(svc)
    state["down"] = True
    state["now"] = FIXED_NOW + timedelta(hours=100)
    late = run(svc)
    assert late.stale_sources == [] and plain.stale_note(late) is None
    assert late.status is CaseStatus.EVALUATED_WITH_PROVIDER_ERRORS
    assert late.data_steps["price_history"] == "failed" and late.data_steps["sec_filings"] == "ok"
    assert any("hours old" in e.message and "was not used" in e.message for e in late.provider_errors)


def test_a_source_that_was_never_reached_has_nothing_to_fall_back_on(tmp_path):
    svc, state = cased(tmp_path)
    state["down"] = True
    cold = run(svc)
    assert cold.stale_sources == [] and cold.data_steps["price_history"] == "failed"
    assert cold.status is CaseStatus.EVALUATED_WITH_PROVIDER_ERRORS
