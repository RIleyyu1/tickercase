"""SEC XBRL company facts: annual revenue, annual net income and shares outstanding.

Endpoint (https://www.sec.gov/search-filings/edgar-application-programming-interfaces):
- https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

Selection rules
- Annual values: facts filed on 10-K or 10-K/A whose period spans 330-400 days.
  When several filings report the same period, the latest filing wins
  (restatements replace earlier values).
- Revenue concepts are tried in a fixed order and merged per period end, so a
  company that changed tags over the years keeps one continuous series. The
  concept used for each value is recorded.
- Shares outstanding: dei:EntityCommonStockSharesOutstanding from the cover
  page of 10-K and 10-Q filings (a point-in-time count).

Values are reported numbers as filed. This adapter does not adjust for splits,
segments, currency or non-GAAP definitions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from ..http_client import FetchedJson, JsonFetcher
from ..models import MetricPoint, ReportedFacts
from .base import Provider, ProviderError, ProviderParseError
from .sec import SecFilingProvider

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
NET_INCOME_CONCEPTS = ("NetIncomeLoss", "ProfitLoss")
ANNUAL_FORMS = ("10-K", "10-K/A")
SHARE_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A")
ANNUAL_MIN_DAYS = 330
ANNUAL_MAX_DAYS = 400


def _parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_value(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _facts_for(taxonomy: Mapping[str, Any], concept: str, unit: str) -> list[Mapping[str, Any]]:
    node = taxonomy.get(concept)
    if not isinstance(node, Mapping):
        return []
    units = node.get("units")
    if not isinstance(units, Mapping):
        return []
    rows = units.get(unit)
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else []


def annual_series(taxonomy: Mapping[str, Any], concepts: Sequence[str], unit: str = "USD") -> list[MetricPoint]:
    """Merge annual 10-K values across concepts, one value per period end, ascending."""
    by_end: dict[date, MetricPoint] = {}
    for rank, concept in enumerate(concepts):
        candidates: dict[date, MetricPoint] = {}
        for row in _facts_for(taxonomy, concept, unit):
            form = row.get("form")
            start, end, filed = _parse_date(row.get("start")), _parse_date(row.get("end")), _parse_date(row.get("filed"))
            value = _parse_value(row.get("val"))
            accession = row.get("accn")
            if form not in ANNUAL_FORMS or None in (start, end, filed, value) or not isinstance(accession, str):
                continue
            if not ANNUAL_MIN_DAYS <= (end - start).days <= ANNUAL_MAX_DAYS:
                continue
            point = MetricPoint(
                period_start=start, period_end=end, value=value, unit=unit, concept=concept, form=form,
                accession=accession, filed=filed,
                fiscal_year=row.get("fy") if isinstance(row.get("fy"), int) else None,
                fiscal_period=row.get("fp") if isinstance(row.get("fp"), str) else None,
            )
            current = candidates.get(end)
            if current is None or (point.filed, point.accession) > (current.filed, current.accession):
                candidates[end] = point
        for end, point in candidates.items():
            # an earlier concept in the list wins for the same period end
            if end not in by_end:
                by_end[end] = point
    return [by_end[k] for k in sorted(by_end)]


def share_series(dei: Mapping[str, Any]) -> list[MetricPoint]:
    by_end: dict[date, MetricPoint] = {}
    for row in _facts_for(dei, "EntityCommonStockSharesOutstanding", "shares"):
        form = row.get("form")
        end, filed = _parse_date(row.get("end")), _parse_date(row.get("filed"))
        value = _parse_value(row.get("val"))
        accession = row.get("accn")
        if form not in SHARE_FORMS or None in (end, filed, value) or not isinstance(accession, str) or value <= 0:
            continue
        point = MetricPoint(
            period_end=end, value=value, unit="shares", concept="dei:EntityCommonStockSharesOutstanding", form=form,
            accession=accession, filed=filed,
            fiscal_year=row.get("fy") if isinstance(row.get("fy"), int) else None,
            fiscal_period=row.get("fp") if isinstance(row.get("fp"), str) else None,
        )
        current = by_end.get(end)
        if current is None or (point.filed, point.accession) > (current.filed, current.accession):
            by_end[end] = point
    return [by_end[k] for k in sorted(by_end)]


class SecCompanyFactsProvider(Provider):
    provider_id = "sec_xbrl_companyfacts"
    display_name = "SEC XBRL company facts"
    capabilities = ("annual_revenue", "annual_net_income", "shares_outstanding")
    limitations = (
        "reported values as tagged by the filer; no split, segment or currency adjustment",
        "annual values from 10-K filings only; quarterly values are not used",
        "multi-class share counts are summed only when the filer reports a single total",
    )

    def __init__(self, client: JsonFetcher, resolver: Optional[SecFilingProvider] = None):
        self.client = client
        self.resolver = resolver or SecFilingProvider(client)

    def fetch_facts(self, ticker: str) -> ReportedFacts:
        cik, title = self.resolver.resolve_cik(ticker)
        fetched = self.client.get_json(COMPANYFACTS_URL.format(cik=cik))
        return self.parse(ticker, cik, title, fetched)

    def parse(self, ticker: str, cik: str, title: Optional[str], fetched: FetchedJson) -> ReportedFacts:
        url = fetched.url
        payload = fetched.payload
        if not isinstance(payload, Mapping):
            raise ProviderParseError("parse_error", "companyfacts: expected a JSON object", url=url)
        facts = payload.get("facts")
        if not isinstance(facts, Mapping):
            raise ProviderParseError("parse_error", "companyfacts.facts: expected an object", url=url)
        gaap = facts.get("us-gaap") if isinstance(facts.get("us-gaap"), Mapping) else {}
        dei = facts.get("dei") if isinstance(facts.get("dei"), Mapping) else {}
        if not gaap and not dei:
            raise ProviderError("no_xbrl_facts", f"SEC company facts for CIK {cik} contain no us-gaap or dei facts", url=url)

        revenue = annual_series(gaap, REVENUE_CONCEPTS)
        net_income = annual_series(gaap, NET_INCOME_CONCEPTS)
        shares = share_series(dei)
        notes: list[str] = []
        if not revenue:
            notes.append("no annual revenue found under " + ", ".join(REVENUE_CONCEPTS))
        if not net_income:
            notes.append("no annual net income found under " + ", ".join(NET_INCOME_CONCEPTS))
        if not shares:
            notes.append("no cover-page shares outstanding (dei:EntityCommonStockSharesOutstanding) found")
        concepts_used = sorted({p.concept for p in revenue})
        if len(concepts_used) > 1:
            notes.append("revenue series combines concepts: " + ", ".join(concepts_used))
        name = payload.get("entityName") if isinstance(payload.get("entityName"), str) else title
        return ReportedFacts(
            provider_id=self.provider_id, ticker=ticker.strip().upper(), cik=cik, company_name=name,
            revenue=revenue, net_income=net_income, shares_outstanding=shares,
            source_url=url, retrieved_at=fetched.retrieved_at, source_captured_at=fetched.source_captured_at,
            data_mode=fetched.data_mode, notes=notes,
        )
