"""One-off live smoke test (SEC filings, SEC XBRL facts, market prices) with recording.

Usage:
    SEC_USER_AGENT="TickerCase/0.1 Your Name you@example.org" python scripts/live_smoke.py --ticker AAPL

Writes:
    data/smoke/<timestamp>_<ticker>.json        summary: company, time, URLs, result or error
    data/snapshots/smoke-<timestamp>/*.json     recorded live responses, replayable offline

Exit code 0 on success, 1 on failure (the failure is still written to the summary).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tickercase.config import load_settings  # noqa: E402
from tickercase.http_client import FetchError, LiveHttpClient, RecordingHttpClient  # noqa: E402
from tickercase.providers.base import ProviderError  # noqa: E402
from tickercase.providers.market import YahooChartProvider  # noqa: E402
from tickercase.providers.sec import SecFilingProvider, SecFilingQuery  # noqa: E402
from tickercase.providers.sec_facts import SecCompanyFactsProvider  # noqa: E402
from tickercase.service import build_fetcher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    args = parser.parse_args()

    settings = load_settings()
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    snap_dir = ROOT / "data" / "snapshots" / f"smoke-{stamp}"
    out = ROOT / "data" / "smoke" / f"{stamp}_{args.ticker.upper()}.json"
    live = LiveHttpClient(user_agent=settings.sec_user_agent, timeout_seconds=settings.timeout_seconds, max_retries=settings.max_retries, min_interval_seconds=max(settings.min_interval_seconds, 0.2))
    client = RecordingHttpClient(live, snap_dir)
    summary: dict = {"ticker": args.ticker.upper(), "started_at": started.isoformat(), "mode": "live+record", "snapshot_dir": str(snap_dir)}
    ok = False
    try:
        result = SecFilingProvider(client).fetch_filings(SecFilingQuery(ticker=args.ticker))
        summary.update(
            company_name=result.company_name,
            cik=result.cik,
            submissions_url=result.submissions_url,
            record_count=len(result.records),
            first_records=[r.model_dump(mode="json") for r in result.records[:5]],
            coverage=result.coverage.model_dump(mode="json"),
            warnings=result.warnings,
        )
        ok = bool(result.records)
        facts = SecCompanyFactsProvider(client).fetch_facts(args.ticker)
        summary["xbrl"] = {
            "url": facts.source_url,
            "latest_revenue": facts.revenue[-1].model_dump(mode="json") if facts.revenue else None,
            "latest_net_income": facts.net_income[-1].model_dump(mode="json") if facts.net_income else None,
            "latest_shares": facts.shares_outstanding[-1].model_dump(mode="json") if facts.shares_outstanding else None,
            "notes": facts.notes,
        }
        ok = ok and bool(facts.revenue or facts.net_income)
    except (FetchError, ProviderError) as exc:
        summary.update(error={"code": exc.code, "message": exc.message, "url": exc.url, "http_status": getattr(exc, "http_status", None)})
    try:
        market_client = RecordingHttpClient(build_fetcher("live", settings, "market"), snap_dir)
        m = YahooChartProvider(market_client).fetch_history(args.ticker)
        summary["market"] = {"url": m.source_url, "last_close": str(m.last_close), "last_date": str(m.last_date),
                             "observations": m.observations, "annualized_volatility": str(m.annualized_volatility)}
    except (FetchError, ProviderError) as exc:
        summary["market_error"] = {"code": exc.code, "message": exc.message, "url": exc.url}
        ok = False
    summary["requested_urls"] = live.request_log
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["snapshots_written"] = [str(p) for p in client.written]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: summary.get(k) for k in ("ticker", "company_name", "cik", "record_count", "error", "market", "market_error")}, ensure_ascii=False))
    print(f"summary: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
