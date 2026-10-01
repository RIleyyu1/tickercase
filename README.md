# TickerCase

**Since v0.5 TickerCase is a stock-only version of digital-oracle:** the headline answer to a claim such as "RKLB reaches $300 within 3 years" or "RKLB spikes to $200" is a probability range computed by independent methods and cross-checked, with every number traced to a source. The original evidence-as-of verdict (OA.13) is kept as a secondary view. This changes the product scope of the Team 12 course documents, which describe a verdict without price prediction; the documents need revising.

## Probability and cross-checks (v0.5)

| Method | What it measures | Source |
| --- | --- | --- |
| M1 option market | risk-neutral P(price at the target date ≥ target) from the implied volatility at the target strike of the expiry nearest the target date (Black-Scholes N(d2)); also the probability of touching the target before the date | Yahoo option chains (session crumb), 10-year Treasury yield (^TNX) |
| M2 statistical model | the same formula with the stock's historical volatility | daily prices |
| M3 base rate | share of US companies of similar size (0.5x–2x, widened to 0.25x–4x below 30 companies) whose revenue or net income grew at least as fast as the claim needs over the same number of years | SEC XBRL frames (all filers); survivors only, so optimistic |
| M4 own history | share of past windows of the same length in which this stock rose enough | monthly closes; used in the range only when the history holds ≥ 4 non-overlapping windows |

The range is [min, max] of the usable methods; the tier follows the middle estimate (<5% lottery, 5–20% unlikely, 20–50% possible, >50% likely). Where methods disagree, the page says which is higher and what each measures. Context layers: insider Form 4 transactions split by code (only P purchases and S sales are trades by choice; grants, exercises and tax withholding are not selling), Polymarket contracts on the ticker, CNN Fear & Greed, S&P 500 and Nasdaq-100 returns over the same length. The page shows each data step as it runs and which ones failed. A live RKLB run fetches all eleven steps in about 12 seconds.

## Touch claims, event pricing and scenarios (v0.7)

- **What counts as coming true.** A claim such as "冲到 200", "脉冲到 200", "hit $300" or "3年内达到 300" is about touching the price at any time before the deadline; "收在 500 以上", "closes above", "2027年底" is about the price on the date. The extractor reads this from the wording (`ClaimExtraction.condition`), the page shows it as a choice the user confirms (`price_condition`), and the probability range follows it: for touch claims M1 and M2 use the first-passage formula, M4 counts windows whose highest month-end close reached the target, and M3 (business base rate) is shown as context only, because a spike does not need the business to grow into the price.
- **What the option market prices for an event.** The options provider also reads the at-the-money implied volatility of up to 12 more expiries (about monthly for the first half year, then every 2–6 months to 24 months). `oracle.event_move` takes the extra implied variance between the expiries just before and after a date, minus what an ordinary stretch of that length costs, as the market's priced move for an event on that date (one standard deviation). Earnings in the same stretch are mixed in, and wide gaps between expiries are flagged.
- **Your scenario.** The "情景推演" tab takes an event date, the chance it succeeds and the price move on success and on failure, and gives the probability under that scenario (`oracle.scenario`: ordinary volatility plus one jump; exact for "on the date", approximate for "touch"), the success probability needed for 50%, and the success probability that today's option prices imply for those move sizes. It updates as the sliders move, without fetching again.
- **Page.** The result reads as a report: a header with the claim, the probability range on a square-root strip showing each method, then tabs for the layered report (with the AI narrative), the scenario, probability by price level and the four methods, the fundamentals check (OA.13/OA.14 verdict, evidence, calculations, sensitivity) and raw data. Muted colours with light and dark themes, short eased transitions (off under reduced-motion). The page is either Chinese or English, switched in the sidebar; no label shows both.

A live RKLB run ("RKLB 3年内脉冲到200") fetches all eleven steps plus the term structure in about 17 seconds: touch probability 26.6%–27.1% (end-above 9.1%), and the option market prices about ±11% of extra movement around 2027-03-01.

## AI narrative with number checking (v0.6)

On the result page, "生成 AI 叙述" (Write AI narrative) makes one call to Claude (`claude-opus-5-5`, effort `high`, structured JSON output, server-side refusal fallback `fallbacks: "default"`). Claude receives only a numbered fact table built from the case (every value fetched or computed, with its source) and returns six sections (core logic, agreeing signals, divergences, conclusion, upside, downside), each sentence in Chinese and English with the fact ids it uses. `narrative.verify` then checks every number in every sentence against the cited facts, allowing unit changes and rounding (%, 亿, 万亿, B, M): ✔ matches, ○ no numbers, ⚠ the number exists but under another fact, ✖ the number has no source. The counts and the token cost are shown with the narrative and stored in the case.

Setup: put `ANTHROPIC_API_KEY` in `.env` (the sidebar form does this), or log in with `ant auth login`. The call is billed per token (Claude Opus 5.5: $4 / $20 per million input / output tokens); one narrative typically uses a few thousand input and output tokens. The narrative is only written when the button is pressed. Without a key the page reports "not configured" and everything else works.

TickerCase turns a stock claim ("SYNT will be $100 in five years") into an inspectable investment case: explicit assumptions, reproducible numbers, dated public evidence, an evidence-as-of verdict and the conditions that should trigger a new review. It follows UC.1 *Evaluate a Stock Claim* from the Team 12 operational concept.

It does **not** trade, manage portfolios, predict prices or give investment advice. The verdict describes the state of the evidence on a date. An optional probability section shows a model output under your assumptions; it never feeds the verdict.

## Flow (UC.1)

1. Write the claim as one sentence. "识别并补全" (Read and fill in) reads the ticker, target price and time frame from it by fixed rules (no AI): tickers such as `TSLA` or `$TSLA` checked against SEC's ticker list, common Chinese and English company names, prices such as "1000一股", "$1.5k", "两百美元", relative targets such as "翻倍", "10x", "涨50%", and times such as "2030年", "五年后", "in 3 years", "明年", "18个月". It then adds reference values from public data (latest close, reported shares, latest annual revenue / net income) and default assumptions for empty fields (target shares = current shares, multiple = today's multiple). Every filled value is visible and labelled with its source. (OA.1, OA.4)
2. Confirm. The confirmation is bound to a SHA-256 fingerprint of the exact inputs; any edit makes it stale. (OA.5)
3. Deterministic calculation (`Decimal`, no network). (OA.8)
4. Public evidence: SEC filings, SEC XBRL financial facts, daily prices. (OA.6, OA.7, OA.9)
5. Deterministic evidence checks classified as supporting / contrary / missing / context, a verdict and recheck conditions. (OA.12–OA.14)
6. Result with calculations, evidence and sources, verdict, recheck conditions, gaps, provider errors and the data mode of every part. (OA.15)

Not implemented yet: AI extraction for sentences the rules cannot read (OA.2–OA.3 currently use rules) and AI-drafted analysis of filing text (OA.10–OA.11).

## Plain report

The first result tab is a layered report for readers without a finance background, adapted from the report template of komako-workshop/digital-oracle (layered signal tables, analysis, scenarios, conclusion, signals to monitor):

- a one-sentence headline: what the claim needs against what the company has done;
- four layers, each a table of signal | data | what it means: what the claim needs, what the company has done, how the market prices it today, how reliable the data is;
- analysis: checks that agree, key divergences (for example "valuation is not the problem, results are"), and a time view (next report, next fiscal year, target date);
- scenarios: the price at the target date if the business keeps its past pace at today's or the claimed valuation, and the claim's own path. These are formulas without probabilities;
- conclusion: what the verdict means, what would strengthen or weaken the case, and signals to watch with current values and triggers.

Every number in the report comes from the calculations, checks and data of the case; the report adds wording, not judgement. Amounts use 亿/万亿 in Chinese and B/T in English.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # set SEC_USER_AGENT for live SEC requests
pytest                  # offline test suite
```

Page:

```bash
streamlit run app.py
```

The page is in Chinese and English (switch in the sidebar). In the sidebar click "合成示例 · P/S" (or P/E), then "确认以上输入", then "运行评估". The examples use the fictional company `SYNT` and synthetic data.

For a real company choose `live`. If no SEC contact is configured, the sidebar asks for an email and saves `SEC_USER_AGENT` to the local `.env`. Fill in the claim box, click "补全其余项" (Fill in the rest), check the values and default assumptions, confirm and run. After a run the inputs collapse into a one-line summary above the case. Past cases are listed under "历史 · History", where two or more can be compared side by side.

API:

```bash
uvicorn --factory tickercase.api:app_factory --reload
# POST /claims/extract, POST /claims/validate, POST /claims/confirm, POST /cases, GET /cases/{id}, GET /reference/{ticker}, GET /health
```

Command line:

```bash
python -m tickercase.cli run examples/synthetic_claim_ps.json --sec-mode synthetic --confirm --out result.json
```

## Inputs

| Field | Kind | Notes |
| --- | --- | --- |
| `claim_text`, `ticker`, `currency` | required | currency is a 3-letter ISO code |
| `target_price` | required, > 0 | the claim |
| `reference_price`, `reference_price_date` | required, > 0 | reference value; typed or prefilled from the quote source |
| `horizon_years` | required, > 0 | assumption, fractional allowed |
| `target_assumed_shares` | required unless derived, > 0 | share count at the target date; give it directly, or give `current_shares` + `share_change_rate` |
| `share_change_rate` | optional assumption | yearly share change between -0.5 and 1 (0.01 = +1% a year); target shares = current x (1 + rate) ^ horizon |
| `share_change_mode` | optional | `trend`, `flat`, `rate` or `absolute`; records how the page chose the share count |
| `valuation_method` | required | `price_to_sales` or `price_to_earnings` |
| `valuation_multiple` | required, > 0 | assumption |
| `current_shares` | optional | reference value; adds `implied_share_count_change` |
| `base_annual_metric`, `base_metric_currency`, `base_metric_period` | optional | revenue for P/S, net income for P/E; only `required_metric_cagr` depends on them |
| `filings_since` | optional | start of the filing window; enables the coverage check |
| `probability_drift` | optional extra | yearly drift assumption, between -1 and 1; enables the probability section |
| `probability_volatility` | optional extra | yearly volatility, > 0 and <= 3; defaults to historical volatility |
| `field_sources` | set by prefill | field -> public source, or `default_assumption:...` for a default; recorded as provenance while the value is unchanged |

Which inputs matter most: the claim gives the ticker, target price and horizon. Reference price, current shares and base metric are facts the page fills from public data. The valuation multiple and the target share count are assumptions the claim usually leaves open, and they drive the result: the required revenue or net income scales with 1 / multiple and with the share count. The defaults are today's multiple ("no re-rating") and the reported share trend; the sensitivity table shows how the requirement moves when the multiple changes.

Target share count: few people can estimate it directly, so the page asks how the share count should change instead: recent trend (default), flat, a custom yearly %, or an absolute count. The trend is the yearly change of SEC cover-page share counts over up to 3 years, using only reports after the last jump of more than 40% between neighbouring reports (a stock split). E3 uses the same split rule. A yearly change outside -10%..+20% produces a warning before confirmation. Values loaded from an example are replaced when "Fill in the rest" runs, so they cannot carry over to another company.

A year in the claim text that is already past, or that disagrees with the horizon by more than a year, produces a warning.

Rejected: missing core fields, non-numbers, NaN, Infinity, non-positive prices/multiples/shares/horizon, future dates, and a base-metric currency different from the price currency (no FX conversion is applied). Nothing is filled in silently: prefilled values are visible, labelled with their source and confirmed like any other input.

## Formulas

```
required_return          = target_price / reference_price - 1
annualized_price_return  = (target_price / reference_price) ** (1 / horizon_years) - 1
target_market_cap        = target_price * target_assumed_shares
P/S: required_annual_revenue    = target_market_cap / assumed_price_to_sales
P/E: required_annual_net_income = target_market_cap / assumed_price_to_earnings
required_metric_cagr     = (required_metric / base_annual_metric) ** (1 / horizon_years) - 1   (base > 0 only)
```

Precision: `Decimal` with 34 significant digits, ROUND_HALF_EVEN. Non-integer powers are computed as `exp(ln(x) / years)`, correctly rounded to 34 digits; tests use a tolerance of 1e-12. Results are ratios (0.25 = 25%); percent formatting happens only on the page. A zero or negative base metric returns `not_computable` with a reason; the other calculations remain.

Acceptance example (synthetic): reference 50, target 100, 5 years, 100,000,000 target shares, P/S 25, base revenue 200,000,000 -> return 1.0, market cap 10,000,000,000, required revenue 400,000,000, CAGR 0.148698355. With P/E 20: required net income 500,000,000.

## Public data sources and cost

| Source | Used for | Key / cost | Notes |
| --- | --- | --- | --- |
| SEC EDGAR submissions `data.sec.gov/submissions` | 10-K / 10-Q / 8-K filing list, links | free, no key | requires a User-Agent with contact email (`SEC_USER_AGENT`); max 10 requests/s |
| SEC XBRL company facts `data.sec.gov/api/xbrl/companyfacts` | annual revenue, annual net income, shares outstanding | free, no key | same User-Agent rule; reported values as tagged by the filer |
| Yahoo Finance chart `query1.finance.yahoo.com/v8/finance/chart` | daily and monthly closes, historical volatility, ^TNX, SPY, QQQ | free, no key | unofficial endpoint without published terms or service guarantee; may rate-limit; sent with `TickerCase/0.2` as User-Agent, no email |
| Yahoo Finance options `query2.finance.yahoo.com/v7/finance/options` | option chains, implied volatility | free, no key | needs a session cookie and crumb (handled by `YahooAuthedClient`); unofficial |
| SEC XBRL frames `data.sec.gov/api/xbrl/frames` | revenue / net income of all filers for a calendar year | free, no key | SEC User-Agent rule |
| SEC Archives Form 4 XML | insider transactions | free, no key | SEC User-Agent rule; at most 40 filings per run |
| Polymarket `gamma-api.polymarket.com/public-search` | event contracts on the ticker | free, no key | usually weekly or monthly contracts |
| CNN Fear & Greed `production.dataviz.cnn.io` | market sentiment | free, no key | needs browser headers |

None of the sources in this version needs a paid plan. Paid items would only appear with later features: an AI API for OA.2–OA.3 / OA.10–OA.11 is billed per token, and a licensed market-data feed would replace the unofficial quote endpoint for production use. Stooq was tested as an alternative price source and now requires a browser JavaScript check, so it cannot be called from code.

## Data modes

| Mode | Network | Data mode on records | Use |
| --- | --- | --- | --- |
| `live` | yes | `live` | real requests; SEC needs `SEC_USER_AGENT` |
| `record` | yes | `live` | live + writes a snapshot per URL to `TICKERCASE_SNAPSHOT_DIR` |
| `replay` | no | `replay` | reads recorded snapshots; a missing snapshot is an error, never a live fallback; `source_captured_at` keeps the original capture time |
| `synthetic` | no | `synthetic` | bundled fictional company `SYNT` (filings, XBRL facts, prices) for demos and tests |

The mode applies to every external source. Each source fails on its own: a failure produces a `provider_errors` entry, the affected checks become *missing*, and the calculations stay. No mode substitutes data from another mode or source.

SEC access: requests send the configured User-Agent (application name + your contact email; there is no default), are throttled (default 0.2 s between requests), time out (default 10 s), retry a bounded number of times on timeouts and 5xx, honour `Retry-After` on 429 up to a cap, and report 403 with the likely causes. Responses are cached in memory (ticker map 24 h, submissions 10 min, company facts 1 h, prices 5 min).

## Evidence checks and verdict (rules-v1, provisional)

| Check | Compares | supporting | neutral | contrary |
| --- | --- | --- | --- | --- |
| E1 growth (core) | required CAGR of the valuation metric from the latest reported fiscal year vs reported CAGR over up to 3 years | required <= reported | gap <= 5 pp | gap > 5 pp, or reported base <= 0 |
| E2 multiple | assumed multiple vs today's (last close x reported shares / latest annual metric) | <= today's | <= 1.25x | > 1.25x |
| E3 share count | implied yearly share change vs reported change over up to 3 years | >= reported - 1 pp | >= reported - 3 pp | faster reduction |
| E4 profitability | latest net income (P/S cases) | context only | | |
| E5 freshness | age of the latest 10-K / 10-Q | | <= 135 days | older -> *missing* |
| E6 price history | past yearly price change vs required | context only | | |

Verdict:

- **Insufficiently Specified** – E1 could not be checked (missing data, one year of history, non-USD claim).
- **Not Supported Today** – E1 contrary and (another check contrary, or growth gap > 15 pp, or reported base <= 0).
- **Supported Today** – E1 supporting and no check contrary.
- **Partially Supported** – every other case.

Time base: required growth runs from the end of the latest reported fiscal year to the target date (reference date + horizon), so the months already passed since that fiscal year end are counted. Share-count changes use the same rule.

Sensitivity: the result includes the required yearly growth of the valuation metric for multiples at 0.5x–2x the assumed one and horizons from h-2 to h+5 years, marked against the reported growth with the E1 thresholds.

Recheck conditions link to the check or input they come from: next annual metric below the required path (R1), share count above the assumed path (R2), today's multiple falling far below the assumption (R3), the next 10-Q / 10-K (R4), each missing item (R-Ex), and any change to a confirmed assumption (R9).

The thresholds are provisional engineering choices. The Business or Mission Analysis leaves quantitative verdict thresholds to team validation; change them in `src/tickercase/analysis.py` and its docstring together. Synthetic examples: the P/S example gives Partially Supported, the P/E example Not Supported Today.

## Probability reference (optional extra)

With `probability_drift` set, the result includes P(price at the horizon >= level) for a ladder of price levels and the target, plus the 10/50/90 % price quantiles, under a lognormal model with constant drift and volatility. S_0 is the confirmed reference price; volatility is yours or the historical volatility of daily adjusted closes. The section lists its assumptions and limitations, is labelled as a model output and is not market-implied. It does not change the verdict.

## Result shape (`CaseResult`)

`case_id`, `created_at`, `status`, `input_fingerprint`, `confirmed_claim` (values, fingerprint, `confirmed_at`, per-field provenance), `calculations[]`, `evidence_records[]` (filings), `coverage`, `reported_facts`, `market`, `evidence_items[]`, `verdict` (label, display, as_of, rationale, limitations, basis, rules_version), `recheck_conditions[]`, `probability`, `sensitivity`, `provider_errors[]`, `validation_issues[]`, `missing_fields[]`, `warnings[]`, `warnings_zh[]`, `data_modes`, `mixed_sources`, `analysis_status`, `disclaimer`. Evidence items, verdict, recheck conditions, coverage and probability texts carry a `_zh` Chinese counterpart next to each English field. Example outputs: `examples/output_synthetic_ps.json`, `examples/output_synthetic_pe.json`.

Statuses: `evaluated`, `evaluated_with_provider_errors`, `blocked_invalid_input` (HTTP 422), `blocked_unconfirmed` and `blocked_confirmation_stale` (HTTP 409). Cases are stored as JSON in `TICKERCASE_CASE_DIR` (default `data/cases`, git-ignored).

## Layout

```
app.py                              Streamlit page
src/tickercase/models.py            typed inputs, results, evidence, verdict
src/tickercase/validation.py        validation, missing fields, confirmation fingerprint
src/tickercase/calculations.py      pure Decimal calculations
src/tickercase/analysis.py          evidence checks, verdict rules, recheck conditions
src/tickercase/probability.py       optional lognormal probability reference
src/tickercase/extract.py           rule-based ticker / target / horizon extraction from the claim sentence
src/tickercase/report.py            layered plain-language report
src/tickercase/oracle.py            probability methods and cross-check
src/tickercase/narrative.py         Claude narrative and number verifier
src/tickercase/http_client.py       live / record / replay / fake network boundary
src/tickercase/providers/sec.py     SEC submissions adapter
src/tickercase/providers/sec_facts.py SEC XBRL company facts adapter
src/tickercase/providers/market.py  daily price adapter
src/tickercase/service.py           workflow, prefill and CaseResult assembly
src/tickercase/api.py               FastAPI app
src/tickercase/cli.py               command line
scripts/live_smoke.py               one-off live check with recording
scripts/build_synthetic_snapshots.py
examples/                           synthetic inputs, outputs and snapshots
tests/                              pytest suite (offline); tests/fixtures/README.md explains data provenance
```

## Live verification

`scripts/live_smoke.py --ticker AAPL` performs one recorded live run of all three sources and writes a summary to `data/smoke/`. The opt-in test `TICKERCASE_RUN_LIVE=1 pytest -m live` checks SEC filings without recording.

Status (2026-10-02, Windows, home network): a full live case ran through the page (TSLA, target 1000 by 2030) with SEC filings, XBRL facts and Yahoo prices all returning data; live reference snapshots for TSLA and NVDA returned split-aware share trends. No live run has been recorded as replayable snapshots yet.

## Next batch (not implemented)

AI extraction of claim fields (OA.2–OA.3), AI-drafted and checked analysis of filing text (OA.10–OA.11), fetching older submission files, team validation of the rules-v1 thresholds.

Design references and what was adapted: see `NOTICE.md`.
