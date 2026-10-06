"""Settings from environment variables, with an optional .env file.

Existing environment variables win over .env values. There is no default SEC
User-Agent: live requests fail with a clear message until one is configured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SEC_MODES = ("live", "record", "replay", "synthetic")  # data modes; apply to every external source
DEFAULT_SYNTHETIC_DIR = REPO_ROOT / "examples" / "sec_synthetic_snapshots"
NARRATIVE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def write_env_value(key: str, value: str, path: Optional[Path] = None) -> Path:
    """Set one KEY=value line in the local .env file (created if missing), keeping the other lines."""
    if not key.isidentifier() or "\n" in value or "\r" in value or '"' in value:
        raise ValueError("invalid .env key or value")
    target = path or REPO_ROOT / ".env"
    lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    entry = f'{key}="{value}"' if " " in value else f"{key}={value}"
    out, done = [], False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == key:
            if not done:
                out.append(entry)
                done = True
            continue
        out.append(line)
    if not done:
        out.append(entry)
    target.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return target


@dataclass(frozen=True)
class Settings:
    sec_user_agent: Optional[str]
    sec_mode: str
    snapshot_dir: Path
    synthetic_dir: Path
    case_dir: Path
    timeout_seconds: float
    max_retries: int
    min_interval_seconds: float
    max_retry_after_seconds: float
    market_user_agent: str = "TickerCase/0.2"
    anthropic_api_key: Optional[str] = None
    market_min_interval_seconds: float = 0.5
    narrative_model: str = "claude-opus-5-5"
    narrative_effort: str = "medium"


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def load_settings(env: Optional[Mapping[str, str]] = None, dotenv_path: Optional[Path] = None) -> Settings:
    merged: MutableMapping[str, str] = {}
    merged.update(read_dotenv(dotenv_path or REPO_ROOT / ".env"))
    merged.update(os.environ if env is None else env)
    mode = merged.get("TICKERCASE_SEC_MODE", "live").strip().lower() or "live"
    if mode not in SEC_MODES:
        raise ValueError(f"TICKERCASE_SEC_MODE must be one of {SEC_MODES}, got '{mode}'")
    effort = (merged.get("TICKERCASE_NARRATIVE_EFFORT") or "").strip().lower() or "medium"
    if effort not in NARRATIVE_EFFORTS:
        raise ValueError(f"TICKERCASE_NARRATIVE_EFFORT must be one of {NARRATIVE_EFFORTS}, got '{effort}'")
    return Settings(
        sec_user_agent=(merged.get("SEC_USER_AGENT") or "").strip() or None,
        sec_mode=mode,
        snapshot_dir=_path(merged.get("TICKERCASE_SNAPSHOT_DIR", "data/snapshots/live")),
        synthetic_dir=_path(merged["TICKERCASE_SYNTHETIC_DIR"]) if merged.get("TICKERCASE_SYNTHETIC_DIR") else DEFAULT_SYNTHETIC_DIR,
        case_dir=_path(merged.get("TICKERCASE_CASE_DIR", "data/cases")),
        timeout_seconds=float(merged.get("SEC_TIMEOUT_SECONDS", "10")),
        max_retries=int(merged.get("SEC_MAX_RETRIES", "2")),
        min_interval_seconds=float(merged.get("SEC_MIN_INTERVAL_SECONDS", "0.2")),
        max_retry_after_seconds=float(merged.get("SEC_MAX_RETRY_AFTER_SECONDS", "30")),
        market_user_agent=(merged.get("TICKERCASE_MARKET_USER_AGENT") or "").strip() or "TickerCase/0.2",
        market_min_interval_seconds=float(merged.get("TICKERCASE_MARKET_MIN_INTERVAL_SECONDS", "0.5")),
        anthropic_api_key=(merged.get("ANTHROPIC_API_KEY") or "").strip() or None,
        narrative_model=(merged.get("TICKERCASE_NARRATIVE_MODEL") or "").strip() or "claude-opus-5-5",
        narrative_effort=effort,
    )
