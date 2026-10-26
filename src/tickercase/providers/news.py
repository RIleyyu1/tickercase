"""Headlines about a company (v0.12): Yahoo Finance's latest and Google News searches.

Endpoints (no key, unofficial, like the other Yahoo endpoints):
- https://query1.finance.yahoo.com/v1/finance/search?q=<SYMBOL>&newsCount=20 : the 20 latest headlines on the ticker.
- https://news.google.com/rss/search?q=<QUERY> : an RSS search; the query takes "when:730d" (relative, so the URL and a
  recorded snapshot stay the same from day to day), and a search such as '"Rocket Lab" Neutron when:730d' returns the
  dated history of a theme (up to 100 items per search).

Only the headline, outlet, time and link are kept: the articles themselves are not fetched. GDELT was tested and
rate-limits anonymous requests (HTTP 429), so it is not used.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Optional
from urllib.parse import quote_plus

from ..http_client import FetchedJson, JsonFetcher
from ..models import NewsFeed, NewsItem
from .base import Provider, ProviderParseError
from .market import yahoo_symbol

YAHOO_NEWS_URL = "https://query1.finance.yahoo.com/v1/finance/search?q={symbol}&newsCount=20&quotesCount=0"
GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
SUFFIXES = re.compile(r"[\s,]+(?:inc|incorporated|corp|corporation|co|company|holdings?|group|ltd|limited|plc|s\.?a|n\.?v|usa|the)\.?$", re.IGNORECASE)


def company_query_name(name: Optional[str], ticker: str) -> str:
    """'Rocket Lab USA, Inc.' -> 'Rocket Lab'; 'Intel Corp' -> 'Intel'. Falls back to the ticker."""
    text = re.sub(r"\s*\([^)]*\)\s*$", "", (name or "").strip())  # "(fictional)", "(The)"
    while True:
        shorter = SUFFIXES.sub("", text).strip(" ,.")
        if shorter == text:
            break
        text = shorter
    return text or ticker


def google_query(company: str, term: Optional[str] = None, *, after: Optional[date] = None, before: Optional[date] = None,
                 days: Optional[int] = None) -> str:
    parts = [f'"{company}"'] + ([term] if term else [])
    if days:
        parts.append(f"when:{days}d")
    if after:
        parts.append(f"after:{after.isoformat()}")
    if before:
        parts.append(f"before:{before.isoformat()}")
    return " ".join(parts)


def parse_yahoo_news(payload: Any, url: str) -> list[NewsItem]:
    rows = payload.get("news") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ProviderParseError("parse_error", "search.news: expected an array", url=url)
    out = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("title"), str) or not isinstance(row.get("providerPublishTime"), int):
            continue
        out.append(NewsItem(published=datetime.fromtimestamp(row["providerPublishTime"], tz=timezone.utc), publisher=str(row.get("publisher") or ""),
                            title=row["title"].strip(), url=str(row.get("link") or ""), origin="yahoo"))
    return out


def parse_google_rss(text: Any, url: str, query: str) -> list[NewsItem]:
    if not isinstance(text, str):
        raise ProviderParseError("parse_error", "Google News: expected an RSS document", url=url)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ProviderParseError("parse_error", f"Google News: RSS is not well-formed ({exc})", url=url) from exc
    out = []
    for item in root.iter("item"):
        title, link, pub = (item.findtext("title") or "").strip(), (item.findtext("link") or "").strip(), item.findtext("pubDate")
        source = (item.findtext("source") or "").strip()
        if not title or not pub:
            continue
        try:
            published = parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            continue
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].strip()
        out.append(NewsItem(published=published.astimezone(timezone.utc), publisher=source, title=title, url=link, origin="google_news", query=query))
    return out


def merge(items: list[NewsItem]) -> list[NewsItem]:
    """Newest first, one item per headline (the same story from two searches counts once)."""
    seen: set[str] = set()
    out = []
    for it in sorted(items, key=lambda x: x.published, reverse=True):
        key = re.sub(r"\W+", " ", it.title.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


class NewsProvider(Provider):
    provider_id = "news_headlines"
    display_name = "Yahoo Finance news and Google News search (unofficial)"
    capabilities = ("headlines",)
    limitations = ("headlines only; articles are not read", "Google News returns at most 100 items per search")

    def __init__(self, client: JsonFetcher):
        self.client = client

    def yahoo(self, ticker: str) -> tuple[list[NewsItem], FetchedJson]:
        url = YAHOO_NEWS_URL.format(symbol=yahoo_symbol(ticker))
        fetched = self.client.get_json(url)
        return parse_yahoo_news(fetched.payload, url), fetched

    def google(self, query: str) -> tuple[list[NewsItem], FetchedJson]:
        url = GOOGLE_NEWS_URL.format(query=quote_plus(query))
        fetched = self.client.get_json(url)
        return parse_google_rss(fetched.payload, url, query), fetched

    def recent(self, ticker: str, company: str, *, days: int = 30) -> NewsFeed:
        """The case's headline feed: Yahoo's latest on the ticker plus a Google News search on the company name."""
        y, fy = self.yahoo(ticker)
        query = google_query(company, days=days)
        g, fg = self.google(query)
        return NewsFeed(items=merge(y + g), queries=[query], source_urls=[fy.url, fg.url], retrieved_at=fy.retrieved_at, data_mode=fy.data_mode)
