"""Backtest of the two methods that can be replayed on past prices: M2 (the stock's own volatility) and M4 (its own history).

Question: when a method said "30 %", did the thing happen about 30 % of the time?

Usage:
    python scripts/backtest_methods.py            # fetch daily prices (one request per stock), write data/backtest/<stamp>/
    python scripts/backtest_methods.py --out DIR

For every stock, on the last trading day of every quarter since 2006, and for horizons of 1, 2 and 3 years and rises of
10, 25, 50 and 100 %, it computes with the product's own functions what each method would have said on that day, using
only prices up to that day, and then looks at what the price did:

    M2  oracle.p_end_above / oracle.p_touch with the volatility of the five years before (as the product does) and the
        10-year Treasury yield of that day
    M4  providers.market.price_base_rate / price_touch_hits on the month-end closes up to that day

M1 (option prices) cannot be replayed: past option chains are not in any free source. M3 is about the business, not the price.

What limits the result, in the order that matters:
  * The stocks are the largest US companies of 2005 that still trade under the same symbol. Companies that failed or were
    bought are missing, so rises are somewhat more frequent here than they were for an investor of 2005.
  * The cases overlap: quarterly starts with horizons of up to three years, and stocks that move together. The number of
    rows is large; the number of independent observations is small (about seven non-overlapping three-year periods).
  * "Reached at any time" is judged on daily closes; the product's M4 counts month-end closes, so both are reported.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from bisect import bisect_right
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tickercase.acquisition import build_fetcher  # noqa: E402
from tickercase.config import load_settings  # noqa: E402
from tickercase.oracle import MIN_INDEPENDENT_WINDOWS, METHOD_WEIGHTS, p_end_above, p_touch, weighted_index  # noqa: E402
from tickercase.providers.market import annualized_volatility, daily_closes, price_base_rate, price_touch_hits  # noqa: E402

# the largest US companies by market value around the end of 2005 that still trade under the same symbol
UNIVERSE = ("XOM GE MSFT C BAC PG JNJ PFE WMT AIG MO INTC JPM CVX IBM CSCO WFC KO PEP VZ T HD AMGN COP UPS ABT MRK ORCL UNH MDT "
            "LLY QCOM MMM DIS AXP BA MS GS TXN EBAY CMCSA HPQ MCD LOW TGT CAT UNP BMY SLB OXY EMR HON USB AAPL GOOGL").split()
HORIZONS = (1, 2, 3)
RISES = (0.10, 0.25, 0.50, 1.00)
FIRST_YEAR = 2006
VOL_DAYS = 1260  # five years of trading days, the window of the product's price history
HOT_RETURN = 2.0  # a price that at least tripled in the five years before
DAILY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=0&period2=9999999999&interval=1d"


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def month_ends(days: list[date], closes: list[float]) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    for d, c in zip(days, closes):
        if out and (out[-1][0].year, out[-1][0].month) == (d.year, d.month):
            out[-1] = (d, c)
        else:
            out.append((d, c))
    return out


def quarter_ends(days: list[date]) -> list[int]:
    """Index of the last trading day of each quarter."""
    out = []
    for i in range(len(days) - 1):
        a, b = days[i], days[i + 1]
        if a.month in (3, 6, 9, 12) and (b.month != a.month or b.year != a.year):
            out.append(i)
    return out


def one_stock(symbol: str, days: list[date], closes: list[float], rate_days: list[date], rates: list[float]) -> list[dict]:
    rows = []
    monthly = month_ends(days, closes)
    month_dates = [d for d, _ in monthly]
    last_day = days[-1]
    for i in quarter_ends(days):
        t = days[i]
        if t.year < FIRST_YEAR or i < VOL_DAYS:
            continue
        spot = closes[i]
        vol = annualized_volatility(closes[i - VOL_DAYS: i + 1])
        j = bisect_right(rate_days, t) - 1
        if vol is None or vol <= 0 or j < 0:
            continue
        r = rates[j] / 100
        past = monthly[: bisect_right(month_dates, t)]
        for h in HORIZONS:
            end_day = add_years(t, h)
            if end_day > last_day:
                continue
            k_end = bisect_right(days, end_day) - 1
            window = closes[i + 1: k_end + 1]
            if not window:
                continue
            m_lo, m_hi = bisect_right(month_dates, t), bisect_right(month_dates, end_day)
            window_me = [c for _, c in monthly[m_lo: m_hi]]
            months = h * 12
            for rise in RISES:
                level = spot * (1 + rise)
                windows, hits, _, _ = price_base_rate(past, months, Decimal(str(rise)))
                touch_hits = price_touch_hits(past, months, Decimal(str(rise))) if windows else 0
                indep = (windows + months) // months if windows else 0
                m2_end, m2_touch = p_end_above(spot, level, vol, h, r), p_touch(spot, level, vol, h, r)
                m4_end, m4_touch = (hits / windows, touch_hits / windows) if windows else (None, None)
                own = bool(windows) and indep >= MIN_INDEPENDENT_WINDOWS

                def index(market: float, history) -> float:
                    # the product's index with its own weights; past option prices do not exist, so the volatility
                    # method stands in for the option method (the two are usually close)
                    values = {"M1": market, "M2": market} | ({"M4": history} if own else {})
                    return weighted_index(values)[0]

                rows.append({
                    "symbol": symbol, "date": t.isoformat(), "years": h, "rise": rise, "vol": round(vol, 4), "rate": round(r, 4),
                    "past_5y_return": round(spot / closes[i - VOL_DAYS] - 1, 4),
                    "m2_end": m2_end, "m2_touch": m2_touch, "m4_end": m4_end, "m4_touch": m4_touch,
                    "index_end": index(m2_end, m4_end), "index_touch": index(m2_touch, m4_touch),
                    "m4_independent": indep,
                    "real_end": int(window[-1] >= level), "real_touch": int(max(window) >= level),
                    "real_touch_month_end": int(bool(window_me) and max(window_me) >= level),
                })
    return rows


def score(pairs: list[tuple[float, int]]) -> dict:
    """Brier score, the same for a constant forecast of the overall frequency, and ten calibration bins."""
    n = len(pairs)
    freq = sum(y for _, y in pairs) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    reference = freq * (1 - freq)
    bins = []
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        inside = [(p, y) for p, y in pairs if lo <= p < hi or (b == 9 and p == 1.0)]
        if inside:
            bins.append({"from": lo, "to": hi, "n": len(inside), "said": statistics.fmean(p for p, _ in inside),
                         "happened": statistics.fmean(y for _, y in inside)})
    return {"n": n, "said": statistics.fmean(p for p, _ in pairs), "happened": freq, "brier": brier, "reference_brier": reference,
            "skill": 1 - brier / reference if reference > 0 else None, "bins": bins}


def summarise(rows: list[dict]) -> dict:
    out: dict = {}
    views = {
        "M2 end": ("m2_end", "real_end", None), "M2 touch": ("m2_touch", "real_touch", None),
        "M4 end": ("m4_end", "real_end", MIN_INDEPENDENT_WINDOWS), "M4 touch (daily closes)": ("m4_touch", "real_touch", MIN_INDEPENDENT_WINDOWS),
        "M4 touch (month-end closes)": ("m4_touch", "real_touch_month_end", MIN_INDEPENDENT_WINDOWS),
        "Index end": ("index_end", "real_end", None), "Index touch": ("index_touch", "real_touch", None),
    }
    for name, (said, real, need) in views.items():
        use = [r for r in rows if r[said] is not None and (need is None or r["m4_independent"] >= need)]
        if not use:
            continue
        entry = score([(r[said], r[real]) for r in use])
        entry["by_years"] = {h: {k: v for k, v in score([(r[said], r[real]) for r in use if r["years"] == h]).items() if k != "bins"} for h in HORIZONS}
        entry["by_rise"] = {str(x): {k: v for k, v in score([(r[said], r[real]) for r in use if r["rise"] == x]).items() if k != "bins"} for x in RISES}
        # the stocks people ask about: those that had already at least tripled in the five years before
        hot = [(r[said], r[real]) for r in use if r["past_5y_return"] >= HOT_RETURN]
        rest = [(r[said], r[real]) for r in use if r["past_5y_return"] < HOT_RETURN]
        entry["after_tripling"] = {k: v for k, v in score(hot).items() if k != "bins"} if hot else None
        entry["others"] = {k: v for k, v in score(rest).items() if k != "bins"} if rest else None
        out[name] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--export", action="store_true", help="write the calibration table the product reads (src/tickercase/calibration.json)")
    args = ap.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else ROOT / "data" / "backtest" / stamp
    out.mkdir(parents=True, exist_ok=True)
    fetcher = build_fetcher("live", load_settings(), "market")
    url = DAILY_URL.format(symbol="%5ETNX")
    tnx = daily_closes(fetcher.get_json(url).payload, url)
    rate_days, rates = [d for d, _ in tnx], [c for _, c in tnx]
    symbols = args.symbols.split() if args.symbols else list(UNIVERSE)
    rows: list[dict] = []
    used, skipped = [], []
    for s in symbols:
        url = DAILY_URL.format(symbol=s)
        try:
            series = daily_closes(fetcher.get_json(url).payload, url)
        except Exception as exc:  # one stock without data does not stop the run
            skipped.append((s, f"{type(exc).__name__}: {exc}"))
            continue
        days, closes = [d for d, _ in series], [c for _, c in series]
        got = one_stock(s, days, closes, rate_days, rates)
        rows += got
        used.append(s)
        print(f"{s:6s} {days[0]} to {days[-1]}  {len(got)} cases", flush=True)
    if not rows:
        print("no cases")
        return 1
    with open(out / "cases.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()})
    summary = {"made_at": stamp, "stocks": used, "skipped": skipped, "cases": len(rows), "first": min(r["date"] for r in rows),
               "last": max(r["date"] for r in rows), "horizons_years": HORIZONS, "rises": RISES, "methods": summarise(rows)}
    if args.export:
        def bins(name):
            return [{k: round(v, 4) if isinstance(v, float) else v for k, v in b.items()} for b in summary["methods"][name]["bins"]]

        table = {"made_at": stamp, "stocks": len(used), "cases": len(rows), "first": summary["first"], "last": summary["last"], "min_cases": 1000,
                 "weights": METHOD_WEIGHTS,
                 "note": "index of the product's weights with the volatility method standing in for option prices; survivors only",
                 "touch": {"bins": bins("Index touch")}, "end": {"bins": bins("Index end")}}
        target = ROOT / "src" / "tickercase" / "calibration.json"
        target.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
        print("calibration table written to", target)
    # for information: the share of the history that would have scored best beside the volatility method
    both = [r for r in rows if r["m4_touch"] is not None and r["m4_independent"] >= MIN_INDEPENDENT_WINDOWS]
    best = min((sum(((1 - w) * r["m2_touch"] + w * r["m4_touch"] - r["real_touch"]) ** 2 for r in both) / len(both), w) for w in [x / 20 for x in range(21)])
    summary["best_history_share_touch"] = {"share": best[1], "brier": best[0]}
    print(f"share of the history that scored best beside the volatility method (touch): {best[1]:.2f} (Brier {best[0]:.4f})")
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(f"\n{len(rows)} cases, {len(used)} stocks, {summary['first']} to {summary['last']}; written to {out}")
    print(f"{'method':30s} {'n':>7s} {'said':>7s} {'happened':>9s} {'Brier':>7s} {'ref':>7s} {'skill':>7s}")
    for name, m in summary["methods"].items():
        print(f"{name:30s} {m['n']:7d} {m['said']:7.3f} {m['happened']:9.3f} {m['brier']:7.4f} {m['reference_brier']:7.4f} {m['skill']:7.3f}")
        print("    bins said -> happened: " + "  ".join(f"{b['said']:.2f}->{b['happened']:.2f}({b['n']})" for b in m["bins"]))
        for label, key in (("after tripling", "after_tripling"), ("others", "others")):
            part = m.get(key)
            if part:
                print(f"    {label:15s} n={part['n']:6d} said {part['said']:.3f} happened {part['happened']:.3f}")
        print("    by years: " + "  ".join(f"{h}y said {v['said']:.2f} happened {v['happened']:.2f}" for h, v in m["by_years"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
