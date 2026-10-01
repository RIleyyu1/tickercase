import gzip
import json
import socket
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.error import URLError

import pytest

from tickercase.http_client import FetchError, LiveHttpClient, TransportResponse, parse_retry_after

URL = "https://data.sec.gov/submissions/CIK0001234567.json"
UA = "TickerCase-test/0.1 tester@example.org"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class ScriptedTransport:
    """Returns/raises the scripted items in order and records headers."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def ok(payload, headers=None):
    return TransportResponse(200, headers or {}, json.dumps(payload).encode())


def client(transport, **kw):
    sleeps = []
    clock = [0.0]

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    c = LiveHttpClient(user_agent=kw.pop("user_agent", UA), transport=transport, sleep=sleep, monotonic=lambda: clock[0], now=lambda: NOW, backoff_seconds=0.5, min_interval_seconds=kw.pop("min_interval_seconds", 0.0), **kw)
    return c, sleeps, clock


def test_success_sends_user_agent_and_marks_live():
    t = ScriptedTransport(ok({"a": 1}))
    c, _, _ = client(t)
    r = c.get_json(URL)
    assert r.payload == {"a": 1} and r.data_mode.value == "live" and r.retrieved_at == NOW
    assert t.calls[0][1]["User-Agent"] == UA


@pytest.mark.parametrize("ua", [None, "", "   ", "TickerCase no email"])
def test_missing_or_bad_user_agent_fails_before_network(ua):
    t = ScriptedTransport()
    c, _, _ = client(t, user_agent=ua)
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "missing_user_agent"
    assert t.calls == []


def test_403_is_explained_and_not_retried():
    t = ScriptedTransport(TransportResponse(403, {}, b"denied"))
    c, _, _ = client(t, max_retries=3)
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "forbidden" and e.value.http_status == 403
    assert "User-Agent" in e.value.message
    assert len(t.calls) == 1


def test_429_honours_retry_after_seconds_then_succeeds():
    t = ScriptedTransport(TransportResponse(429, {"retry-after": "3"}, b""), ok({"x": 1}))
    c, sleeps, _ = client(t, max_retries=2)
    assert c.get_json(URL).payload == {"x": 1}
    assert 3.0 in sleeps


def test_429_retry_after_http_date():
    when = format_datetime(NOW + timedelta(seconds=4), usegmt=True)
    t = ScriptedTransport(TransportResponse(429, {"retry-after": when}, b""), ok({}))
    c, sleeps, _ = client(t)
    c.get_json(URL)
    assert any(abs(s - 4.0) < 1e-6 for s in sleeps)


def test_429_retry_after_too_long_raises_with_value():
    t = ScriptedTransport(TransportResponse(429, {"retry-after": "600"}, b""))
    c, sleeps, _ = client(t, max_retries=3, max_retry_after_seconds=30)
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "rate_limited" and e.value.retry_after_seconds == 600
    assert 600 not in sleeps and len(t.calls) == 1


def test_429_exhausts_retries():
    t = ScriptedTransport(*[TransportResponse(429, {"retry-after": "1"}, b"")] * 3)
    c, _, _ = client(t, max_retries=2)
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "rate_limited" and len(t.calls) == 3


def test_5xx_retried_then_success():
    t = ScriptedTransport(TransportResponse(503, {}, b""), ok({"y": 2}))
    c, sleeps, _ = client(t, max_retries=2)
    assert c.get_json(URL).payload == {"y": 2}
    assert sleeps == [0.5]


def test_5xx_exhausted():
    t = ScriptedTransport(*[TransportResponse(502, {}, b"")] * 2)
    c, _, _ = client(t, max_retries=1)
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "server_error" and e.value.http_status == 502 and len(t.calls) == 2


def test_timeout_retried_then_raised():
    t = ScriptedTransport(socket.timeout("t"), TimeoutError("t"), URLError(socket.timeout("t")))
    c, _, _ = client(t, max_retries=2)
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "timeout" and len(t.calls) == 3


def test_network_error():
    t = ScriptedTransport(URLError("Name or service not known"))
    c, _, _ = client(t, max_retries=0)
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "network_error"


def test_404_and_other_status():
    c, _, _ = client(ScriptedTransport(TransportResponse(404, {}, b"")))
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "not_found"
    c, _, _ = client(ScriptedTransport(TransportResponse(418, {}, b"")))
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "http_error"


def test_gzip_and_invalid_json():
    body = gzip.compress(json.dumps({"z": 3}).encode())
    c, _, _ = client(ScriptedTransport(TransportResponse(200, {"content-encoding": "gzip"}, body)))
    assert c.get_json(URL).payload == {"z": 3}
    c, _, _ = client(ScriptedTransport(TransportResponse(200, {}, b"<html>")))
    with pytest.raises(FetchError) as e:
        c.get_json(URL)
    assert e.value.code == "invalid_json"


def test_throttle_spaces_requests():
    t = ScriptedTransport(ok({}), ok({}))
    c, sleeps, _ = client(t, min_interval_seconds=0.25)
    c.get_json(URL)
    c.get_json(URL + "?2")
    assert sleeps == [0.25]


def test_cache_ttl():
    t = ScriptedTransport(ok({"v": 1}), ok({"v": 2}))
    c, _, clock = client(t, cache_ttl_seconds={"https://data.sec.gov/": 60})
    assert c.get_json(URL).payload == {"v": 1}
    assert c.get_json(URL).payload == {"v": 1}
    assert len(t.calls) == 1
    clock[0] += 61
    assert c.get_json(URL).payload == {"v": 2}


def test_https_only():
    c, _, _ = client(ScriptedTransport())
    with pytest.raises(FetchError) as e:
        c.get_json("http://data.sec.gov/x.json")
    assert e.value.code == "invalid_url"


def test_parse_retry_after():
    assert parse_retry_after("5", NOW) == 5
    assert parse_retry_after(None, NOW) is None
    assert parse_retry_after("garbage", NOW) is None
    assert parse_retry_after(format_datetime(NOW - timedelta(seconds=10), usegmt=True), NOW) == 0
