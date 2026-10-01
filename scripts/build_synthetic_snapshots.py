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

from tickercase.http_client import ORIGIN_SYNTHETIC, write_snapshot  # noqa: E402
from tickercase.providers.sec import SUBMISSIONS_URL, TICKERS_URL  # noqa: E402

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


def main() -> None:
    for path in OUT.glob("*.json"):
        path.unlink()
    write_snapshot(OUT, TICKERS_URL, TICKERS, captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, SUBMISSIONS_URL.format(cik=CIK), submissions(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, SUBMISSIONS_URL.format(cik="0009999902"), empty_submissions(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    print(f"wrote {len(list(OUT.glob('*.json')))} synthetic snapshots to {OUT}")


if __name__ == "__main__":
    main()
