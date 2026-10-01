"""Insider transactions from SEC Form 4 ownership documents.

Filings are listed from the company's submissions JSON (form "4"); each XML
document is read for its non-derivative transactions. Transaction codes
(https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html):
  P open-market or private purchase    S open-market or private sale
  A grant or award                     M option exercise / conversion
  F tax withholding on vesting         G gift; others are counted as "other"
Only P and S are trades an insider chose to make; counting Form 4 filings
alone mixes them with grants and tax withholding.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from ..http_client import FetchError, JsonFetcher
from ..models import DataMode, InsiderSummary, InsiderTransaction
from .base import Provider, ProviderError
from .sec import ARCHIVE_BASE, SUBMISSIONS_URL, SecFilingProvider

MAX_FILINGS = 40


def _text(node: Optional[ET.Element], path: str) -> Optional[str]:
    if node is None:
        return None
    found = node.find(path)
    if found is None:
        return None
    value = found.find("value")
    text = (value.text if value is not None else found.text) or ""
    return text.strip() or None


def _dec(text: Optional[str]) -> Optional[Decimal]:
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def xml_url(cik: str, accession: str, primary_document: str) -> str:
    """The primary document is often an XSL-rendered path (xslF345X05/form4.xml); the raw XML has the same name without that folder."""
    name = primary_document.split("/")[-1]
    return f"{ARCHIVE_BASE}/{int(cik)}/{accession.replace('-', '')}/{name}"


def parse_form4(xml_text: str, filed: date, url: str) -> list[InsiderTransaction]:
    root = ET.fromstring(xml_text)
    owner = root.find("reportingOwner")
    name = _text(owner, "reportingOwnerId/rptOwnerName") or "unknown"
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = []
    if rel is not None:
        if (_text(rel, "isDirector") or "").lower() in ("1", "true"):
            roles.append("director")
        if (_text(rel, "isOfficer") or "").lower() in ("1", "true"):
            roles.append(_text(rel, "officerTitle") or "officer")
        if (_text(rel, "isTenPercentOwner") or "").lower() in ("1", "true"):
            roles.append("10% owner")
    plan = (_text(root, "aff10b5One") or "").lower() in ("1", "true")
    out = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(txn, "transactionCoding/transactionCode") or "?"
        shares = _dec(_text(txn, "transactionAmounts/transactionShares")) or Decimal(0)
        price = _dec(_text(txn, "transactionAmounts/transactionPricePerShare"))
        acquired = (_text(txn, "transactionAmounts/transactionAcquiredDisposedCode") or "") == "A"
        footnote_plan = "10b5-1" in ET.tostring(txn, encoding="unicode")
        out.append(InsiderTransaction(
            filed=filed, owner=name, role=", ".join(roles) or "insider", code=code, shares=shares, price=price,
            value=(shares * price).quantize(Decimal("0.01")) if price is not None else None, acquired=acquired,
            plan_10b5_1=plan or footnote_plan, url=url,
        ))
    return out


class InsiderProvider(Provider):
    provider_id = "sec_form4"
    display_name = "SEC Form 4 insider transactions"
    capabilities = ("insider_transactions",)
    limitations = (f"at most {MAX_FILINGS} most recent Form 4 filings in the window", "derivative transactions (options) are not summed")

    def __init__(self, client: JsonFetcher, resolver: Optional[SecFilingProvider] = None):
        self.client = client
        self.resolver = resolver or SecFilingProvider(client)

    def summary(self, ticker: str, *, today: date, months: int = 12) -> InsiderSummary:
        cik, _ = self.resolver.resolve_cik(ticker)
        fetched = self.client.get_json(SUBMISSIONS_URL.format(cik=cik))
        recent: Mapping[str, Any] = (fetched.payload.get("filings") or {}).get("recent") or {}
        forms, dates, accs, docs = (recent.get(k) or [] for k in ("form", "filingDate", "accessionNumber", "primaryDocument"))
        start = today - timedelta(days=int(months * 30.44))
        listed = []
        for i, form in enumerate(forms):
            if form != "4" or i >= min(len(dates), len(accs), len(docs)):
                continue
            try:
                filed = date.fromisoformat(dates[i])
            except (TypeError, ValueError):
                continue
            if filed >= start and isinstance(docs[i], str) and docs[i]:
                listed.append((filed, accs[i], docs[i]))
        listed.sort(reverse=True)
        summary = InsiderSummary(window_start=start, filings_listed=len(listed), filings_read=0, data_mode=fetched.data_mode)
        buyers, sellers = set(), set()
        for filed, acc, doc in listed[:MAX_FILINGS]:
            url = xml_url(cik, acc, doc)
            try:
                doc_fetched = self.client.get_json(url)
                if not isinstance(doc_fetched.payload, str):
                    raise ProviderError("parse_error", "Form 4 document is not XML text", url=url)
                txns = parse_form4(doc_fetched.payload, filed, url)
            except (FetchError, ProviderError, ET.ParseError):
                summary.filings_failed += 1
                continue
            summary.filings_read += 1
            for t in txns:
                summary.transactions.append(t)
                value = t.value or Decimal(0)
                if t.code == "P":
                    summary.purchases += 1
                    summary.purchase_value += value
                    buyers.add(t.owner)
                elif t.code == "S":
                    summary.sales += 1
                    summary.sale_value += value
                    sellers.add(t.owner)
                    if t.plan_10b5_1:
                        summary.plan_sale_value += value
                elif t.code == "A":
                    summary.grants += 1
                elif t.code == "M":
                    summary.exercises += 1
                elif t.code == "F":
                    summary.tax_withholding += 1
                else:
                    summary.other += 1
        summary.buyers, summary.sellers = len(buyers), len(sellers)
        summary.net_open_market_value = summary.purchase_value - summary.sale_value
        summary.transactions = summary.transactions[:80]
        if fetched.data_mode is DataMode.SYNTHETIC:
            summary.data_mode = DataMode.SYNTHETIC
        return summary
