"""Similar stocks from Yahoo Finance, offered as the default peer basket (v0.11).

Endpoint: https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/<SYMBOL>
No API key; unofficial, like the chart endpoint. Yahoo builds the list from what its users look at
together, so it mixes industry peers with stocks that trade on the same theme (for RKLB in October 2026:
ASTS, NBIS, IONQ, IREN, CRWV; for INTC: AMD, CSCO, IBM, ORCL, PFE). The page shows it as a default the
user confirms or edits, and the market-structure step keeps only peers that actually moved with the stock.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..http_client import JsonFetcher
from .base import Provider, ProviderError, ProviderParseError
from .market import yahoo_symbol

RECOMMEND_URL = "https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{symbol}"


def parse_similar(payload: Any, url: str) -> list[str]:
    fin = payload.get("finance") if isinstance(payload, Mapping) else None
    results = fin.get("result") if isinstance(fin, Mapping) else None
    if not isinstance(results, list):
        raise ProviderParseError("parse_error", "finance.result: expected an array", url=url)
    out: list[str] = []
    for row in results[:1]:
        for rec in (row.get("recommendedSymbols") or []) if isinstance(row, Mapping) else []:
            sym = rec.get("symbol") if isinstance(rec, Mapping) else None
            if isinstance(sym, str) and sym.strip() and sym.strip().upper() not in out:
                out.append(sym.strip().upper())
    if not out:
        raise ProviderError("no_peers", "the quote source lists no similar stocks for this ticker", url=url)
    return out


class YahooPeersProvider(Provider):
    provider_id = "yahoo_finance_similar"
    display_name = "Yahoo Finance similar stocks (unofficial)"
    capabilities = ("similar_stocks",)
    limitations = ("built from what users view together, not from an industry classification",)

    def __init__(self, client: JsonFetcher):
        self.client = client

    def similar(self, ticker: str) -> tuple[list[str], str]:
        url = RECOMMEND_URL.format(symbol=yahoo_symbol(ticker))
        fetched = self.client.get_json(url)
        return parse_similar(fetched.payload, url), url
