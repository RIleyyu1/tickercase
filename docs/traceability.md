# Traceability: model to code

Each row links one function of the Innoslate model (project 650, Team 12 TickerCase) to the module that carries it
out, the requirement it answers and the test that checks it. The IDs are the model's own: function IDs from the
IDEF0 diagrams, requirement IDs from the SRD (SR), the system specification (SS) and the C.2 component
requirements specification (CRS.C2).

The function worked through in full is **A2 Acquire Dated Evidence**, performed by **C.2 Evidence Acquisition
Subsystem**. The tests named `test_SR_…` and `test_CR_…` are in `tests/test_functional_requirements.py`; CI runs
them on every push (`.github/workflows/tests.yml`).

## A2 Acquire Dated Evidence

| Function ID | Function | Performed by | Module path | Requirement ID | Test name |
|---|---|---|---|---|---|
| A2 | Acquire Dated Evidence | C.2 Evidence Acquisition Subsystem | `src/tickercase/acquisition.py::acquire_dated_evidence` | SR.1.2 | `test_SR_1_2_acquires_the_evidence_set_for_the_confirmed_claim_from_the_public_sources` |
| A2.1 | Request Filings and Reported Facts | C.2.1 SEC EDGAR Client | `src/tickercase/acquisition.py::request_filings_and_reported_facts`; `providers/sec.py`, `providers/sec_facts.py`, `providers/sec_frames.py`, `providers/insiders.py` | SR.1.2.3, SS.2.1 | `test_SR_1_2_3_sends_the_declared_contact_user_agent_with_the_filing_service_request` |
| A2.2 | Request Market and Option Data | C.2.2 Market Data Client | `src/tickercase/acquisition.py::request_market_and_option_data`; `providers/market.py`, `providers/options.py`, `providers/calendar.py`, `providers/yahoo_auth.py` | SR.1.2.4, SS.2.2 | `test_SR_1_2_4_requests_the_option_chain_of_the_claim_ticker` |
| A2.3 | Request Context Signals | C.2.3 Context Signal Client | `src/tickercase/acquisition.py::request_context_signals`; `providers/sentiment.py`, `providers/news.py` | SR.1.2.5, SS.2.3 | `test_SR_1_2_5_excludes_the_context_signal_from_the_verdict_rules` |
| A2.4 | Stamp Provenance | C.2.4 HTTP Fetcher and Snapshot Store | `src/tickercase/acquisition.py::stamp_provenance`; `src/tickercase/http_client.py` | SR.1.2.1, SS.2.4 | `test_SR_1_2_1_attaches_the_provenance_record_to_the_evidence_record` |
| A2.5 | Isolate Source Failures | C.2.5 Source Guard | `src/tickercase/acquisition.py::SourceGuard` | SR.1.2.2, SS.2.5 | `test_SR_1_2_2_converts_the_failed_source_request_into_the_source_error_record` |

### ICOM of A2 and the signature

The signature of `acquire_dated_evidence` is built from the arrows of the A2 box on the A0 diagram.

| ICOM | Information product | In the code |
|---|---|---|
| Input | IO.5 Confirmed Claim | parameter `confirmed_claim: ConfirmedClaim` |
| Input | IO.7 Source Evidence and Reference Snapshot | parameter `source_evidence` (the responses come in through it) |
| Control | C2 Provider Access Terms | parameter `provider_access_terms: Settings` (declared User-Agent, request spacing, retry limits) |
| Control | C3 Data Mode Setting | parameter `data_mode_setting` (`live`, `record`, `replay`, `synthetic`) |
| Output | IO.6 Evidence Query | `AcquiredEvidence.evidence_query` |
| Output | IO.29 Evidence Set | `AcquiredEvidence.evidence_set` |
| Output | IO.30 Source Error Records | `AcquiredEvidence.source_error_records` |
| Mechanism | C.2 Evidence Acquisition Subsystem | the module `src/tickercase/acquisition.py` |

`test_A2_signature_is_built_from_its_ICOM` fails if the parameters or the outputs are renamed away from the model.

### Internal flows of the A2 diagram

| Flow | From → to | Conduit | Model in the code | Operation (`contracts/api-v1.yaml`) |
|---|---|---|---|---|
| IO.37 Filing Records | A2.1 → A2.4 | IF.18 SEC Exchange | `acquisition.FilingRecords` | `requestFilingsAndReportedFacts` — `POST /evidence/filings` |
| IO.38 Market Records | A2.2 → A2.4 | IF.19 Market Exchange | `acquisition.MarketRecords` | `requestMarketAndOptionData` — `POST /evidence/market` |
| IO.39 Context Records | A2.3 → A2.4 | IF.20 Context Exchange | `acquisition.ContextRecords` | `requestContextSignals` — `POST /evidence/context` |
| IO.29 Evidence Set, IO.30 Source Error Records | A2.4, A2.5 → A3, A4, A5, A6 | IF.9 to IF.12 | `acquisition.EvidenceSet`, `models.ProviderErrorRecord` | `acquireDatedEvidence` — `POST /evidence` |

`test_contract_operations_of_the_A2_internal_flows_are_served` checks every `operationId` of the contract against
the running API.

### Component requirements of C.2 (CRS.C2)

| Requirement ID | Statement (short) | Parent | Component | Module path | Test name |
|---|---|---|---|---|---|
| CR.2.0.1 | Complete the source requests of the live case within 20 seconds | NFR.1 (apportionment: 20 s of 30 s) | C.2 | `src/tickercase/acquisition.py` | timed on live runs, not in the offline suite (`scripts/live_smoke.py`) |
| CR.2.0.2 | Exclude the paid data services from the evidence requests | NFR.8 | C.2 | `src/tickercase/acquisition.py::SOURCES` | `test_CR_2_0_2_excludes_the_paid_data_services_from_the_evidence_requests` |
| CR.2.1.1 | Space consecutive filing requests by 200 ms | NFR.3, SS.2.1 | C.2.1 | `src/tickercase/http_client.py::LiveHttpClient._throttle` | `tests/test_http_client.py::test_throttle_spaces_requests` |
| CR.2.1.2 | Refuse the live filing request without the contact address | SS.2.1 | C.2.1 | `src/tickercase/http_client.py::validate_user_agent` | `test_CR_2_1_2_refuses_the_live_filing_request_without_the_contact_address` |
| CR.2.1.3 | Discard the peer revenue value reported in the wrong scale | SS.4.1 | C.2.1 | `src/tickercase/providers/sec_frames.py` | `tests/test_fixes_v013.py::test_a_mis_scaled_company_is_left_out_of_the_base_rate` |
| CR.2.1.4 | Remove the investment fund filer from the base-rate sample | SS.4.1 | C.2.1 | `src/tickercase/providers/sec_frames.py` | `tests/test_fixes_v013.py::test_funds_and_commodity_trusts_are_not_similar_companies` |
| CR.2.1.5 | Report the count of the parsed insider-trade filings | SS.2.1 | C.2.1 | `src/tickercase/providers/insiders.py` | `tests/test_fixes_v013.py::test_insider_counts_over_part_of_the_filings_say_so` |
| CR.2.2.1 | Return the month-end closes on the gap-free month grid | SS.4.1 | C.2.2 | `src/tickercase/providers/market.py::monthly_closes` | `tests/test_fixes_v013.py::test_monthly_windows_are_months_even_when_yahoo_sends_quarters` |
| CR.2.2.2 | Fill the missing close of the evaluation day from the quote | SS.2.4 | C.2.2 | `src/tickercase/providers/market.py` | `tests/test_fixes_v013.py::test_the_latest_close_comes_from_the_quote_when_the_bar_has_none` |
| CR.2.2.3 | Fetch the option expiries that bracket the earnings date | SS.4.3 | C.2.2 | `src/tickercase/providers/options.py::YahooOptionsProvider.around` | live check only (NVDA earnings of 11-17: expiries 11-13 and 11-20); no offline test yet |
| CR.2.3.1 | Record the publication date with the headline | SS.2.3 | C.2.3 | `src/tickercase/providers/news.py` | `tests/test_catalysts.py::test_news_parsing_and_queries` |
| CR.2.3.2 | Report the missing earnings date in the case notes | SS.2.5 | C.2.3 | `src/tickercase/acquisition.py::request_market_and_option_data` | `tests/test_fixes_v013.py::test_no_earnings_date_is_a_note_not_a_failed_source` |
| CR.2.4.1 | Serve the recorded response in replay mode without the live request | NFR.7, NFR.9, SS.2.4 | C.2.4 | `src/tickercase/http_client.py::ReplayHttpClient` | `test_CR_2_4_1_serves_the_recorded_response_in_replay_mode_without_the_live_request` |
| CR.2.4.2 | Use the case data mode for the case requests | NFR.9, SS.2.4 | C.2.4 | `src/tickercase/acquisition.py::SourceGateway` | `test_CR_2_4_2_uses_the_case_data_mode_for_the_case_requests` |
| CR.2.4.3 | Wait for the Retry-After interval of the throttled response | NFR.3 | C.2.4 | `src/tickercase/http_client.py::LiveHttpClient.get_json` | `tests/test_http_client.py::test_429_honours_retry_after_seconds_then_succeeds` |
| CR.2.5.1 | Complete the remaining source requests despite the failed request | NFR.2, SS.2.5 | C.2.5 | `src/tickercase/acquisition.py::SourceGuard` | `test_CR_2_5_1_completes_the_remaining_source_requests_despite_the_failed_request` |

Two rows have no offline test: CR.2.0.1 is a time budget on live sources, and CR.2.2.3 was checked on a live case
only. Both are listed here so the gap is visible.

## The other first-level functions

| Function ID | Function | Performed by | Module path | Requirement ID | Tests |
|---|---|---|---|---|---|
| A1 | Read and Confirm Claim | C.1 Claim Intake Subsystem | `src/tickercase/extract.py`, `src/tickercase/validation.py`, `app.py` (① Claim, ② Check) | SR.1.1 | `tests/test_extract_report.py`, `tests/test_validation.py`, `tests/test_app.py` |
| A3 | Compute Required Numbers | C.3 Calculation Engine | `src/tickercase/calculations.py` | SR.1.3 | `tests/test_calculations.py` |
| A4 | Estimate Probability Range | C.4 Probability Engine | `src/tickercase/oracle.py`, `src/tickercase/structure.py`, `src/tickercase/catalysts.py` | SR.1.4 | `tests/test_oracle.py`, `tests/test_structure.py`, `tests/test_event_scenario.py` |
| A5 | Assess Evidence and State Verdict | C.5 Evidence Analyzer | `src/tickercase/analysis.py` | SR.1.5 | `tests/test_analysis.py` |
| A6 | Present and Explain Investment Case | C.6 Case Presentation Subsystem | `src/tickercase/report.py`, `src/tickercase/narrative.py`, `src/tickercase/storage.py`, `app.py` (report pages) | SR.1.6 | `tests/test_extract_report.py`, `tests/test_narrative.py`, `tests/test_page_scan.py` |
