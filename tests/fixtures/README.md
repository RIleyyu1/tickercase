# Test data provenance

All SEC- and quote-shaped payloads used by the test suite are **synthetic** (hand-written or generated from a fixed seed). None of them is a network recording.

| Location | Kind | Notes |
| --- | --- | --- |
| `tests/conftest.py` (`TICKERS_PAYLOAD`, `submissions_payload`) | synthetic | Built inline per test for edge cases: empty data, wrong types, short arrays, amendments, coverage gaps. |
| `tests/test_facts_market.py`, `tests/test_analysis.py` | synthetic | XBRL facts, chart payloads and market snapshots built inline for parser and rule edge cases. |
| `examples/sec_synthetic_snapshots/*.json` | synthetic | Snapshot envelopes with `"origin": "synthetic_fixture"`; replay reports `data_mode = "synthetic"`. Regenerate with `python scripts/build_synthetic_snapshots.py`. Includes submissions, XBRL company facts and five years of daily closes (seeded random walk ending at 50.00). The company `SYNT`, CIK `0009999901`, every accession number and every price are fictional. |

Real recordings are produced only by `record` mode or `scripts/live_smoke.py` and land in `data/snapshots/` (git-ignored). They carry `"origin": "live_recorded"` and replay as `data_mode = "replay"` with the original `captured_at`. No live recording is committed (see README, "Live verification").
