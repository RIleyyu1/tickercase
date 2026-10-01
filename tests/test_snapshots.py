import json
import threading
from datetime import datetime, timezone

import pytest

from tickercase.http_client import (
    ORIGIN_SYNTHETIC,
    FetchError,
    LiveHttpClient,
    RecordingHttpClient,
    ReplayHttpClient,
    TransportResponse,
    snapshot_filename,
    write_snapshot,
)
from tickercase.models import DataMode

URL = "https://data.sec.gov/submissions/CIK0001234567.json"
T_CAPTURE = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
T_REPLAY = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def live_with(payload):
    def transport(url, headers, timeout):
        return TransportResponse(200, {}, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    return LiveHttpClient(user_agent="t/0.1 t@example.org", transport=transport, min_interval_seconds=0, now=lambda: T_CAPTURE)


def test_record_then_replay_keeps_original_capture_time(tmp_path):
    payload = {"name": "Société Générale 测试", "filings": {}}
    rec = RecordingHttpClient(live_with(payload), tmp_path)
    recorded = rec.get_json(URL)
    assert recorded.data_mode is DataMode.LIVE

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert "Société Générale 测试" in raw  # UTF-8, not \u escapes
    envelope = json.loads(raw)
    assert envelope["origin"] == "live_recorded" and envelope["request"]["url"] == URL

    replay = ReplayHttpClient(tmp_path, now=lambda: T_REPLAY)
    r = replay.get_json(URL)
    assert r.payload == payload
    assert r.data_mode is DataMode.REPLAY
    assert r.source_captured_at == T_CAPTURE  # original date not refreshed
    assert r.retrieved_at == T_REPLAY


def test_replay_missing_snapshot_raises_and_never_calls_network(tmp_path, monkeypatch):
    import tickercase.http_client as hc

    def boom(*a, **k):
        raise AssertionError("network must not be used in replay")

    monkeypatch.setattr(hc, "urlopen", boom)
    with pytest.raises(FetchError) as e:
        ReplayHttpClient(tmp_path).get_json(URL)
    assert e.value.code == "snapshot_missing" and e.value.data_mode is DataMode.REPLAY


def test_failed_live_request_writes_nothing(tmp_path):
    def transport(url, headers, timeout):
        return TransportResponse(500, {}, b"")

    live = LiveHttpClient(user_agent="t/0.1 t@example.org", transport=transport, max_retries=0, min_interval_seconds=0)
    with pytest.raises(FetchError):
        RecordingHttpClient(live, tmp_path).get_json(URL)
    assert list(tmp_path.glob("*")) == []


def test_synthetic_snapshot_replays_as_synthetic(tmp_path):
    write_snapshot(tmp_path, URL, {"k": 1}, captured_at=T_CAPTURE, origin=ORIGIN_SYNTHETIC)
    r = ReplayHttpClient(tmp_path).get_json(URL)
    assert r.data_mode is DataMode.SYNTHETIC and r.snapshot_origin == ORIGIN_SYNTHETIC


def test_concurrent_writes_same_url_leave_valid_file(tmp_path):
    errors = []

    def worker(i):
        try:
            write_snapshot(tmp_path, URL, {"writer": i, "blob": "x" * 20000}, captured_at=T_CAPTURE)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    files = list(tmp_path.glob("*.json"))
    assert [f.name for f in files] == [snapshot_filename(URL)]
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["response"]["writer"] in range(16)
    assert not list(tmp_path.glob(".tmp-*"))


def test_corrupt_or_mismatched_snapshot(tmp_path):
    (tmp_path / snapshot_filename(URL)).write_text("{not json", encoding="utf-8")
    with pytest.raises(FetchError) as e:
        ReplayHttpClient(tmp_path).get_json(URL)
    assert e.value.code == "snapshot_corrupt"
    path = write_snapshot(tmp_path, URL, {}, captured_at=T_CAPTURE)
    env = json.loads(path.read_text(encoding="utf-8"))
    env["request"]["url"] = "https://example.org/other"
    path.write_text(json.dumps(env), encoding="utf-8")
    with pytest.raises(FetchError) as e:
        ReplayHttpClient(tmp_path).get_json(URL)
    assert e.value.code == "snapshot_corrupt"
