"""Measure what the AI narrative costs, using stored cases.

Usage:
    python scripts/narrative_usage.py                      # offline: fact counts and prompt size per case
    python scripts/narrative_usage.py --count              # + exact input tokens from count_tokens (free, needs a key)
    python scripts/narrative_usage.py --call --limit 1     # + real narrative calls (paid), prints usage and cost
    python scripts/narrative_usage.py --call --model claude-sonnet-5-5 --effort low --language en

Model and effort default to the settings (TICKERCASE_NARRATIVE_MODEL / TICKERCASE_NARRATIVE_EFFORT).
Real calls write to a copy of the case in memory only; stored cases are not changed.
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
from tickercase.narrative import build_facts, estimate_cost, request_params, write_narrative  # noqa: E402
from tickercase.storage import CaseStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true", help="count input tokens with the API (free)")
    parser.add_argument("--call", action="store_true", help="make real narrative calls (paid)")
    parser.add_argument("--limit", type=int, default=0, help="only the N largest cases (0 = all)")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--language", default="zh", choices=("zh", "en"))
    args = parser.parse_args()

    settings = load_settings()
    model = args.model or settings.narrative_model
    effort = args.effort or settings.narrative_effort
    cases = [(c, build_facts(c)) for c in CaseStore(settings.case_dir).list_cases()]
    cases = sorted([x for x in cases if x[1]], key=lambda x: len(x[1]), reverse=True)
    if args.limit:
        cases = cases[: args.limit]
    client = None
    if args.count or args.call:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else anthropic.Anthropic()

    print(f"model {model} · effort {effort} · language {args.language} · {len(cases)} cases")
    totals = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    for case, facts in cases:
        params = request_params(facts, model=model, effort=effort, language=args.language)
        chars = len(params["system"]) + len(params["messages"][0]["content"]) + len(json.dumps(params["output_config"]))
        line = f"{case.case_id[:8]} {case.confirmed_claim.values.ticker:<6} {len(facts):>3} facts  prompt {chars:>6} chars"
        if args.count:
            counted = client.messages.count_tokens(model=model, system=params["system"], messages=params["messages"])
            line += f"  input {counted.input_tokens:>6} tokens"
        if args.call:
            n = write_narrative(case.model_copy(deep=True), client=client, now=lambda: datetime.now(timezone.utc),
                                model=model, effort=effort, language=args.language)
            cost = estimate_cost(n.model, n.usage)
            line += f"  {n.status}  in {n.usage.get('input_tokens', 0)} out {n.usage.get('output_tokens', 0)}"
            line += f"  ${cost:.4f}" if cost is not None else "  (no price)"
            line += f"  {n.verified}/{n.total} pass, {n.unsupported} unsourced" if n.status == "ok" else f"  {n.error}"
            totals["input_tokens"] += n.usage.get("input_tokens", 0)
            totals["output_tokens"] += n.usage.get("output_tokens", 0)
            totals["cost"] += cost or 0.0
        print(line)
    if args.call and cases:
        print(f"total: in {totals['input_tokens']} out {totals['output_tokens']} ${totals['cost']:.4f} "
              f"(mean ${totals['cost'] / len(cases):.4f} per narrative)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
