"""Trade study of the acquisition mode (Lab Manual Chapter 11): swing weights, value scores, cost and sensitivity.

Usage:
    python scripts/trade_study.py                      # prints the tables as Markdown
    python scripts/trade_study.py --json out.json      # also writes every number to a file

Inputs:
    docs/decisions/0001-inputs.json   the team's judgments (importance of each criterion, hours) and the measured or
                                      sourced figure of each alternative on each criterion, with where it came from
    data/timings/ch11_*               the measured runs (scripts/measure_runs.py); when the folders are present the
                                      times are read from them and must agree with the file above

Method (swing weight matrix): a criterion's weight comes from two things, how much it matters to the customer
(importance) and how far the alternatives differ on it (variation). A criterion that matters a great deal but on which
the alternatives are equal gets a low weight, because it cannot tell them apart. Each raw figure is turned into a value
from 0 to 100 by the criterion's objective function (a straight line between a worst and a best level), and the
weighted values are added.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "docs" / "decisions" / "0001-inputs.json"


def value(raw: float, worst: float, best: float) -> float:
    """The objective function of a criterion: 0 at the worst level, 100 at the best, a straight line between, cut at both ends."""
    if best == worst:
        return 100.0
    return max(0.0, min(100.0, 100.0 * (raw - worst) / (best - worst)))


def measured_times(folder: Path) -> list[float]:
    out = []
    for f in sorted(glob.glob(str(folder / "runs" / "run_*.json"))):
        r = json.loads(Path(f).read_text(encoding="utf-8"))
        if r.get("completed"):
            out.append(r["t_report_ready"] - r["t_run_pressed"])
    return out


def scores(inputs: dict, weights: dict[str, float]) -> dict[str, dict]:
    total = sum(weights.values())
    out = {}
    for alt in inputs["alternatives"]:
        parts = {}
        for c in inputs["criteria"]:
            v = value(c["raw"][alt["id"]], c["worst"], c["best"])
            parts[c["id"]] = {"raw": c["raw"][alt["id"]], "value": v, "weighted": v * weights[c["id"]] / total}
        out[alt["id"]] = {"parts": parts, "total": sum(p["weighted"] for p in parts.values())}
    return out


def swing_weights(inputs: dict) -> dict[str, float]:
    matrix = inputs["swing_matrix"]
    return {c["id"]: float(matrix[c["variation"]][c["importance"]]) for c in inputs["criteria"]}


def table(head: list[str], rows: list[list]) -> str:
    line = lambda cells: "| " + " | ".join(str(x) for x in cells) + " |"  # noqa: E731
    return "\n".join([line(head), line(["---"] * len(head))] + [line(r) for r in rows])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    alts = inputs["alternatives"]
    names = {a["id"]: a["name"] for a in alts}
    result: dict = {"measured": {}}

    print("## Measured time from RUN to the report (seconds)\n")
    rows = []
    for a in alts:
        folder = ROOT / "data" / "timings" / a["timing_folder"]
        times = measured_times(folder)
        if not times:
            rows.append([a["id"], a["name"], "folder not present", "", "", "", ""])
            continue
        mean = statistics.mean(times)
        result["measured"][a["id"]] = {"runs": len(times), "mean": mean, "min": min(times), "max": max(times), "sd": statistics.pstdev(times),
                                       "over_30": sum(t > 30 for t in times)}
        rows.append([a["id"], a["name"], len(times), f"{mean:.1f}", f"{min(times):.1f}", f"{max(times):.1f}", sum(t > 30 for t in times)])
        time_raw = next(c for c in inputs["criteria"] if c["id"] == "EC.1")["raw"][a["id"]]
        assert abs(time_raw - round(mean, 1)) < 0.05, (a["id"], time_raw, mean)     # the inputs file must say what was measured
    print(table(["Alt.", "Alternative", "Runs", "Mean", "Min", "Max", "Runs over 30 s"], rows))

    weights = swing_weights(inputs)
    total = sum(weights.values())
    print("\n## Swing weight matrix\n")
    levels, widths = inputs["importance_levels"], inputs["variation_levels"]
    cells = {(c["variation"], c["importance"]): [] for c in inputs["criteria"]}
    for c in inputs["criteria"]:
        cells[(c["variation"], c["importance"])].append(c["id"])
    print(table(["Variation between the alternatives"] + [f"{k}: {v}" for k, v in levels.items()],
                [[f"{w}: {text}"] + [f"{inputs['swing_matrix'][w][k]}" + (f" ({', '.join(cells[(w, k)])})" if (w, k) in cells else "") for k in levels]
                 for w, text in widths.items()]))
    print("\n## Criteria, objective functions and weights\n")
    print(table(["Id", "Criterion", "Metric (unit)", "Worst (value 0)", "Best (value 100)", "Importance", "Variation", "Swing weight", "Share"],
                [[c["id"], c["name"], f"{c['metric']} ({c['unit']})", c["worst"], c["best"], c["importance"], c["variation"],
                  f"{weights[c['id']]:.0f}", f"{100 * weights[c['id']] / total:.1f}%"] for c in inputs["criteria"]]))

    base = scores(inputs, weights)
    result["weights"], result["scores"] = weights, base
    print("\n## Raw figures and values (0 to 100)\n")
    print(table(["Id", "Criterion"] + [f"{a['id']} raw" for a in alts] + [f"{a['id']} value" for a in alts],
                [[c["id"], c["name"]] + [c["raw"][a["id"]] for a in alts] + [f"{base[a['id']]['parts'][c['id']]['value']:.1f}" for a in alts]
                 for c in inputs["criteria"]]))
    print("\n## Weighted value\n")
    print(table(["Id", "Criterion", "Share"] + [a["id"] for a in alts],
                [[c["id"], c["name"], f"{100 * weights[c['id']] / total:.1f}%"] + [f"{base[a['id']]['parts'][c['id']]['weighted']:.1f}" for a in alts]
                 for c in inputs["criteria"]] + [["", "**Total value**", "100%"] + [f"**{base[a['id']]['total']:.1f}**" for a in alts]]))

    # effectiveness (every criterion but the cost), life cycle cost, affordability
    cost_id = inputs["cost_criterion"]
    eff_w = {k: v for k, v in weights.items() if k != cost_id}
    eff_inputs = dict(inputs, criteria=[c for c in inputs["criteria"] if c["id"] != cost_id])
    eff = scores(eff_inputs, eff_w)
    lcc = inputs["life_cycle_cost"]
    print("\n## Effectiveness, life cycle cost and affordability\n")
    rows = []
    result["cost"] = {}
    for a in alts:
        h = lcc["hours"][a["id"]]
        hours = h["build"] + h["upkeep_per_year"] * lcc["years"]
        dollars = hours * lcc["rate_usd_per_hour"] + lcc["paid_services_usd_per_year"][a["id"]] * lcc["years"]
        gain = eff[a["id"]]["total"] - eff[alts[0]["id"]]["total"]
        result["cost"][a["id"]] = {"hours": hours, "usd": dollars, "effectiveness": eff[a["id"]]["total"]}
        rows.append([a["id"], a["name"], f"{eff[a['id']]['total']:.1f}", h["build"], h["upkeep_per_year"], f"{hours:g}", f"{dollars:,.0f}",
                     "within" if hours <= lcc["budget_hours"] else "over", "baseline" if a is alts[0] else (f"{gain / hours:.2f}" if hours else "n/a")])
    print(table(["Alt.", "Alternative", "Effectiveness (0 to 100)", "Build hours", "Upkeep hours a year", f"Hours over {lcc['years']} years",
                 f"Cost at ${lcc['rate_usd_per_hour']}/h (USD)", f"Against the budget of {lcc['budget_hours']} h", "Effectiveness gained per hour"], rows))

    # sensitivity: move one weight at a time across the whole matrix range and see who wins
    print("\n## Sensitivity: the winner as one weight changes (the others stay)\n")
    lo = min(min(r.values()) for r in inputs["swing_matrix"].values())
    hi = max(max(r.values()) for r in inputs["swing_matrix"].values())
    rows = []
    result["sensitivity"] = {}
    best_now = max(base, key=lambda k: base[k]["total"])
    for c in inputs["criteria"]:
        winners, flips = [], []
        prev = None
        for w in range(0, 201):
            trial = dict(weights, **{c["id"]: float(w)})
            s = scores(inputs, trial)
            win = max(s, key=lambda k: s[k]["total"])
            if prev is not None and win != prev:
                flips.append((w, prev, win))
            prev = win
            if w in (lo, weights[c["id"]], hi):
                winners.append((w, win))
        result["sensitivity"][c["id"]] = {"flips": flips}
        rows.append([c["id"], c["name"], f"{weights[c['id']]:.0f}",
                     "; ".join(f"at {w}: {a} to {b}" for w, a, b in flips) or f"none between 0 and 200 ({best_now} throughout)",
                     ", ".join(f"{w:g} → {win}" for w, win in dict(winners).items())])
    print(table(["Id", "Criterion", "Weight now", "Weight at which the winner changes", f"Winner at the lowest ({lo}), the present and the highest ({hi}) matrix weight"], rows))
    print(f"\nSelected: **{best_now} {names[best_now]}** (total value {base[best_now]['total']:.1f}).")
    result["selected"] = best_now
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
