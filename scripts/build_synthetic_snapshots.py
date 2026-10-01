"""Regenerate the synthetic SEC snapshots in examples/sec_synthetic_snapshots/.

Everything written here is invented for demos and tests. The company, CIK,
accession numbers and documents do not exist. Each envelope carries
origin="synthetic_fixture", so replay reports data_mode="synthetic".
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import math  # noqa: E402
import random  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from tickercase.http_client import ORIGIN_SYNTHETIC, write_snapshot  # noqa: E402
from tickercase.providers.market import CHART_URL  # noqa: E402
from tickercase.providers.sec import SUBMISSIONS_URL, TICKERS_URL  # noqa: E402
from tickercase.providers.sec_facts import COMPANYFACTS_URL  # noqa: E402

OUT = ROOT / "examples" / "sec_synthetic_snapshots"
AUTHORED_AT = datetime(2026, 10, 1, tzinfo=timezone.utc)
NOTE = "SYNTHETIC: hand-written example data for TickerCase demos/tests. Not an SEC response."

CIK = "0009999901"

TICKERS = {
    "0": {"cik_str": int(CIK), "ticker": "SYNT", "title": "Synthetic Example Corp (fictional)"},
    "1": {"cik_str": 9999902, "ticker": "SYNX", "title": "Synthetic Empty Filer Inc (fictional)"},
}

# (form, filingDate, reportDate, primaryDocument, description)
ROWS = [
    ("8-K", "2026-08-05", "2026-08-04", "synt-20260804.htm", "8-K"),
    ("10-Q", "2026-08-01", "2026-06-30", "synt-20260630.htm", "10-Q"),
    ("4", "2026-06-02", "2026-05-29", "xslF345X05/form4.xml", "FORM 4"),
    ("10-Q", "2026-05-02", "2026-03-31", "synt-20260331.htm", "10-Q"),
    ("10-K/A", "2026-03-20", "2025-12-31", "synt-20251231a.htm", "10-K/A"),
    ("10-K", "2026-02-20", "2025-12-31", "synt-20251231.htm", "10-K"),
    ("8-K", "2026-01-15", "2026-01-14", "synt-20260114.htm", "8-K"),
    ("10-Q", "2025-11-01", "2025-09-30", "synt-20250930.htm", "10-Q"),
    ("10-Q", "2025-08-01", "2025-06-30", "synt-20250630.htm", "10-Q"),
    ("10-Q", "2025-05-02", "2025-03-31", "synt-20250331.htm", "10-Q"),
    ("10-K", "2025-02-21", "2024-12-31", "synt-20241231.htm", "10-K"),
    ("S-8", "2024-06-10", "", "synt-s8.htm", "S-8"),
    ("10-K", "2024-02-23", "2023-12-31", "synt-20231231.htm", "10-K"),
]


def submissions() -> dict:
    recent = {k: [] for k in ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "primaryDocDescription")}
    for i, (form, fdate, rdate, doc, desc) in enumerate(ROWS):
        recent["accessionNumber"].append(f"{CIK}-{fdate[2:4]}-{len(ROWS) - i:06d}")
        recent["filingDate"].append(fdate)
        recent["reportDate"].append(rdate)
        recent["form"].append(form)
        recent["primaryDocument"].append(doc)
        recent["primaryDocDescription"].append(desc)
    return {
        "cik": CIK.lstrip("0"),
        "name": "Synthetic Example Corp (fictional)",
        "tickers": ["SYNT"],
        "filings": {
            "recent": recent,
            "files": [
                {"name": f"CIK{CIK}-submissions-001.json", "filingCount": 120, "filingFrom": "2012-03-01", "filingTo": "2024-01-31"}
            ],
        },
    }


def empty_submissions() -> dict:
    cols = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "primaryDocDescription")
    return {"cik": "9999902", "name": "Synthetic Empty Filer Inc (fictional)", "filings": {"recent": {c: [] for c in cols}, "files": []}}


# fiscal year -> (revenue, net income, accession of the 10-K that first reported it, filed date)
ANNUAL = {
    2021: (120_000_000, 22_000_000, f"{CIK}-22-000090", "2022-02-25"),
    2022: (140_000_000, 28_000_000, f"{CIK}-23-000090", "2023-02-24"),
    2023: (160_000_000, 32_000_000, f"{CIK}-24-000001", "2024-02-23"),
    2024: (180_000_000, 36_000_000, f"{CIK}-25-000003", "2025-02-21"),
    2025: (200_000_000, 40_000_000, f"{CIK}-26-000008", "2026-02-20"),
}
# cover-page share counts: (as-of date, shares, form, accession, filed)
SHARES = [
    ("2023-07-28", 92_100_000, "10-Q", f"{CIK}-23-000150", "2023-08-01"),
    ("2024-07-26", 93_500_000, "10-Q", f"{CIK}-24-000150", "2024-08-01"),
    ("2025-07-25", 94_200_000, "10-Q", f"{CIK}-25-000006", "2025-08-01"),
    ("2026-01-30", 94_600_000, "10-K", f"{CIK}-26-000008", "2026-02-20"),
    ("2026-07-24", 95_000_000, "10-Q", f"{CIK}-26-000012", "2026-08-01"),
]


def companyfacts() -> dict:
    revenue, income = [], []
    for fy, (rev, ni, accn, filed) in ANNUAL.items():
        # each 10-K reports its own year and the prior year as comparative
        for year in (fy - 1, fy):
            if year not in ANNUAL:
                continue
            row = {"start": f"{year}-01-01", "end": f"{year}-12-31", "accn": accn, "fy": fy, "fp": "FY", "form": "10-K", "filed": filed}
            revenue.append({**row, "val": ANNUAL[year][0]})
            income.append({**row, "val": ANNUAL[year][1]})
    shares = [{"end": end, "val": val, "accn": accn, "fy": int(filed[:4]), "fp": "FY" if form == "10-K" else "Q2", "form": form, "filed": filed}
              for end, val, form, accn, filed in SHARES]
    return {
        "cik": int(CIK),
        "entityName": "Synthetic Example Corp (fictional)",
        "facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"label": "Entity Common Stock, Shares Outstanding", "units": {"shares": shares}}},
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {"label": "Revenue", "units": {"USD": revenue}},
                "NetIncomeLoss": {"label": "Net Income (Loss)", "units": {"USD": income}},
            },
        },
    }


def price_chart() -> dict:
    """Five years of fictional daily closes ending at exactly 50.00 on 2026-09-30."""
    rng = random.Random(5151)
    days, d = [], date(2021, 10, 1)
    while d <= date(2026, 9, 30):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    sigma = 0.35 / math.sqrt(252)
    logs, x = [], 0.0
    for _ in days:
        x += rng.gauss(0.0004, sigma)
        logs.append(x)
    shift = math.log(50.0) - logs[-1]
    closes = [round(math.exp(v + shift), 4) for v in logs]
    closes[-1] = 50.0
    stamps = [int((datetime(dd.year, dd.month, dd.day, 13, 30, tzinfo=timezone.utc)).timestamp()) for dd in days]
    return {
        "chart": {
            "result": [{
                "meta": {"currency": "USD", "symbol": "SYNT", "exchangeTimezoneName": "America/New_York", "gmtoffset": -14400,
                         "regularMarketPrice": 50.0, "longName": "Synthetic Example Corp (fictional)"},
                "timestamp": stamps,
                "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": closes}]},
            }],
            "error": None,
        }
    }


def main() -> None:
    for path in OUT.glob("*.json"):
        path.unlink()
    write_snapshot(OUT, TICKERS_URL, TICKERS, captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, SUBMISSIONS_URL.format(cik=CIK), submissions(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, SUBMISSIONS_URL.format(cik="0009999902"), empty_submissions(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, COMPANYFACTS_URL.format(cik=CIK), companyfacts(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, CHART_URL.format(symbol="SYNT"), price_chart(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    print(f"wrote {len(list(OUT.glob('*.json')))} synthetic snapshots to {OUT}")


if __name__ == "__main__":
    main()
