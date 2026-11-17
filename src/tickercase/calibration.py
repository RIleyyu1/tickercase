"""What the main number has been worth: when the index said p, how often did the thing happen in the backtest?

The table is written by ``scripts/backtest_methods.py --export`` into ``calibration.json`` beside this file. It holds,
for each condition ("touch", "end"), the bins of the backtest: the average the index said in a bin and how often the
claim came true. ``calibrated`` reads between two bins in a straight line. The backtest has no past option prices, so
the index there is built from the volatility method and the stock's own history; the note with each number says so.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

FILE = Path(__file__).with_name("calibration.json")


@lru_cache(maxsize=1)
def table() -> Optional[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def calibrated(p: Optional[float], condition: str) -> Optional[float]:
    """How often a claim came true in the backtest when the index said ``p``; None without a table or outside its bins."""
    data = table()
    if p is None or data is None:
        return None
    bins = [(b["said"], b["happened"]) for b in data.get(condition, {}).get("bins", []) if b.get("n", 0) >= data.get("min_cases", 200)]
    if len(bins) < 2 or not bins[0][0] <= p <= bins[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(bins, bins[1:]):
        if x0 <= p <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (p - x0) / (x1 - x0)
    return None


def summary() -> Optional[dict]:
    data = table()
    return None if data is None else {k: data.get(k) for k in ("made_at", "stocks", "cases", "first", "last")}
