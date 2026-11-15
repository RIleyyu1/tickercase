"""Join the two sides of the measured runs (Lab Manual 10.4) into one table for each run, and the statistics over the runs.

Usage:
    python scripts/timing_report.py data/timings/<folder>

Reads  <folder>/runs/run_NN.json   the user's side, written by scripts/measure_runs.py
       <folder>/timelines/*.json   the service's side, written by src/tickercase/timing.py
Writes <folder>/stages.csv         one row for each run, one column for each stage (seconds)
       <folder>/summary.json       n, min, mean, median, p90, max and standard deviation of each stage
and prints the summary as a table.

A stage is one stay inside one participant of the sequence diagram. Inside each stage the time spent waiting for an
outside service (the request spans) is separated from the time spent inside the system, and the request spacing wait of
the HTTP fetcher is shown on its own, because it is time the system chooses to spend.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

SEC, YAHOO = "SEC EDGAR", "Yahoo Finance"
CONTEXT = ("Polymarket", "CNN Fear and Greed", "Google News")
WAITS = ("request spacing wait", "retry wait")


def load(folder: Path):
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((folder / "runs").glob("run_*.json"))]
    timelines = []
    for p in sorted((folder / "timelines").glob("*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        t["epoch"] = datetime.fromisoformat(t["started_at"]).timestamp()
        timelines.append(t)
    return runs, timelines


def inside(spans, start, end):
    return [s for s in spans if start - 1e-9 <= s["start"] and s["end"] <= end + 1e-9]


def split(spans, start, end) -> dict:
    """The time of one stage, split into outside services, the spacing wait and the rest (the system's own work)."""
    mine = inside(spans, start, end)
    requests = [s for s in mine if s["stage"] == "request"]
    waits = [s for s in mine if s["stage"] in WAITS]
    by = lambda names: sum(s["end"] - s["start"] for s in requests if s["participant"] in names)  # noqa: E731
    total = end - start
    outside = sum(s["end"] - s["start"] for s in requests)
    wait = sum(s["end"] - s["start"] for s in waits)
    return {"total": total, "outside": outside, "sec": by((SEC,)), "yahoo": by((YAHOO,)), "context": by(CONTEXT),
            "wait": wait, "own": total - outside - wait, "requests": len(requests),
            "requests_sec": sum(1 for s in requests if s["participant"] == SEC),
            "requests_yahoo": sum(1 for s in requests if s["participant"] == YAHOO),
            "requests_context": sum(1 for s in requests if s["participant"] in CONTEXT)}


def stage(spans, name_starts: str):
    return next((s for s in spans if s["stage"].startswith(name_starts)), None)


def one_run(marks: dict, timelines: list) -> dict | None:
    if not marks.get("completed"):
        return None
    lo, hi = marks["t_open"] - 1, marks["t_report_ready"] + 1
    mine = [t for t in timelines if lo <= t["epoch"] <= hi]
    get = lambda kind: next((t for t in mine if t["kind"] == kind), None)  # noqa: E731
    extract, reference, evaluate = get("extract"), get("reference"), get("evaluate")
    if evaluate is None:
        return None
    row: dict = {"run": marks["run"], "status": evaluate["detail"].get("status"), "source_errors": evaluate["detail"].get("source_errors", 0),
                 "cache_hits": evaluate["detail"].get("cache_hits", 0)}
    # ---- the user's side (a script: no reading or thinking time)
    row["user_submit_script"] = marks["t_read_pressed"] - marks["t_page_ready"]
    row["user_confirm_script"] = marks["t_confirm_pressed"] - marks["t_check_ready"]
    row["user_run_script"] = marks["t_run_pressed"] - marks["t_run_enabled"]
    # ---- C.1 before the run
    row["read_to_review_page"] = marks["t_check_ready"] - marks["t_read_pressed"]
    pre = [t for t in (extract, reference) if t is not None]
    row["read_service"] = sum(t["seconds"] for t in pre)
    row["read_outside"] = sum(s["end"] - s["start"] for t in pre for s in t["spans"] if s["stage"] == "request")
    row["read_wait"] = sum(s["end"] - s["start"] for t in pre for s in t["spans"] if s["stage"] in WAITS)
    row["read_requests"] = sum(1 for t in pre for s in t["spans"] if s["stage"] == "request")
    row["confirm_to_armed_page"] = marks["t_run_enabled"] - marks["t_confirm_pressed"]
    # ---- the run
    spans = evaluate["spans"]
    row["run_to_report_page"] = marks["t_report_ready"] - marks["t_run_pressed"]
    row["evaluate_service"] = evaluate["seconds"]
    row["page_overhead"] = row["run_to_report_page"] - row["evaluate_service"]
    row["c1_validate"] = sum(s["end"] - s["start"] for s in spans if s["participant"] == "C.1" and s["depth"] == 1)
    row["c3_calculate"] = sum(s["end"] - s["start"] for s in spans if s["participant"] == "C.3" and s["depth"] == 1)
    for key, name in (("a2", "A2 Acquire"), ("a21", "A2.1"), ("a22", "A2.2"), ("a23", "A2.3"), ("a24", "A2.4"),
                      ("c5", "A5"), ("c4", "A4"), ("c6", "A6")):
        s = stage(spans, name)
        if s is None:
            continue
        for k, v in split(spans, s["start"], s["end"]).items():
            row[f"{key}_{k}"] = v
    whole = split(spans, 0.0, evaluate["seconds"])
    for k in ("outside", "sec", "yahoo", "context", "wait", "own", "requests", "requests_sec", "requests_yahoo", "requests_context"):
        row[f"run_{k}"] = whole[k]
    return row


def stats(values: list[float]) -> dict:
    v = sorted(values)
    n = len(v)
    p90 = v[min(n - 1, int(round(0.9 * (n - 1))))]
    return {"n": n, "min": v[0], "mean": statistics.fmean(v), "median": statistics.median(v), "p90": p90, "max": v[-1],
            "sd": statistics.stdev(v) if n > 1 else 0.0}


def main() -> int:
    folder = Path(sys.argv[1])
    runs, timelines = load(folder)
    rows = [r for r in (one_run(m, timelines) for m in runs) if r is not None]
    if not rows:
        print("no completed run found")
        return 1
    columns = list(rows[0].keys())
    with open(folder / "stages.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    numeric = [c for c in columns if c not in ("run", "status") and all(isinstance(r.get(c), (int, float)) for r in rows)]
    summary = {c: stats([float(r[c]) for r in rows]) for c in numeric}
    summary["_runs"] = {"asked": len(runs), "completed": len(rows), "with_source_errors": sum(1 for r in rows if r["source_errors"]),
                        "statuses": sorted({str(r["status"]) for r in rows})}
    (folder / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)} completed runs of {len(runs)}; runs with source errors: {summary['_runs']['with_source_errors']}")
    print(f"{'stage':28s} {'n':>3s} {'min':>8s} {'mean':>8s} {'median':>8s} {'p90':>8s} {'max':>8s} {'sd':>7s}")
    for c in numeric:
        s = summary[c]
        print(f"{c:28s} {s['n']:3d} {s['min']:8.3f} {s['mean']:8.3f} {s['median']:8.3f} {s['p90']:8.3f} {s['max']:8.3f} {s['sd']:7.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
