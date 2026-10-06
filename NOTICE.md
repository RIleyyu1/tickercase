# Design references

TickerCase is a new implementation. No source files were copied from other projects.

## komako-workshop/digital-oracle (MIT)

- Repository: https://github.com/komako-workshop/digital-oracle
- Commit read: `a63e4c19a2f3313d54914c44666febaf5ffb9d6f`
- License: MIT, Copyright (c) 2026 komako-workshop

Used as an engineering reference only (architecture and testing approach, no copied code):

| TickerCase file | Reference file | What was taken as an idea | What is different here |
| --- | --- | --- | --- |
| `src/tickercase/providers/base.py` | `digital_oracle/providers/base.py` | provider id, display name, capability tuple | adds declared limitations; error classes carry a code |
| `src/tickercase/providers/sec.py` | `digital_oracle/providers/edgar.py` | typed query/result, ticker -> CIK resolution, injectable client | reads 10-K/10-Q/8-K (+/A) metadata instead of Form 4; validates array types/lengths; builds Archive document and index URLs; reports coverage gaps; no default contact email |
| `src/tickercase/http_client.py` | `digital_oracle/http.py`, `digital_oracle/snapshots.py` | one network boundary; recording and replay clients | status-aware errors (403/404/429/5xx/timeout), Retry-After, throttling, TTL cache, required User-Agent, atomic UTF-8 snapshot writes, original capture time preserved on replay, synthetic vs recorded origin |
| `tests/test_snapshots.py` | `tests/test_snapshots.py` | record -> replay round trip, missing snapshot raises | adds concurrency, corruption, no-network and date-preservation tests |
| `src/tickercase/providers/market.py` | `digital_oracle/providers/yahoo.py` (price data source idea) | use of a public daily price source behind the same client boundary | reads only the chart endpoint for daily closes and historical volatility; no options chain; no fallback to other sources |
| `src/tickercase/oracle.py`, `providers/options.py`, `providers/insiders.py`, `providers/sentiment.py` | `digital_oracle/providers/yahoo.py`, `edgar.py`, `polymarket.py`, `fear_greed.py`; `SKILL.md` signal menu | option chains as a direct pricing signal, Form 4 insider activity, Polymarket contracts, Fear & Greed as environment | probabilities are computed by formulas and base rates instead of written by a model; Form 4 transactions are summed by code rather than counted; crumb handling and replay keys are new code |
| `src/tickercase/report.py` | `SKILL.md` Step 6 report template | layered "signal / data / what it's saying" tables, resonance and divergence analysis, time alignment, scenarios, upside/downside risks, signals to monitor | built only from TickerCase's deterministic results, in Chinese and English, for non-specialist readers; scenarios are formulas without probabilities and the verdict stays the evidence-as-of classification |
| `src/tickercase/probability.py` | the reference's probability-oriented reporting | showing a probability next to the analysis | a lognormal model output under user assumptions, kept outside the verdict; no market-implied probabilities |

Not adopted: price-signal-only methodology and probability as the main output (TickerCase keeps the evidence-as-of verdict of its operational concept), multi-provider fan-out, `gather` concurrency (its timeout does not stop running threads; this version has a single dependent call chain), proxy rotation, and the reference's SKILL.md installation steps.

## Fonts (SIL Open Font License 1.1)

Bundled in `static/fonts` with their licence files, unmodified:

- Fusion Pixel Font, 12px monospaced, Simplified Chinese build (`fusion-pixel-12px-monospaced-zh_hans.otf.woff2`): Copyright (c) 2022, TakWolf, https://github.com/TakWolf/fusion-pixel-font. Licence: `static/fonts/OFL-FusionPixel.txt`.
- VT323, Latin subset (`VT323-Regular.woff2`): Copyright 2011, The VT323 Project Authors. Licence: `static/fonts/OFL-VT323.txt`.

IBM Plex Mono and Noto Serif SC are loaded from Google Fonts at run time (both OFL) and are not bundled.
