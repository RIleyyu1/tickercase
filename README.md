# TickerCase

TickerCase turns a stock claim ("SYNT will be $100 in five years") into an inspectable investment case: explicit assumptions, reproducible numbers, dated public evidence, an evidence-as-of verdict and the conditions that should trigger a new review. It follows UC.1 *Evaluate a Stock Claim* from the Team 12 operational concept.

It does **not** trade, manage portfolios, predict prices or give investment advice. The verdict describes the state of the evidence on a date. An optional probability section shows a model output under your assumptions; it never feeds the verdict.

## Flow (UC.1)

1. Enter the claim and structured assumptions (page form, API or JSON file). The page can prefill reference values (latest close, reported shares, latest annual revenue / net income) from public data; you still review them. (OA.1, OA.4)
2. Confirm. The confirmation is bound to a SHA-256 fingerprint of the exact inputs; any edit makes it stale. (OA.5)
3. Deterministic calculation (`Decimal`, no network). (OA.8)
4. Public evidence: SEC filings, SEC XBRL financial facts, daily prices. (OA.6, OA.7, OA.9)
5. Deterministic evidence checks classified as supporting / contrary / missing / context, a verdict and recheck conditions. (OA.12–OA.14)
6. Result with calculations, evidence and sources, verdict, recheck conditions, gaps, provider errors and the data mode of every part. (OA.15)

Not implemented yet: AI extraction of claim fields from free text (OA.2–OA.3) and AI-drafted analysis of filing text (OA.10–OA.11).

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

In the sidebar click "合成示例 · P/S" (or P/E), then "确认以上输入", then "运行评估". The examples use the fictional company `SYNT` and synthetic data. For a real company choose `live`, enter a ticker, click "带入公开数据", check the values and assumptions, confirm and run.

API:

```bash
uvicorn --factory tickercase.api:app_factory --reload
# POST /claims/validate, POST /claims/confirm, POST /cases, GET /cases/{id}, GET /reference/{ticker}, GET /health
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
| `target_assumed_shares` | required, > 0 | assumption for the target date, kept separate from `current_shares` |
| `valuation_method` | required | `price_to_sales` or `price_to_earnings` |
| `valuation_multiple` | required, > 0 | assumption |
| `current_shares` | optional | reference value; adds `implied_share_count_change` |
| `base_annual_metric`, `base_metric_currency`, `base_metric_period` | optional | revenue for P/S, net income for P/E; only `required_metric_cagr` depends on them |
| `filings_since` | optional | start of the filing window; enables the coverage check |
| `probability_drift` | optional extra | yearly drift assumption, between -1 and 1; enables the probability section |
| `probability_volatility` | optional extra | yearly volatility, > 0 and <= 3; defaults to historical volatility |
| `field_sources` | set by prefill | field -> public source; recorded as provenance while the value is unchanged |

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
| Yahoo Finance chart `query1.finance.yahoo.com/v8/finance/chart` | daily closes, historical volatility | free, no key | unofficial endpoint without published terms or service guarantee; may rate-limit; sent with `TickerCase/0.2` as User-Agent, no email |

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

Recheck conditions link to the check or input they come from: next annual metric below the required path (R1), share count above the assumed path (R2), today's multiple falling far below the assumption (R3), the next 10-Q / 10-K (R4), each missing item (R-Ex), and any change to a confirmed assumption (R9).

The thresholds are provisional engineering choices. The Business or Mission Analysis leaves quantitative verdict thresholds to team validation; change them in `src/tickercase/analysis.py` and its docstring together. Synthetic examples: the P/S example gives Partially Supported, the P/E example Not Supported Today.

## Probability reference (optional extra)

With `probability_drift` set, the result includes P(price at the horizon >= level) for a ladder of price levels and the target, plus the 10/50/90 % price quantiles, under a lognormal model with constant drift and volatility. S_0 is the confirmed reference price; volatility is yours or the historical volatility of daily adjusted closes. The section lists its assumptions and limitations, is labelled as a model output and is not market-implied. It does not change the verdict.

## Result shape (`CaseResult`)

`case_id`, `created_at`, `status`, `input_fingerprint`, `confirmed_claim` (values, fingerprint, `confirmed_at`, per-field provenance), `calculations[]`, `evidence_records[]` (filings), `coverage`, `reported_facts`, `market`, `evidence_items[]`, `verdict` (label, display, as_of, rationale, limitations, basis, rules_version), `recheck_conditions[]`, `probability`, `provider_errors[]`, `validation_issues[]`, `missing_fields[]`, `warnings[]`, `data_modes`, `mixed_sources`, `analysis_status`, `disclaimer`. Example outputs: `examples/output_synthetic_ps.json`, `examples/output_synthetic_pe.json`.

Statuses: `evaluated`, `evaluated_with_provider_errors`, `blocked_invalid_input` (HTTP 422), `blocked_unconfirmed` and `blocked_confirmation_stale` (HTTP 409). Cases are stored as JSON in `TICKERCASE_CASE_DIR` (default `data/cases`, git-ignored).

## Layout

```
app.py                              Streamlit page
src/tickercase/models.py            typed inputs, results, evidence, verdict
src/tickercase/validation.py        validation, missing fields, confirmation fingerprint
src/tickercase/calculations.py      pure Decimal calculations
src/tickercase/analysis.py          evidence checks, verdict rules, recheck conditions
src/tickercase/probability.py       optional lognormal probability reference
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

Status (2026-10-01, Windows, home network): `www.sec.gov` and `data.sec.gov` (submissions and company facts) and the Yahoo chart endpoint answered HTTP 200. A full live case run through TickerCase with `SEC_USER_AGENT` set has not been recorded yet.

## Next batch (not implemented)

AI extraction of claim fields (OA.2–OA.3), AI-drafted and checked analysis of filing text (OA.10–OA.11), fetching older submission files, team validation of the rules-v1 thresholds.

Design references and what was adapted: see `NOTICE.md`.
