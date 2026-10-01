"""Single network boundary for TickerCase.

Every external JSON request goes through a ``JsonFetcher``:

- ``LiveHttpClient``    real HTTPS with User-Agent, timeout, bounded retries,
                        throttling, Retry-After handling and an in-memory TTL cache
- ``RecordingHttpClient`` live requests that also write a snapshot per URL
- ``ReplayHttpClient``  offline reads of recorded snapshots; a missing snapshot
                        is an error and never falls back to the network
- ``FakeHttpClient``    in-memory responses for tests and synthetic demos

Design reference: the client/recording/replay split follows the approach in
komako-workshop/digital-oracle (digital_oracle/http.py, digital_oracle/snapshots.py,
commit a63e4c19a2f3313d54914c44666febaf5ffb9d6f, MIT). The code here is new;
it adds atomic UTF-8 snapshot writes, original-capture timestamps on replay,
explicit data modes and typed HTTP errors.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import DataMode

SNAPSHOT_SCHEMA = "tickercase.snapshot/1"
ORIGIN_LIVE = "live_recorded"
ORIGIN_SYNTHETIC = "synthetic_fixture"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FetchError(RuntimeError):
    """Structured network/data-access failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: Optional[str] = None,
        http_status: Optional[int] = None,
        retry_after_seconds: Optional[float] = None,
        data_mode: Optional[DataMode] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.url = url
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.data_mode = data_mode


@dataclass(frozen=True)
class FetchedJson:
    url: str
    payload: Any
    retrieved_at: datetime
    data_mode: DataMode
    source_captured_at: Optional[datetime] = None
    snapshot_origin: Optional[str] = None


class JsonFetcher(Protocol):
    data_mode: DataMode

    def get_json(self, url: str) -> FetchedJson: ...


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, Mapping[str, str], float], TransportResponse]


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> TransportResponse:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - https URLs only, checked by caller
            return TransportResponse(response.status, {k.lower(): v for k, v in response.headers.items()}, response.read())
    except HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        hdrs = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
        return TransportResponse(exc.code, hdrs, body or b"")


def parse_retry_after(value: Optional[str], now: datetime) -> Optional[float]:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())


def validate_user_agent(user_agent: Optional[str]) -> Optional[str]:
    """Return an error message if the User-Agent does not meet SEC's declared-client requirement."""
    ua = (user_agent or "").strip()
    if not ua:
        return (
            "SEC_USER_AGENT is not set. SEC requires automated clients to declare a User-Agent "
            "with an application name and a contact email (e.g. 'TickerCase/0.1 Jane Doe jane@example.org'). "
            "Set it in .env; no default is used."
        )
    if "@" not in ua:
        return "SEC_USER_AGENT must include a contact email address you control."
    return None


@dataclass
class LiveHttpClient:
    user_agent: Optional[str]
    timeout_seconds: float = 10.0
    max_retries: int = 2
    min_interval_seconds: float = 0.2
    max_retry_after_seconds: float = 30.0
    backoff_seconds: float = 1.0
    cache_ttl_seconds: Mapping[str, float] = field(default_factory=dict)
    require_contact_email: bool = True  # SEC fair-access rule; other sources only need a non-empty User-Agent
    service_name: str = "SEC"
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    transport: Transport = urllib_transport
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = utcnow

    data_mode: DataMode = field(default=DataMode.LIVE, init=False)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request: Optional[float] = None
        self._cache: dict[str, tuple[float, FetchedJson]] = {}
        self.request_log: list[str] = []

    def _ttl_for(self, url: str) -> float:
        best = 0.0
        best_len = -1
        for prefix, ttl in self.cache_ttl_seconds.items():
            if url.startswith(prefix) and len(prefix) > best_len:
                best, best_len = ttl, len(prefix)
        return best

    def _throttle(self) -> None:
        with self._lock:
            if self._last_request is not None:
                wait = self.min_interval_seconds - (self.monotonic() - self._last_request)
                if wait > 0:
                    self.sleep(wait)
            self._last_request = self.monotonic()

    def get_json(self, url: str) -> FetchedJson:
        if self.require_contact_email:
            problem = validate_user_agent(self.user_agent)
        else:
            problem = None if (self.user_agent or "").strip() else f"no User-Agent configured for {self.service_name}"
        if problem:
            raise FetchError("missing_user_agent", problem, url=url, data_mode=DataMode.LIVE)
        if urlparse(url).scheme != "https":
            raise FetchError("invalid_url", f"only https URLs are allowed: {url}", url=url, data_mode=DataMode.LIVE)

        ttl = self._ttl_for(url)
        cached = self._cache.get(url)
        if cached and ttl > 0 and self.monotonic() - cached[0] < ttl:
            return cached[1]

        headers = {
            "User-Agent": self.user_agent.strip(),
            "Accept": "application/json, text/xml;q=0.9, */*;q=0.5",
            "Accept-Encoding": "gzip",
            **self.extra_headers,
        }
        last_error: Optional[FetchError] = None
        for attempt in range(self.max_retries + 1):
            is_last = attempt >= self.max_retries
            self._throttle()
            self.request_log.append(url)
            try:
                response = self.transport(url, headers, self.timeout_seconds)
            except (TimeoutError, socket.timeout) as exc:
                last_error = FetchError("timeout", f"request timed out after {self.timeout_seconds}s: {url}", url=url, data_mode=DataMode.LIVE)
                last_error.__cause__ = exc
                if not is_last:
                    self.sleep(self.backoff_seconds * (2 ** attempt))
                continue
            except (URLError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    code, msg = "timeout", f"request timed out after {self.timeout_seconds}s: {url}"
                else:
                    code, msg = "network_error", f"network error for {url}: {reason}"
                last_error = FetchError(code, msg, url=url, data_mode=DataMode.LIVE)
                if not is_last:
                    self.sleep(self.backoff_seconds * (2 ** attempt))
                continue

            status = response.status
            if 200 <= status < 300:
                fetched = FetchedJson(url=url, payload=self._decode(url, response), retrieved_at=self.now(), data_mode=DataMode.LIVE)
                if ttl > 0:
                    self._cache[url] = (self.monotonic(), fetched)
                return fetched
            if status == 403:
                if self.service_name == "SEC":
                    message = (
                        "SEC returned 403 Forbidden. Common causes: missing or generic User-Agent without a contact email, "
                        "request rate above SEC's fair-access limit, or a network/firewall that blocks sec.gov. "
                    )
                else:
                    message = f"{self.service_name} returned 403 Forbidden (access refused by the source or a network filter). "
                raise FetchError("forbidden", message + f"URL: {url}", url=url, http_status=403, data_mode=DataMode.LIVE)
            if status == 404:
                raise FetchError("not_found", f"404 Not Found: {url}", url=url, http_status=404, data_mode=DataMode.LIVE)
            if status == 429:
                retry_after = parse_retry_after(response.headers.get("retry-after"), self.now())
                last_error = FetchError(
                    "rate_limited",
                    f"429 Too Many Requests from {urlparse(url).netloc}"
                    + (f"; Retry-After {retry_after:.0f}s" if retry_after is not None else ""),
                    url=url, http_status=429, retry_after_seconds=retry_after, data_mode=DataMode.LIVE,
                )
                wait = retry_after if retry_after is not None else self.backoff_seconds * (2 ** attempt)
                if is_last or wait > self.max_retry_after_seconds:
                    raise last_error
                self.sleep(wait)
                continue
            if 500 <= status < 600:
                last_error = FetchError("server_error", f"{status} server error from {url}", url=url, http_status=status, data_mode=DataMode.LIVE)
                if not is_last:
                    self.sleep(self.backoff_seconds * (2 ** attempt))
                continue
            raise FetchError("http_error", f"unexpected HTTP {status} from {url}", url=url, http_status=status, data_mode=DataMode.LIVE)

        assert last_error is not None
        raise last_error

    @staticmethod
    def _decode(url: str, response: TransportResponse) -> Any:
        body = response.body
        if response.headers.get("content-encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except OSError as exc:
                raise FetchError("invalid_payload", f"could not decompress gzip body from {url}", url=url, data_mode=DataMode.LIVE) from exc
        if urlparse(url).path.lower().endswith(".xml"):
            return body.decode("utf-8", "replace")  # SEC ownership documents (Form 4) are XML; kept as text
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError("invalid_json", f"response from {url} is not valid JSON", url=url, data_mode=DataMode.LIVE) from exc


# ---------------------------------------------------------------- snapshots


def snapshot_filename(url: str) -> str:
    key = json.dumps({"method": "GET", "url": url}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    tail = Path(urlparse(url).path).name or "root"
    safe = "".join(c if c.isalnum() else "_" for c in tail).strip("_")[:60] or "root"
    return f"{safe}__{digest}.json"


def _replace_with_retry(src: str, dst: Path, attempts: int = 50, delay: float = 0.01) -> None:
    """``os.replace`` with a bounded retry: on Windows a concurrent replace of the
    same target raises a transient PermissionError instead of being atomic."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(delay)


def write_snapshot(
    snapshot_dir: Union[str, Path],
    url: str,
    payload: Any,
    *,
    captured_at: datetime,
    origin: str = ORIGIN_LIVE,
    note: Optional[str] = None,
) -> Path:
    """Atomically write a UTF-8 snapshot. Concurrent writers of the same URL never leave a partial file."""
    directory = Path(snapshot_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / snapshot_filename(url)
    envelope = {
        "schema": SNAPSHOT_SCHEMA,
        "origin": origin,
        "request": {"method": "GET", "url": url},
        "captured_at": captured_at.isoformat(),
        "response": payload,
    }
    if note:
        envelope["note"] = note
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(envelope, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return target


class RecordingHttpClient:
    """Live client that writes a snapshot for every successful response."""

    def __init__(self, live: LiveHttpClient, snapshot_dir: Union[str, Path]):
        self.live = live
        self.snapshot_dir = Path(snapshot_dir)
        self.data_mode = DataMode.LIVE
        self.written: list[Path] = []

    def get_json(self, url: str) -> FetchedJson:
        fetched = self.live.get_json(url)
        self.written.append(write_snapshot(self.snapshot_dir, url, fetched.payload, captured_at=fetched.retrieved_at))
        return FetchedJson(
            url=url, payload=fetched.payload, retrieved_at=fetched.retrieved_at, data_mode=DataMode.LIVE,
            source_captured_at=fetched.retrieved_at, snapshot_origin=ORIGIN_LIVE,
        )


class ReplayHttpClient:
    """Offline client. Missing snapshots raise; there is no live fallback."""

    def __init__(self, snapshot_dir: Union[str, Path], *, now: Callable[[], datetime] = utcnow):
        self.snapshot_dir = Path(snapshot_dir)
        self.now = now
        self.data_mode = DataMode.REPLAY

    def get_json(self, url: str) -> FetchedJson:
        path = self.snapshot_dir / snapshot_filename(url)
        if not path.is_file():
            raise FetchError(
                "snapshot_missing",
                f"no snapshot for {url} in {self.snapshot_dir} (replay mode does not call the network)",
                url=url, data_mode=DataMode.REPLAY,
            )
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FetchError("snapshot_corrupt", f"snapshot {path.name} is unreadable", url=url, data_mode=DataMode.REPLAY) from exc
        if not isinstance(envelope, Mapping) or envelope.get("schema") != SNAPSHOT_SCHEMA:
            raise FetchError("snapshot_corrupt", f"snapshot {path.name} has an unknown format", url=url, data_mode=DataMode.REPLAY)
        request = envelope.get("request") or {}
        if request.get("url") != url:
            raise FetchError("snapshot_corrupt", f"snapshot {path.name} was recorded for a different URL", url=url, data_mode=DataMode.REPLAY)
        origin = envelope.get("origin")
        captured_raw = envelope.get("captured_at")
        captured = datetime.fromisoformat(captured_raw) if isinstance(captured_raw, str) else None
        mode = DataMode.SYNTHETIC if origin == ORIGIN_SYNTHETIC else DataMode.REPLAY
        return FetchedJson(
            url=url, payload=envelope.get("response"), retrieved_at=self.now(), data_mode=mode,
            source_captured_at=captured, snapshot_origin=origin,
        )


class FakeHttpClient:
    """In-memory responses keyed by URL. Values may be payloads or FetchError instances."""

    def __init__(self, responses: Mapping[str, Any], *, data_mode: DataMode = DataMode.SYNTHETIC, now: Callable[[], datetime] = utcnow):
        self.responses = dict(responses)
        self.data_mode = data_mode
        self.now = now
        self.calls: list[str] = []

    def get_json(self, url: str) -> FetchedJson:
        self.calls.append(url)
        if url not in self.responses:
            raise FetchError("not_found", f"404 Not Found: {url}", url=url, http_status=404, data_mode=self.data_mode)
        value = self.responses[url]
        if isinstance(value, FetchError):
            raise value
        return FetchedJson(url=url, payload=value, retrieved_at=self.now(), data_mode=self.data_mode)
