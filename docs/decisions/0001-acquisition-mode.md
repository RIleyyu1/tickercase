# ADR 0001: A2 asks the SEC and the market sources side by side

- Status: accepted, 2026-11-21. Implemented in v0.24.0 (`TICKERCASE_ACQUISITION`, default `parallel`).
- Decision owner: Team 12. Trade study: Lab Manual Chapter 11; course report, System Analysis section.
- Model: Innoslate project 650, decision D.1, alternatives AL.1 to AL.3, criteria EC.1 to EC.5, scenarios SC.1 and SC.2.

## The decision that was open

A live case took 28.3 s on average in the 30 runs of Chapter 10, against a limit of 30 s (NFR.1), and 2 of the 30 ran over.
Sixty per cent of that time was the fetcher waiting between requests. The simulation of Chapter 10 (SC.2) said that asking
the two large sources at the same time would save about 10 s, but nothing was built and the team had not chosen.
Since v0.23.0 the product is meant for every retail investor, so the wait before the first answer matters more than before.

## Alternatives

| Id | Alternative | What it is | What it needs |
|---|---|---|---|
| AL.1 | Sequential acquisition | A2.1 (SEC), then A2.2 (market), then A2.3 (context), one request at a time. The product up to v0.23.0. | Nothing new. |
| AL.2 | Parallel acquisition by source | A2.1 and A2.2 run in two lanes at the same time; A2.3 follows. Each source keeps its own fetcher and its own request spacing, so no source is asked faster than before. | Two worker threads; failure records and the request log kept per lane; the page told of progress from the calling thread only; a test for each of these. |
| AL.3 | Parallel acquisition with half the market spacing | AL.2, and the wait between two requests to Yahoo Finance goes from 0.5 s to 0.25 s. | The code of AL.2 and one setting; someone watching for throttled responses, since the source publishes no rate. |

## Criteria and measures

Each criterion traces to a stakeholder need and to entities of the model. The weights come from a swing weight matrix:
importance to the customer and how far the alternatives differ.

### Measured time from RUN to the report (seconds)

| Alt. | Alternative | Runs | Mean | Min | Max | Runs over 30 s |
| --- | --- | --- | --- | --- | --- | --- |
| AL.1 | Sequential acquisition | 10 | 27.3 | 26.7 | 28.3 | 0 |
| AL.2 | Parallel acquisition by source | 10 | 19.6 | 18.6 | 25.7 | 0 |
| AL.3 | Parallel acquisition with half the market request spacing | 10 | 16.7 | 15.0 | 24.9 | 0 |

### Swing weight matrix

| Variation between the alternatives | critical: the product fails its purpose without it | important: the product is clearly worse without it | useful: good to have |
| --- | --- | --- | --- |
| large: the alternatives differ by a large part of the acceptable range | 100 (EC.1) | 60 | 30 |
| medium: they differ, and no alternative leaves the acceptable range | 75 (EC.2) | 45 (EC.5) | 20 |
| small: they are equal or nearly equal | 50 | 25 (EC.3, EC.4) | 10 |

### Criteria, objective functions and weights

| Id | Criterion | Metric (unit) | Worst (value 0) | Best (value 100) | Importance | Variation | Swing weight | Share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EC.1 | Time to the answer | mean time from RUN pressed to the report ready, live sources (s) | 30 | 10 | critical | large | 100 | 37.0% |
| EC.2 | Standing with the market data source | peak request rate to the source that publishes no limit (requests/s) | 10 | 2 | critical | medium | 75 | 27.8% |
| EC.3 | Completion when a source is down | share of the other source's steps that still end ok when one source refuses every request (%) | 0 | 100 | important | small | 25 | 9.3% |
| EC.4 | Same record on replay | share of a replayed case's fields equal to the sequential record, clock times apart (%) | 0 | 100 | important | small | 25 | 9.3% |
| EC.5 | Life cycle cost | team hours to build and keep for two years (h) | 40 | 0 | important | medium | 45 | 16.7% |

### Raw figures and values (0 to 100)

| Id | Criterion | AL.1 raw | AL.2 raw | AL.3 raw | AL.1 value | AL.2 value | AL.3 value |
| --- | --- | --- | --- | --- | --- | --- | --- |
| EC.1 | Time to the answer | 27.3 | 19.6 | 16.7 | 13.5 | 52.0 | 66.5 |
| EC.2 | Standing with the market data source | 2 | 2 | 4 | 100.0 | 100.0 | 75.0 |
| EC.3 | Completion when a source is down | 100 | 100 | 100 | 100.0 | 100.0 | 100.0 |
| EC.4 | Same record on replay | 100 | 100 | 100 | 100.0 | 100.0 | 100.0 |
| EC.5 | Life cycle cost | 0 | 12 | 20 | 100.0 | 70.0 | 50.0 |

### Weighted value

| Id | Criterion | Share | AL.1 | AL.2 | AL.3 |
| --- | --- | --- | --- | --- | --- |
| EC.1 | Time to the answer | 37.0% | 5.0 | 19.3 | 24.6 |
| EC.2 | Standing with the market data source | 27.8% | 27.8 | 27.8 | 20.8 |
| EC.3 | Completion when a source is down | 9.3% | 9.3 | 9.3 | 9.3 |
| EC.4 | Same record on replay | 9.3% | 9.3 | 9.3 | 9.3 |
| EC.5 | Life cycle cost | 16.7% | 16.7 | 11.7 | 8.3 |
|  | **Total value** | 100% | **68.0** | **77.2** | **72.3** |

### Effectiveness, life cycle cost and affordability

| Alt. | Alternative | Effectiveness (0 to 100) | Build hours | Upkeep hours a year | Hours over 2 years | Cost at $50/h (USD) | Against the budget of 40 h | Effectiveness gained per hour |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AL.1 | Sequential acquisition | 61.6 | 0 | 0 | 0 | 0 | within | baseline |
| AL.2 | Parallel acquisition by source | 78.7 | 8 | 2 | 12 | 600 | within | 1.43 |
| AL.3 | Parallel acquisition with half the market request spacing | 76.8 | 8 | 6 | 20 | 1,000 | within | 0.76 |

### Sensitivity: the winner as one weight changes (the others stay)

| Id | Criterion | Weight now | Weight at which the winner changes | Winner at the lowest (10), the present and the highest (100) matrix weight |
| --- | --- | --- | --- | --- |
| EC.1 | Time to the answer | 100 | at 36: AL.1 to AL.2; at 192: AL.2 to AL.3 | 10 → AL.1, 100 → AL.2 |
| EC.2 | Standing with the market data source | 75 | at 23: AL.3 to AL.2 | 10 → AL.3, 75 → AL.2, 100 → AL.2 |
| EC.3 | Completion when a source is down | 25 | none between 0 and 200 (AL.2 throughout) | 10 → AL.2, 25 → AL.2, 100 → AL.2 |
| EC.4 | Same record on replay | 25 | none between 0 and 200 (AL.2 throughout) | 10 → AL.2, 25 → AL.2, 100 → AL.2 |
| EC.5 | Life cycle cost | 45 | at 129: AL.2 to AL.1 | 10 → AL.2, 45 → AL.2, 100 → AL.2 |

Selected: **AL.2 Parallel acquisition by source** (total value 77.2).

## Decision

**AL.2, parallel acquisition by source.** It takes 7.7 s off the mean wait (27.3 s to 19.6 s) without asking any source
faster than the product already did, and it costs the least for what it gains. AL.3 is 2.9 s faster again, but it doubles
the request rate to the one source that publishes no limit and whose terms restrict automated access; the gain does not
pay for that at any weight the matrix allows (the winner changes to AL.3 only if the weight of time were 192, and the
highest weight of the matrix is 100).

The selection holds when one weight is changed at a time across the whole matrix: AL.1 wins only if the weight of time
falls below 36 or the weight of cost rises above 129; AL.3 wins only if the weight of standing with the source falls below 23.

## What the losing alternatives would have required

- **AL.1** required nothing, and left the product with no margin under NFR.1 (0 of 10 runs over 30 s on 2026-11-21, 2 of 30 on 2026-11-15).
- **AL.3** required the same code, a spacing of 0.25 s, and a standing watch on the market source: a count of throttled
  responses for each release (MOS.3), and a way back to 0.5 s. In its 10 runs no response was throttled and none was retried,
  but 2 of the 10 runs had one slow answer from the source (24.9 s and 19.9 s against a usual 15 s). Ten runs cannot tell
  whether that is chance; AL.2 had 1 such run in 10.

## Consequences

- `TICKERCASE_ACQUISITION=sequential` brings AL.1 back, and stays in the test suite: both modes must write the same case.
- The Evidence Query (IO.6) and the Source Error Records (IO.30) list the SEC lane before the market lane in both modes, so a stored case reads the same.
- A failure in one lane is counted against that lane's step only (`SourceGuard._isolated`); before, a step compared the length of a shared list.
- The timeline of a case (`timing.py`) now nests the two lanes inside one span of C.2; `scripts/timing_report.py` still adds the lanes' waits together, so its "own work" figure is wrong for a parallel run. To do.
- A4 still sends 14 follow-up requests after A2 (5.5 s). Moving them into the lanes is the next candidate.
- Standing risk, not changed by this decision: the market data comes from endpoints with no published terms of service level (TR.1, R.2 in `docs/risks.md`).

## Sources checked by the team

| Claim | Source | Read |
|---|---|---|
| The SEC allows at most 10 requests a second and asks for a declared User-Agent. | sec.gov, "Accessing EDGAR Data": "Current max request rate: 10 requests/second." and "Please declare your user agent in request headers". | 2026-11-21 |
| Above that rate the SEC may limit the address for a brief period. | sec.gov/privacy.htm, Internet Security Policy: "further requests from the IP address(es) may be limited for a brief period". | 2026-11-21 |
| Yahoo's terms restrict automated collection, and no page read publishes a request rate for its finance endpoints. | legal.yahoo.com terms, section 2(d): "access or collect data ... using any automated means ... without our express, prior permission". | 2026-11-21 |
| The product sends 5 requests a second to the SEC at most and 2 a second to Yahoo. | `config.py`: `SEC_MIN_INTERVAL_SECONDS` 0.2, `TICKERCASE_MARKET_MIN_INTERVAL_SECONDS` 0.5. | code |
| Times of the three alternatives. | 10 live runs each, 2026-11-21, `data/timings/ch11_*` (not in the repository), `scripts/measure_runs.py`. | measured |
| A thread pool overlaps waiting on the network. | Python documentation, `concurrent.futures`: "ThreadPoolExecutor is often used to overlap I/O instead of CPU work". | 2026-11-21 |
| A lane's changes to context variables stay in its own copy. | Python documentation, `contextvars`: "Any changes that the 'main' function makes to 'var' will be contained in 'ctx'." | 2026-11-21 |

Assumptions (not measured): the best level of the time criterion (10 s); the worst level of the request-rate criterion
(10 a second); the hours of the life cycle cost and the rate of 50 USD an hour; the budget of 40 hours. They are listed
with their reasons in `docs/decisions/0001-inputs.json`.

To repeat: `python scripts/measure_runs.py --runs 10 --out data/timings/ch11_a_sequential` with `TICKERCASE_UI=expert` and
the setting of each alternative, then `python scripts/trade_study.py`.
