"""The timeline recorder (Lab Manual 10.4): entry and exit times at the participant boundaries of the sequence diagram.

All data is SYNTHETIC (replayed snapshots); no test touches the network.
"""

import json

from tickercase import timing
from tickercase.http_client import LiveHttpClient, TransportResponse
from tickercase.service import CaseService
from tickercase.storage import CaseStore
from tickercase.validation import confirm

from conftest import FIXED_NOW, FIXED_TODAY, make_settings, offline_factory, ps_draft


def run_case(tmp_path):
    settings = make_settings(tmp_path)
    svc = CaseService(settings, fetcher_factory=offline_factory(settings), store=CaseStore(tmp_path / "cases"),
                      now=lambda: FIXED_NOW, today=lambda: FIXED_TODAY)
    draft = ps_draft()
    return svc.evaluate(draft, confirm(draft, today=FIXED_TODAY, now=lambda: FIXED_NOW), sec_mode="synthetic")


def test_nothing_is_recorded_without_a_timing_folder(tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV_DIR, raising=False)
    run_case(tmp_path)
    assert timing.active() is None
    assert not list(tmp_path.rglob("*_evaluate_*.json"))


def test_one_case_writes_one_timeline_with_every_subsystem(tmp_path, monkeypatch):
    out = tmp_path / "timelines"
    monkeypatch.setenv(timing.ENV_DIR, str(out))
    result = run_case(tmp_path)
    files = list(out.glob("*_evaluate_*.json"))
    assert len(files) == 1
    timeline = json.loads(files[0].read_text(encoding="utf-8"))
    assert timeline["detail"]["status"] == result.status.value
    spans = timeline["spans"]
    top = [s for s in spans if s["depth"] == 1]
    assert {s["participant"] for s in top} == {"C.1", "C.2", "C.3", "C.4", "C.5", "C.6"}
    # the stages of one level follow one another and lie inside the case
    for a, b in zip(top, top[1:]):
        assert a["end"] <= b["start"] + 1e-6
    assert all(0 <= s["start"] <= s["end"] <= timeline["seconds"] + 1e-6 for s in spans)
    # A2 holds its four sub-functions in order, each performed by its component
    a2 = next(s for s in top if s["participant"] == "C.2")
    inside = [s for s in spans if s["depth"] == 2 and a2["start"] <= s["start"] and s["end"] <= a2["end"]]
    # since v0.24.0 the first two run side by side (docs/decisions/0001-acquisition-mode.md): one span of C.2 holds both lanes
    assert [s["participant"] for s in inside] == ["C.2", "C.2.3", "C.2.4"]
    both = inside[0]
    lanes = sorted((s for s in spans if s["depth"] == 3 and s["participant"] in ("C.2.1", "C.2.2")), key=lambda s: s["participant"])
    assert [s["stage"][:4] for s in lanes] == ["A2.1", "A2.2"]
    assert all(both["start"] <= s["start"] and s["end"] <= both["end"] + 1e-6 for s in lanes)
    assert [s["stage"][:4] for s in inside[1:]] == ["A2.3", "A2.4"]
    # a guarded step of a lane nests inside its own lane, never inside the other
    for step in (s for s in spans if s["stage"].startswith("guarded step") and s["depth"] == 4):
        assert sum(1 for lane in lanes if lane["start"] <= step["start"] and step["end"] <= lane["end"] + 1e-6) >= 1
    # replayed snapshots are read by the snapshot store, not asked from an outside service
    assert any(s["stage"] == "snapshot read" and s["participant"] == "C.2.4" for s in spans)
    assert not any(s["stage"] == "request" for s in spans)


def test_a_live_request_is_a_span_of_the_outside_service_and_the_spacing_wait_is_its_own_span(tmp_path):
    clock = [0.0]

    def transport(url, headers, timeout):
        clock[0] += 0.05
        return TransportResponse(200, {}, b"{}")

    def sleep(seconds):
        clock[0] += seconds

    client = LiveHttpClient(user_agent="TickerCase/test someone@example.org", min_interval_seconds=0.2, transport=transport,
                            sleep=sleep, monotonic=lambda: clock[0])
    with timing.capture("probe", directory=tmp_path) as timeline:
        client.get_json("https://data.sec.gov/submissions/CIK0000000001.json")
        client.get_json("https://data.sec.gov/submissions/CIK0000000002.json")
    stages = [(s["participant"], s["stage"]) for s in timeline.spans]
    assert stages.count(("SEC EDGAR", "request")) == 2
    assert stages.count(("C.2.4", "request spacing wait")) == 1
    assert len(list(tmp_path.glob("*_probe_*.json"))) == 1


def test_the_outside_participant_is_named_by_the_host_of_the_request():
    assert timing.external_participant("https://www.sec.gov/Archives/x.htm") == "SEC EDGAR"
    assert timing.external_participant("https://query2.finance.yahoo.com/v8/finance/chart/NVDA") == "Yahoo Finance"
    assert timing.external_participant("https://gamma-api.polymarket.com/markets") == "Polymarket"
    assert timing.external_participant("https://production.dataviz.cnn.io/index/fearandgreed/graphdata") == "CNN Fear and Greed"
    assert timing.external_participant("https://news.google.com/rss/search?q=NVDA") == "Google News"
    assert timing.external_participant("https://example.org/x") == "example.org"
