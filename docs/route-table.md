# Route table: product entry points to use cases

Each row is one way into the product, the use-case step it serves and the function of the model that runs behind
it (Lab Manual Ch 6). The use case is **UC.1 Evaluate a Stock Claim**; its actions are numbered OA.1 to OA.21 in
the Innoslate model (project 650).

The function worked through in full is **A2 Acquire Dated Evidence**. In UC.1 it is the three actions
OA.6 Request Public Evidence, OA.9 Organize Evidence Set and OA.21 Record Source Errors, answered by
OA.7 Return Dated Source Records of the Public Financial Evidence Sources.

## Entry points that reach A2

| Entry point | Kind | Where in the code | Use case and actions | Function |
|---|---|---|---|---|
| ③ Run → **RUN** key (`btn_run`) | Page (Streamlit, `streamlit run app.py`) | `app.py::run_case` → `CaseService.evaluate` → `acquisition.acquire_dated_evidence` | UC.1: OA.6, OA.7, OA.9, OA.21, then OA.8 to OA.15 | A2, then A3 to A6 |
| ③ Run → the pipeline board | Page | `app.py::run_case` (`on_step`) ← `acquisition.SourceGuard.step` | UC.1: OA.6, OA.21 (each source request shown as running, ok or failed) | A2.1 to A2.5 |
| ⑦ Data report page (`nav_data`) | Page | `app.py::page_data` | UC.1: OA.15 Present Investment Case (sources, capture times, data modes, failed sources) | A6.2, showing the outputs of A2.4 and A2.5 |
| `POST /cases` | API (`uvicorn --factory tickercase.api:app_factory`) | `api.py::run_case` → `CaseService.evaluate` | UC.1: OA.6 to OA.15 | A2 to A6 |
| `POST /evidence` | API | `api.py::acquire_evidence` → `CaseService.acquire` → `acquisition.acquire_dated_evidence` | UC.1: OA.6, OA.7, OA.9, OA.21 | A2 |
| `POST /evidence/filings` | API | `CaseService.acquire(flow="filings")` → `acquisition.request_filings_and_reported_facts` | UC.1: OA.6, OA.7 (filing service) | A2.1 |
| `POST /evidence/market` | API | `CaseService.acquire(flow="market")` → `acquisition.request_market_and_option_data` | UC.1: OA.6, OA.7 (market data) | A2.2 |
| `POST /evidence/context` | API | `CaseService.acquire(flow="context")` → `acquisition.request_context_signals` | UC.1: OA.6, OA.7 (context signals) | A2.3 |
| `python -m tickercase.cli run <claim.json> --confirm` | Command line | `cli.py::main` → `CaseService.evaluate` | UC.1: OA.5 (with `--confirm`), OA.6 to OA.15 | A2 to A6 |

No entry point reaches A2 without a confirmed claim: the page keeps RUN disabled until the inputs are armed, and
the API answers 409 (`blocked_unconfirmed`, `blocked_confirmation_stale`) before any source request is sent.

## The other entry points of UC.1

| Entry point | Kind | Where in the code | Use case and actions | Function |
|---|---|---|---|---|
| ① Claim → **READ** key (`btn_prefill`) | Page | `app.py` → `CaseService.extract`, `CaseService.reference_snapshot` | UC.1: OA.1 Submit Stock Claim, OA.2 Read Claim Fields, OA.3 Flag Unread Claim Fields | A1.1 |
| ② Check → **ARM** key (`btn_confirm`) | Page | `app.py` → `validation.confirm` | UC.1: OA.4 Present Claim Review, OA.5 Confirm Claim and Assumptions, OA.17 Fingerprint Confirmed Claim | A1.2 to A1.4 |
| Report pages ① Answer to ⑥ Narrative (`nav_answer` … `nav_nar`) | Page | `app.py::PAGES` | UC.1: OA.15 Present Investment Case, OA.16 Review Investment Case, OA.10, OA.19, OA.20 | A6 |
| `POST /claims/extract`, `GET /reference/{ticker}` | API | `api.py` → `CaseService.extract`, `reference_snapshot` | UC.1: OA.2, OA.3 | A1.1 |
| `POST /claims/validate`, `POST /claims/confirm` | API | `api.py` → `validation.validate_draft`, `validation.confirm` | UC.1: OA.4, OA.5, OA.17 | A1.2 to A1.4 |
| `GET /cases/{case_id}` | API | `api.py::get_case` → `storage.CaseStore` | UC.1: OA.15 (a stored case record) | A6.4 |
| `GET /health` | API | `api.py::health` | none (operations check) | none |
