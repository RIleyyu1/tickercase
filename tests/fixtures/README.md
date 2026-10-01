# Test data provenance

All SEC-shaped payloads used by the test suite are **synthetic** (hand-written). None of them is a network recording.

| Location | Kind | Notes |
| --- | --- | --- |
| `tests/conftest.py` (`TICKERS_PAYLOAD`, `submissions_payload`) | synthetic | Built inline per test for edge cases: empty data, wrong types, short arrays, amendments, coverage gaps. |
| `examples/sec_synthetic_snapshots/*.json` | synthetic | Snapshot envelopes with `"origin": "synthetic_fixture"`; replay reports `data_mode = "synthetic"`. Regenerate with `python scripts/build_synthetic_snapshots.py`. The company `SYNT`, CIK `0009999901` and every accession number are fictional. |

Real recordings are produced only by `record` mode or `scripts/live_smoke.py` and land in `data/snapshots/` (git-ignored). They carry `"origin": "live_recorded"` and replay as `data_mode = "replay"` with the original `captured_at`. As of the first commit no live recording has been made (see README, "Live verification").
