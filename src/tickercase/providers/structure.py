"""Daily closes for the market-structure split (v0.11): the stock, SPY, the peer basket, QQQ, IWM and the VIX.

All from the Yahoo chart endpoint used for prices (see ``market.py``). The stock's five-year history is the
same URL the price step already fetched, so a live run reads it from the client's cache and a replay run
from the same snapshot. The split fails when the stock or SPY is missing; a peer or a reading that cannot
be fetched is listed with the reason and left out.
"""

from __future__ import annotations

from typing import Sequence

from ..http_client import FetchError, JsonFetcher
from ..models import DataMode, Text
from ..structure import MARKET, REGIME, StructureInputs
from .base import Provider, ProviderError
from .market import CHART_URL, DAILY_URL, daily_closes, yahoo_symbol


class MarketStructureProvider(Provider):
    provider_id = "yahoo_finance_structure"
    display_name = "Yahoo Finance daily closes for the market-structure split (unofficial)"
    capabilities = ("daily_close",)
    limitations = ("unofficial endpoint without published terms or service guarantee",)

    def __init__(self, client: JsonFetcher):
        self.client = client

    def inputs(self, ticker: str, peers: Sequence[str], *, peer_source: str | None = None) -> StructureInputs:
        symbol = yahoo_symbol(ticker)
        urls: list[str] = []
        modes: set[DataMode] = set()
        first = None

        def fetch(url: str):
            nonlocal first
            fetched = self.client.get_json(url)
            urls.append(url)
            modes.add(fetched.data_mode)
            first = first or fetched.retrieved_at
            return daily_closes(fetched.payload, url)

        inp = StructureInputs(symbol=symbol, stock=fetch(CHART_URL.format(symbol=symbol)), market=fetch(DAILY_URL.format(symbol=MARKET)),
                              peer_source=peer_source)
        for peer in peers:
            try:
                inp.peers[yahoo_symbol(peer)] = fetch(DAILY_URL.format(symbol=yahoo_symbol(peer)))
            except (FetchError, ProviderError) as exc:
                inp.peer_notes[yahoo_symbol(peer)] = Text(en=f"not fetched ({exc.code})", zh=f"未取到（{exc.code}）")
        for sym, _ in REGIME:
            try:
                inp.regime[sym] = fetch(DAILY_URL.format(symbol=sym))
            except (FetchError, ProviderError):
                pass  # a missing reading only leaves its line out
        inp.source_urls = urls
        inp.retrieved_at = first
        inp.data_mode = DataMode.SYNTHETIC if DataMode.SYNTHETIC in modes else DataMode.REPLAY if DataMode.REPLAY in modes else DataMode.LIVE
        return inp
