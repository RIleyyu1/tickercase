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
from tickercase.providers.insiders import xml_url  # noqa: E402
from tickercase.providers.market import CHART_URL, MONTHLY_URL  # noqa: E402
from tickercase.providers.options import OPTIONS_URL  # noqa: E402
from tickercase.providers.sec_frames import FRAMES_URL  # noqa: E402
from tickercase.providers.sentiment import FEAR_GREED_URL, POLYMARKET_URL  # noqa: E402
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


def _daily_path(seed: int, end_value: float, vol: float, drift: float = 0.0004) -> tuple[list[date], list[float]]:
    rng = random.Random(seed)
    days, d = [], date(2021, 10, 1)
    while d <= date(2026, 9, 30):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    sigma = vol / math.sqrt(252)
    logs, x = [], 0.0
    for _ in days:
        x += rng.gauss(drift, sigma)
        logs.append(x)
    shift = math.log(end_value) - logs[-1]
    closes = [round(math.exp(v + shift), 4) for v in logs]
    closes[-1] = end_value
    return days, closes


def _chart(symbol: str, days: list[date], closes: list[float]) -> dict:
    stamps = [int(datetime(dd.year, dd.month, dd.day, 13, 30, tzinfo=timezone.utc).timestamp()) for dd in days]
    return {"chart": {"result": [{"meta": {"currency": "USD", "symbol": symbol, "gmtoffset": -14400}, "timestamp": stamps,
                                  "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": closes}]}}], "error": None}}


def _monthly(days: list[date], closes: list[float]) -> tuple[list[date], list[float]]:
    last: dict[tuple[int, int], tuple[date, float]] = {}
    for d, c in zip(days, closes):
        last[(d.year, d.month)] = (d, c)
    keys = sorted(last)
    return [last[k][0] for k in keys], [last[k][1] for k in keys]


EXPIRIES = (date(2026, 11, 20), date(2026, 12, 18), date(2027, 1, 15), date(2027, 3, 19), date(2027, 6, 17), date(2028, 1, 21), date(2028, 12, 15))
# at-the-money IV by expiry: flat 45% except a fictional event in February 2027, priced into the March and later expiries
ATM_IV = {date(2027, 3, 19): 0.53, date(2027, 6, 17): 0.50, date(2028, 1, 21): 0.47}


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def option_chain(expiry: date) -> dict:
    """Fictional calls: strikes 20-120, IV smile around 45%, open interest thinning at high strikes."""
    calls = []
    for k in range(20, 125, 5):
        iv = ATM_IV.get(expiry, 0.45) + 0.0015 * abs(k - 50)
        calls.append({"contractSymbol": f"SYNT{expiry:%y%m%d}C{k:05d}000", "strike": float(k), "impliedVolatility": round(iv, 4),
                      "openInterest": max(0, 900 - 9 * k), "bid": round(max(0.05, 50 - k + 8), 2), "ask": round(max(0.1, 50 - k + 9), 2),
                      "lastPrice": round(max(0.08, 50 - k + 8.5), 2)})
    return {"optionChain": {"result": [{"underlyingSymbol": "SYNT", "expirationDates": [_ts(e) for e in EXPIRIES],
                                        "quote": {"regularMarketPrice": 50.0}, "options": [{"expirationDate": _ts(expiry), "calls": calls, "puts": []}]}],
                            "error": None}}


def frames(concept: str, year: int, seed: int) -> dict:
    """About 300 fictional filers with revenue 50M-600M in 2020 growing at a random steady rate, for any year 2015-2025."""
    rng = random.Random(seed)
    rows = []
    for i in range(300):
        base = rng.uniform(50e6, 600e6)
        growth = rng.gauss(0.07, 0.12)
        value = base * (1 + growth) ** (year - 2020)
        if concept == "NetIncomeLoss":
            value = value * rng.uniform(-0.05, 0.2)
        rows.append({"accn": f"0009990{i:03d}-{year % 100 + 1:02d}-000001", "cik": 9990000 + i, "entityName": f"Synthetic Filer {i:03d} (fictional)",
                     "loc": "US-DE", "end": f"{year}-12-31", "val": int(value)})
    if concept == "Revenues":
        rows = rows[:40]  # the second concept only fills a few gaps
    return {"taxonomy": "us-gaap", "tag": concept, "ccp": f"CY{year}", "uom": "USD", "label": concept, "pts": len(rows), "data": rows}


FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2026-05-29</periodOfReport>
  <aff10b5One>1</aff10b5One>
  <issuer><issuerCik>0009999901</issuerCik><issuerName>Synthetic Example Corp (fictional)</issuerName><issuerTradingSymbol>SYNT</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0009999990</rptOwnerCik><rptOwnerName>Example Officer (fictional)</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-05-29</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>20000</value></transactionShares><transactionPricePerShare><value>48.50</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-05-29</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>15000</value></transactionShares><transactionPricePerShare><value>0</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def main() -> None:
    for path in OUT.glob("*.json"):
        path.unlink()
    write_snapshot(OUT, TICKERS_URL, TICKERS, captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, SUBMISSIONS_URL.format(cik=CIK), submissions(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, SUBMISSIONS_URL.format(cik="0009999902"), empty_submissions(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, COMPANYFACTS_URL.format(cik=CIK), companyfacts(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    write_snapshot(OUT, CHART_URL.format(symbol="SYNT"), price_chart(), captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)
    w = lambda url, payload: write_snapshot(OUT, url, payload, captured_at=AUTHORED_AT, origin=ORIGIN_SYNTHETIC, note=NOTE)  # noqa: E731
    # option chains
    w(OPTIONS_URL.format(symbol="SYNT"), option_chain(EXPIRIES[0]))
    for e in EXPIRIES:
        w(f"{OPTIONS_URL.format(symbol='SYNT')}?date={_ts(e)}", option_chain(e))
    # 10-year yield (^TNX is quoted in percent, 4.2 = 4.2%): flat
    days, _ = _daily_path(1, 4.2, 0.05)
    w(CHART_URL.format(symbol="^TNX"), _chart("^TNX", days, [4.2] * len(days)))
    # monthly histories: SYNT from its daily synthetic path, SPY and QQQ as fictional index paths
    chart = price_chart()["chart"]["result"][0]
    synt_days = [datetime.fromtimestamp(t, tz=timezone.utc).date() for t in chart["timestamp"]]
    md, mc = _monthly(synt_days, chart["indicators"]["adjclose"][0]["adjclose"])
    w(MONTHLY_URL.format(symbol="SYNT"), _chart("SYNT", md, mc))
    for sym, seed, end in (("SPY", 11, 600.0), ("QQQ", 12, 520.0)):
        dd, cc = _daily_path(seed, end, 0.18, 0.0004)
        md, mc = _monthly(dd, cc)
        w(MONTHLY_URL.format(symbol=sym), _chart(sym, md, mc))
    # SEC frames for the base rate: every year 2015-2025, so horizons of 1 to 10 years have both ends
    for concept, seed in (("RevenueFromContractWithCustomerExcludingAssessedTax", 21), ("Revenues", 22), ("NetIncomeLoss", 23)):
        for year in range(2015, 2026):
            w(FRAMES_URL.format(concept=concept, year=year), frames(concept, year, seed))
    # Form 4: the submissions block lists one Form 4 on 2026-06-02
    form4_index = next(i for i, row in enumerate(ROWS) if row[0] == "4")
    accession = f"{CIK}-{ROWS[form4_index][1][2:4]}-{len(ROWS) - form4_index:06d}"
    w(xml_url(CIK, accession, ROWS[form4_index][3]), FORM4_XML)
    # no prediction market for a fictional company; a neutral market mood
    w(POLYMARKET_URL.format(q="SYNT"), {"events": [], "tags": [], "profiles": []})
    w(FEAR_GREED_URL, {"fear_and_greed": {"score": 50.0, "rating": "neutral", "timestamp": "2026-10-01T00:00:00+00:00",
                                          "previous_close": 49.0, "previous_1_week": 48.0, "previous_1_month": 45.0}})
    print(f"wrote {len(list(OUT.glob('*.json')))} synthetic snapshots to {OUT}")


if __name__ == "__main__":
    main()
