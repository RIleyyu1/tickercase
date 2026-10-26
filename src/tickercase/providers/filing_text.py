"""Sentences from 8-K press releases (v0.12), the company's own statements about its plans and dates.

For each 8-K the filing index (https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/index.json) lists the
files; the press release is the exhibit whose file name contains "ex99" (Exhibit 99.1), otherwise the primary
document is read. The HTML is reduced to text, the boilerplate after "Forward-Looking Statements" or "About
<company>" is cut, and the sentences that carry a date, a plan or a milestone are kept (at most a few per
filing), so a quarterly results release does not flood the source table.
"""

from __future__ import annotations

import html
import re
from typing import Iterable, Optional

from ..http_client import JsonFetcher
from ..models import FilingRecord
from .base import Provider, ProviderParseError

INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
EXHIBIT_RE = re.compile(r"ex-?99", re.IGNORECASE)
BOILERPLATE_RE = re.compile(r"\b(?:Forward[- ]Looking Statements|Cautionary (?:Note|Statement)|Safe Harbor|Investor Relations? Contacts?|Media Contacts?)",
                            re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")
SIGNAL_RE = re.compile(
    r"\b(?:20[2-4]\d|Q[1-4]|first|second|third|fourth|half|quarter|January|February|March|April|May|June|July|August|September|October|"
    r"November|December|expect\w*|anticipat\w*|plan\w*|target\w*|schedul\w*|delay\w*|launch\w*|milestone\w*|approv\w*|award\w*|contract\w*|"
    r"acqui\w*|agreement|backlog|guidance|outlook|inaugural|debut|complet\w*|first flight|certif\w*|clearance|trial)\b", re.IGNORECASE)
MAX_SENTENCES, MAX_CHARS = 6, 420


def html_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    text = re.sub(r"(?is)<br\s*/?>|</(p|div|tr|li|h\d)>", ". ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"(\.\s*){2,}", ". ", text).strip()


def key_sentences(text: str, company: str = "") -> list[str]:
    """The opening sentence and the dated or forward-looking ones, before the boilerplate starts ("About <company>", safe harbor)."""
    cuts = [m.start() for m in [BOILERPLATE_RE.search(text, 20)] if m]
    if company:
        about = re.search(rf"\bAbout {re.escape(company)}\b", text[20:])
        if about:
            cuts.append(about.start() + 20)
    body = text[: min(cuts)] if cuts else text
    body = re.sub(r"^.*?\bExhibit 99\.\d\b\s*", "", body, count=1, flags=re.IGNORECASE)
    sentences = [s.strip(" .") for s in SENTENCE_RE.split(body) if len(s.strip()) > 30]
    if not sentences:
        return []
    keep = [sentences[0]] + [s for s in sentences[1:] if SIGNAL_RE.search(s)]
    return [s if len(s) <= MAX_CHARS else s[: MAX_CHARS - 1].rsplit(" ", 1)[0] + "…" for s in keep[:MAX_SENTENCES]]


class FilingTextProvider(Provider):
    provider_id = "sec_filing_text"
    display_name = "SEC EDGAR 8-K press releases"
    capabilities = ("filing_text",)
    limitations = ("only sentences with dates or plans are kept", "exhibits are found by file name (ex99)")

    def __init__(self, client: JsonFetcher):
        self.client = client

    def document_url(self, record: FilingRecord) -> str:
        acc = record.accession_number.replace("-", "")
        cik = str(int(record.cik))
        index_url = INDEX_URL.format(cik=cik, acc=acc)
        index = self.client.get_json(index_url).payload
        items = index.get("directory", {}).get("item", []) if isinstance(index, dict) else []
        if not isinstance(items, list):
            raise ProviderParseError("parse_error", "filing index: expected directory.item", url=index_url)
        names = [i.get("name", "") for i in items if isinstance(i, dict)]
        exhibits = sorted(n for n in names if EXHIBIT_RE.search(n) and n.lower().endswith((".htm", ".html", ".txt")))
        name = exhibits[0] if exhibits else record.primary_document
        if not name:
            raise ProviderParseError("parse_error", "filing has no readable document", url=index_url)
        return DOC_URL.format(cik=cik, acc=acc, name=name)

    def sentences(self, record: FilingRecord, company: str = "") -> tuple[list[str], str]:
        url = self.document_url(record)
        payload = self.client.get_json(url).payload
        if not isinstance(payload, str):
            raise ProviderParseError("parse_error", "filing document: expected HTML text", url=url)
        return key_sentences(html_text(payload), company), url


def recent_8ks(records: Iterable[FilingRecord], *, since, limit: int = 10) -> list[FilingRecord]:
    """8-K filings on or after ``since``, newest first; only items that carry news (not votes or exhibits alone)."""
    quiet = {"5.07", "9.01", "5.03"}
    out = [r for r in records if r.form_type in ("8-K", "8-K/A") and r.filing_date >= since
           and (not r.items or any(x.strip() not in quiet for x in r.items.split(",")))]
    return sorted(out, key=lambda r: r.filing_date, reverse=True)[:limit]


def items_label(record: FilingRecord) -> Optional[str]:
    items = [x.strip() for x in (record.items or "").split(",") if x.strip() and x.strip() != "9.01"]
    return ", ".join(items) or None
