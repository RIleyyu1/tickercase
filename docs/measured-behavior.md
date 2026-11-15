# Measured behavior

Thirty live runs of one case, timed at the participant boundaries of the UC.1 sequence diagram (Lab Manual Chapter 10.4).
The simulated side of the comparison is in the Innoslate model (project 650, scenarios SC.1 and SC.2) and in the course report.

- Claim: `NVDA will hit $300 in 3 years`, live sources, 2026-11-15 13:59 to 14:19 Eastern, version 0.15.0.
- Each run started with no response held in memory (`TICKERCASE_TIMING_COLD=1`). All 30 completed with no source error.
- Each run sent 88 requests: 4 before the confirmation, 84 after it (45 SEC EDGAR, 36 Yahoo Finance, 3 context sources).
- The user was a script, so the user's own times are not those of a person and are left out.

To repeat: `python scripts/measure_runs.py --runs 30`, then `python scripts/timing_report.py data/timings/<folder>`.

## Stages (seconds, 30 runs)

| Stage | Participant | Min | Mean | Max |
|---|---|---|---|---|
| READ pressed to the Check page ready | C.1 | 1.62 | 2.08 | 2.65 |
| CONFIRM pressed to RUN enabled | C.1 | 0.72 | 0.98 | 1.11 |
| A2.1 Request Filings and Reported Facts (41 requests) | C.2.1, SEC EDGAR | 8.14 | 8.34 | 10.46 |
| A2.2 Request Market and Option Data (25 requests) | C.2.2, Yahoo Finance | 11.39 | 11.54 | 12.33 |
| A2.3 Request Context Signals (4 requests) | C.2.3, context sources | 1.07 | 1.22 | 1.70 |
| A2.4 Stamp Provenance | C.2.4 | < 0.01 | < 0.01 | < 0.01 |
| A2 Acquire Dated Evidence, whole | C.2 | 20.65 | 21.10 | 23.38 |
| A3 Compute Required Numbers | C.3 | < 0.01 | < 0.01 | < 0.01 |
| A5 Assess Evidence and State Verdict | C.5 | < 0.01 | < 0.01 | < 0.01 |
| A4 Estimate Probability Range (14 follow-up requests) | C.4 | 5.37 | 5.80 | 8.76 |
| A6 in the service, plus the page drawing the report | C.6 | 0.85 | 1.40 | 1.88 |
| RUN pressed to the report ready | whole system | 26.98 | 28.30 | 31.15 |

## Where the time of a case goes (mean, RUN to result in the service: 26.98 s)

| Part | Seconds | Share |
|---|---|---|
| Request spacing wait of the fetcher (C.2.4) | 16.30 | 60 % |
| Inside the outside services | 10.16 | 38 % |
| TickerCase's own work | 0.51 | 2 % |

## Against the requirements

- **NFR.1** (live evaluation within 30 s): met on average (28.3 s), with no margin; 2 of 30 runs took longer than 30 s.
- **CR.2.0.1** (source requests of C.2 within 20 s): not met; A2 took 20.7 to 23.4 s. The 14 follow-up requests of A4 (5.8 s) are outside this measure.
- **NFR.3 / CR.2.1.1** (200 ms between filing requests): met; the smallest of 1,320 gaps between SEC EDGAR requests is 0.200 s.

Limits: one ticker, one computer and network, one afternoon, first case of a session. A second case of the same session is faster,
because responses are kept for 5 minutes to 24 hours.
