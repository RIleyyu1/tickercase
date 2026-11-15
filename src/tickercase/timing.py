"""Timeline of one case: an entry and an exit time at every participant boundary of the UC.1 sequence diagram.

Lab Manual Chapter 10.4 (instrumentation and measured behavior). The participants are the lifelines of the sequence
diagram: the six subsystems C.1 to C.6, the components of C.2, and the external services, named by host.

Nothing is recorded unless a directory is given, either to ``capture`` or in the environment variable
``TICKERCASE_TIMING_DIR``. With no directory every hook below is a no-op, so the product behaves as before.

One ``capture`` is one call of the service (read the claim, fetch the reference values, run the case). It writes one
JSON file::

    {"kind": "evaluate", "started_at": "<UTC ISO time>", "seconds": 23.4, "detail": {...},
     "spans": [{"participant": "C.2.1", "stage": "A2.1 Request Filings and Reported Facts",
                "start": 0.012, "end": 6.480, "depth": 1, ...}, ...]}

``start`` and ``end`` are seconds from the start of the capture, read from ``time.perf_counter``. ``depth`` is the
nesting level: a span of depth 2 lies inside the span of depth 1 that was open when it started.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

ENV_DIR = "TICKERCASE_TIMING_DIR"

# the external participants of the sequence diagram, by host of the request
EXTERNAL = {
    "sec.gov": "SEC EDGAR",
    "yahoo.com": "Yahoo Finance",
    "polymarket.com": "Polymarket",
    "cnn.io": "CNN Fear and Greed",
    "google.com": "Google News",
}


def external_participant(url: str) -> str:
    """The external service a URL belongs to, or its host when the service is not one of the five named ones."""
    host = urlparse(url).netloc.lower()
    for suffix, name in EXTERNAL.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return host or "unknown host"


class Timeline:
    def __init__(self, kind: str, directory: Path, detail: dict[str, Any]):
        self.kind = kind
        self.directory = directory
        self.detail = detail
        self.started_at = datetime.now(timezone.utc)
        self.t0 = time.perf_counter()
        self.spans: list[dict[str, Any]] = []
        self.depth = 0

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def add(self, participant: str, stage: str, start: float, end: float, **detail: Any) -> None:
        self.spans.append({"participant": participant, "stage": stage, "start": round(start, 6), "end": round(end, 6),
                           "depth": self.depth + 1, **detail})

    def write(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        name = f"{self.started_at.strftime('%Y%m%dT%H%M%S.%fZ')}_{self.kind}_{uuid.uuid4().hex[:8]}.json"
        path = self.directory / name
        body = {"kind": self.kind, "started_at": self.started_at.isoformat(), "seconds": round(self.now(), 6),
                "detail": self.detail, "spans": sorted(self.spans, key=lambda s: (s["start"], s["depth"]))}
        path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return path


_active: ContextVar[Optional[Timeline]] = ContextVar("tickercase_timeline", default=None)


def active() -> Optional[Timeline]:
    return _active.get()


@contextmanager
def capture(kind: str, directory: Optional[Path] = None, **detail: Any) -> Iterator[Optional[Timeline]]:
    """Record the spans of one service call and write them to a file when it ends.

    Inside another capture this is only a span of it, so a call that runs inside a recorded call is not written twice.
    """
    outer = _active.get()
    if outer is not None:
        with span("C.0", kind, **detail):
            yield outer
        return
    target = directory or (Path(os.environ[ENV_DIR]) if os.environ.get(ENV_DIR, "").strip() else None)
    if target is None:
        yield None
        return
    timeline = Timeline(kind, Path(target), dict(detail))
    token = _active.set(timeline)
    try:
        yield timeline
    finally:
        _active.reset(token)
        timeline.write()


@contextmanager
def span(participant: str, stage: str, **detail: Any) -> Iterator[None]:
    """One stay inside a participant: the entry time when the block starts, the exit time when it ends."""
    timeline = _active.get()
    if timeline is None:
        yield
        return
    start = timeline.now()
    timeline.depth += 1
    try:
        yield
    finally:
        timeline.depth -= 1
        timeline.add(participant, stage, start, timeline.now(), **detail)


def note(key: str, value: Any) -> None:
    """Attach one fact to the timeline that is being recorded (for example the status of the case)."""
    timeline = _active.get()
    if timeline is not None:
        timeline.detail[key] = value


def count(key: str) -> None:
    """Count one event on the timeline that is being recorded (for example a response served from the cache)."""
    timeline = _active.get()
    if timeline is not None:
        timeline.detail[key] = int(timeline.detail.get(key, 0)) + 1
