# TickerCase

TickerCase turns a stock claim ("SYNT will be $100 in five years") into explicit assumptions, reproducible numbers and traceable SEC filing sources. It shows what market cap, revenue or net income the claim requires under **your** assumptions, lists the company's recent 10-K / 10-Q / 8-K filings with official links, and makes gaps and data failures visible.

It does **not** rate the claim, estimate a probability, trade, manage portfolios or give investment advice. `verdict` is always `null` and `analysis_status` is `not_implemented` in this version.

## Flow

1. Enter the claim and structured assumptions (page form, API or JSON file).
2. Confirm. The confirmation is bound to a SHA-256 fingerprint of the exact inputs; any edit makes it stale.
3. Deterministic calculation (`Decimal`, no network).
4. SEC lookup: ticker -> CIK -> submissions -> filing records.
5. Result: calculations with formula, inputs, units and assumptions; filing records with dates and links; coverage gaps; missing fields; provider errors; data mode of every part.

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

Click "加载合成示例（P/S）", then "确认以上输入", then "运行评估". The example uses synthetic SEC data; switch the mode to `live` (with `SEC_USER_AGENT` set) and enter a real ticker for real filings.

API:

```bash
uvicorn --factory tickercase.api:app_factory --reload
# POST /claims/validate, POST /claims/confirm, POST /cases, GET /cases/{id}, GET /health
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
| `reference_price`, `reference_price_date` | required, > 0 | manual reference value, not from SEC |
| `horizon_years` | required, > 0 | assumption, fractional allowed |
| `target_assumed_shares` | required, > 0 | assumption for the target date, kept separate from `current_shares` |
| `valuation_method` | required | `price_to_sales` or `price_to_earnings` |
| `valuation_multiple` | required, > 0 | assumption |
| `current_shares` | optional | manual reference value; adds `implied_share_count_change` |
| `base_annual_metric`, `base_metric_currency`, `base_metric_period` | optional | revenue for P/S, net income for P/E; only the growth rate depends on them |
| `filings_since` | optional | start of the filing window; enables the coverage check |

Rejected: missing core fields, non-numbers, NaN, Infinity, non-positive prices/multiples/shares/horizon, future dates, and a base-metric currency different from the price currency (no FX conversion is applied). Nothing is filled in silently.

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

Acceptance example (synthetic): reference 50, target 100, 5 years, 100,000,000 target shares, P/S 25, base revenue 200,000,000 -> return 1.0, market cap 10,000,000,000, required revenue 400,000,000, CAGR 0.148698355. With P/E 20: required net income 500,000,000; base net income 250,000,000 gives the same CAGR.

## SEC data modes

| Mode | Network | Data mode on records | Use |
| --- | --- | --- | --- |
| `live` | yes | `live` | real requests; needs `SEC_USER_AGENT` |
| `record` | yes | `live` | live + writes a snapshot per URL to `TICKERCASE_SNAPSHOT_DIR` |
| `replay` | no | `replay` | reads recorded snapshots; a missing snapshot is an error, never a live fallback; `source_captured_at` keeps the original capture time |
| `synthetic` | no | `synthetic` | bundled fictional company `SYNT` for demos and tests |

A failed live or replay request produces a `provider_errors` entry and keeps the calculations. No mode substitutes data from another mode.

SEC access: requests send the configured User-Agent (application name + your contact email; there is no default), are throttled (default 0.2 s between requests, below SEC's 10 requests/second limit), time out (default 10 s), retry a bounded number of times on timeouts and 5xx, honour `Retry-After` on 429 up to a cap, and report 403 with the likely causes. Responses are cached in memory (ticker map 24 h, submissions 10 min).

Limits of this version: only `filings.recent` is parsed. If the requested window starts earlier, the result says `coverage_gap: true` and lists the older submission files SEC points to, without fetching them. Filing records are metadata: the documents have not been read and do not by themselves support or refute the claim. No XBRL financials are extracted; base metrics and share counts come from the user and are labelled as such.

## Result shape (`CaseResult`)

`case_id`, `created_at`, `status`, `input_fingerprint`, `confirmed_claim` (values, fingerprint, `confirmed_at`, per-field provenance), `calculations[]`, `evidence_records[]`, `coverage`, `provider_errors[]`, `validation_issues[]`, `missing_fields[]`, `warnings[]`, `data_modes`, `mixed_sources`, `analysis_status = "not_implemented"`, `verdict = null`, `disclaimer`. Example outputs: `examples/output_synthetic_ps.json`, `examples/output_synthetic_pe.json`.

Statuses: `evaluated`, `evaluated_with_provider_errors`, `blocked_invalid_input` (HTTP 422), `blocked_unconfirmed` and `blocked_confirmation_stale` (HTTP 409). Cases are stored as JSON in `TICKERCASE_CASE_DIR` (default `data/cases`, git-ignored).

## Layout

```
app.py                         Streamlit page
src/tickercase/models.py       typed inputs, results, evidence
src/tickercase/validation.py   validation, missing fields, confirmation fingerprint
src/tickercase/calculations.py pure Decimal calculations
src/tickercase/http_client.py  live / record / replay / fake network boundary
src/tickercase/providers/sec.py SEC submissions adapter
src/tickercase/service.py      workflow and CaseResult assembly
src/tickercase/api.py          FastAPI app
src/tickercase/cli.py          command line
scripts/live_smoke.py          one-off live SEC check with recording
scripts/build_synthetic_snapshots.py
examples/                      synthetic inputs, outputs and SEC snapshots
tests/                         pytest suite (offline); tests/fixtures/README.md explains data provenance
```

## Live verification

`scripts/live_smoke.py --ticker AAPL` performs one recorded live run and writes a summary to `data/smoke/`. The opt-in test `TICKERCASE_RUN_LIVE=1 pytest -m live` does the same check without recording.

Status at the first commit: **live SEC access not verified.** The development environment's network policy rejected connections to `www.sec.gov` (proxy CONNECT 403, reported by the client as `network_error`, not an SEC 403). All tests ran offline against synthetic data.

## Next batch (not implemented)

LLM extraction of claim fields, evidence-based draft analysis, real market prices, XBRL metric normalisation, fetching older submission files.

Design references and what was adapted: see `NOTICE.md`.
