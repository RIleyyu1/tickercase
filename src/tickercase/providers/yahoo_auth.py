"""Yahoo Finance endpoints that need a session cookie and a "crumb" token (option chains).

The crumb is added to the request internally; callers and snapshots use the
URL without it, so a recorded option chain replays under the same key.
Unofficial endpoint: no published terms or service guarantee.
"""

from __future__ import annotations

import gzip
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.cookiejar import CookieJar
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import HTTPCookieProcessor, Request, build_opener

from ..http_client import FetchError, FetchedJson, utcnow
from ..models import DataMode

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"


@dataclass
class YahooAuthedClient:
    user_agent: str = BROWSER_UA
    timeout_seconds: float = 15.0
    min_interval_seconds: float = 0.5
    cache_ttl_seconds: float = 300.0
    now: Callable[[], datetime] = utcnow
    data_mode: DataMode = field(default=DataMode.LIVE, init=False)

    def __post_init__(self) -> None:
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._crumb: Optional[str] = None
        self._lock = threading.Lock()
        self._last = 0.0
        self._cache: dict[str, tuple[float, FetchedJson]] = {}
        self.request_log: list[str] = []

    def _open(self, url: str) -> tuple[int, bytes, dict]:
        with self._lock:
            wait = self.min_interval_seconds - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        self.request_log.append(url)
        req = Request(url, headers={"User-Agent": self.user_agent, "Accept": "*/*", "Accept-Encoding": "gzip"})
        try:
            with self._opener.open(req, timeout=self.timeout_seconds) as resp:
                return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
        except HTTPError as exc:
            return exc.code, exc.read() if hasattr(exc, "read") else b"", {}
        except (TimeoutError, socket.timeout) as exc:
            raise FetchError("timeout", f"request timed out after {self.timeout_seconds}s: {url}", url=url, data_mode=DataMode.LIVE) from exc
        except (URLError, OSError) as exc:
            raise FetchError("network_error", f"network error for {url}: {getattr(exc, 'reason', exc)}", url=url, data_mode=DataMode.LIVE) from exc

    @staticmethod
    def _body(raw: bytes, headers: dict) -> bytes:
        if headers.get("content-encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        return raw

    def _get_crumb(self) -> str:
        if self._crumb:
            return self._crumb
        self._open(COOKIE_URL)  # sets the session cookie; the page itself may answer 404
        status, raw, headers = self._open(CRUMB_URL)
        crumb = self._body(raw, headers).decode("utf-8", "replace").strip()
        if status != 200 or not crumb or "<" in crumb or len(crumb) > 64:
            raise FetchError("auth_failed", f"Yahoo did not issue a crumb (HTTP {status})", url=CRUMB_URL, http_status=status, data_mode=DataMode.LIVE)
        self._crumb = crumb
        return crumb

    @staticmethod
    def _with_crumb(url: str, crumb: str) -> str:
        parts = urlparse(url)
        query = parse_qsl(parts.query) + [("crumb", crumb)]
        return urlunparse(parts._replace(query=urlencode(query)))

    def get_json(self, url: str) -> FetchedJson:
        cached = self._cache.get(url)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_seconds:
            return cached[1]
        for attempt in range(2):
            status, raw, headers = self._open(self._with_crumb(url, self._get_crumb()))
            if status == 401 and attempt == 0:
                self._crumb = None  # expired session: get a new crumb once
                continue
            break
        if status == 404:
            raise FetchError("not_found", f"404 Not Found: {url}", url=url, http_status=404, data_mode=DataMode.LIVE)
        if status == 429:
            raise FetchError("rate_limited", f"429 Too Many Requests from {urlparse(url).netloc}", url=url, http_status=429, data_mode=DataMode.LIVE)
        if not 200 <= status < 300:
            raise FetchError("http_error", f"unexpected HTTP {status} from {url}", url=url, http_status=status, data_mode=DataMode.LIVE)
        try:
            payload: Any = json.loads(self._body(raw, headers).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
            raise FetchError("invalid_json", f"response from {url} is not valid JSON", url=url, data_mode=DataMode.LIVE) from exc
        fetched = FetchedJson(url=url, payload=payload, retrieved_at=self.now(), data_mode=DataMode.LIVE)
        self._cache[url] = (time.monotonic(), fetched)
        return fetched
