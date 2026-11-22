# Product risk register

Lab Manual Chapter 12. Version 0.25.0, 2026-11-21. The system-level risks R.1 to R.5 are also entities of the Innoslate
model (project 650), where the risk matrix is drawn; this file adds the risks of an AI-enabled product (P.1 to P.6) and
says what the product does about each one today.

## Scales

Every rating runs from 1 (lowest) to 5 (highest).

| Rating | Likelihood | Cost impact | Schedule impact | Technical impact |
|---|---|---|---|---|
| 1 | Almost never in the life of the product | No extra spending | No course milestone moves | The answer is not affected |
| 2 | Possible, not expected within a year | A few hours of work | Days of delay inside one milestone | A side figure is wrong or missing, and the page says so |
| 3 | About once a year | Tens of hours, or one paid data source | One milestone slips | One function is degraded, and the page says so |
| 4 | Several times a year | More than the hours the team has left this term | Two milestones slip | The main number is missing, or shifted by a few points in a way a careful reader can find |
| 5 | Almost every week | More than the project can pay | No runnable product at the end of the term | The main number is wrong and the reader cannot tell |

- **Overall impact** = (cost + schedule + 2 × technical) / 4. Technical impact counts twice: the customer is a retail
  investor with no training in finance (need N13), who is hurt most by an answer that is wrong or missing.
- **Risk level** = likelihood × overall impact, from 1 to 25. Low up to 6 (green), medium above 6 and up to 12 (yellow),
  high above 12 (red).
- On the 5 × 5 matrix a risk sits at its likelihood and at its overall impact rounded to the nearest whole level (a half rounds up).

The ratings are judgments of the team about this product in this course, made on 2026-11-21 from the evidence named in
each row. They are not measurements.

## System-level risks (from the interactions of the architecture model)

Each risk was found by asking of an interface of the model what happens if the interaction is degraded, substituted,
late, missing, or correct but misread (Lab Manual 12.1.1).

| Id | Risk | Interaction (model) | Type, phase | L | Cost | Sched. | Tech. | Overall impact | Level |
|---|---|---|---|---|---|---|---|---|---|
| R.1 | The AI service returns an event score that is well formed and wrong (wrong direction, wrong strength, or an event of another company). Code turns it into points and the main number moves by up to 5 points. The reader is affected. | IF.4.2 Catalyst Proposals, X.C.4 External AI API to C.4.3 | Correctness of AI output; Operations | 4 | 2 | 2 | 4 | 3.00 | 12.0 medium |
| R.2 | The market data source changes its answers, slows or blocks the product. Three of the four methods lose their inputs and the reader gets no main number. | IF.19 Market Exchange, IO.50 and IO.51, X.C.2.2 to C.2.2 | Availability of an outside source; Operations | 4 | 3 | 3 | 4 | 3.50 | **14.0 high** |
| R.3 | A headline written to steer the model reaches the prompt and changes the scores of the events. | IO.53 Context Signal Responses into IF.4.2 | Security (untrusted input); Operations | 2 | 2 | 1 | 4 | 2.75 | 5.5 low |
| R.4 | The reader takes the probability, or the three outcomes of "if you buy today", as advice to buy or sell. | IF.1.2 Case Display, C.6.2 to X.C.1 Research User | Regulatory and reputation; Operations | 3 | 5 | 3 | 2 | 3.00 | 9.0 medium |
| R.5 | The answer comes too late and a first-time reader leaves before it arrives. | IO.48 to IO.51, the request and response pairs of A2 | Performance; Design and Operations | 2 | 1 | 1 | 3 | 2.00 | 4.0 low |

Why these ratings:

- **R.1** Likelihood 4: in the first two real runs of the event update (2026-11-18) the model scored several headlines of one event separately and left out a change of chief executive. Technical 4, not 5: the points are capped at 5 and the page shows the number before and after the events.
- **R.2** Likelihood 4: the team rated the same source at 70 % in October (TR.1); its endpoints are unofficial, it publishes no request rate, and its terms restrict automated access (read 2026-11-21). Cost 3: the way out is a licensed source. Technical 4: without it the options method, the swings method and the stock's own history cannot be computed.
- **R.3** Likelihood 2: it needs a crafted headline about the same company in the news search of the same weeks. Technical 4 for the same reason as R.1.
- **R.4** Cost 5: the team rated the consequence of "read as advice" at 90 % in October (TR.6); a regulator's view of advice is beyond what a course project can pay for. Likelihood 3, one above October: v0.23.0 added a card that begins "if you buy today".
- **R.5** Likelihood 2: 0 of 10 live runs were over 30 s after ADR 0001 (mean 19.6 s); 2 of 30 were over before it.

## The top risk: R.2

R.2 has the highest level (14.0) and is the only risk in the red area.

- **Handling: mitigate.** The team cannot remove the cause (the source is not ours) and will not accept a page with no answer.
- **Built: a fallback, with a guardrail on its age** (`LastGoodHttpClient` in `src/tickercase/http_client.py`).
  - A fallback contains a failure; a guardrail prevents one. The outage of an outside source cannot be prevented from here, so the product contains it: every good answer of the market source is kept, and when the source does not answer, the last good answer to the same request is used.
  - The guardrail prevents the failure the fallback could cause, an old price passing for today's: a stored answer older than 72 hours (`TICKERCASE_FALLBACK_HOURS`) is refused, and so is a stored file that is damaged, dated in the future, or written for another request.
  - The reader is told before the number: which source did not answer, when its figures were fetched, and how many hours before the answer that was. The case record keeps it (`CaseResult.stale_sources`).
- **Test of the failure path** (`tests/test_fallback.py`, 22 tests, all pass): the source refuses every request and the case still gives the same number and says the data is 20 hours old; past the limit the old data is refused and the market steps are reported as failed; a source never reached has nothing to fall back on; six kinds of damaged or planted file are not served; a wrong request (no User-Agent) is never hidden by a stored answer.
- **What is left:** the fallback answers an outage, a block and a throttled or unreadable response. It does not answer a change of the response format that still parses as JSON: that fails later, in the provider, and the step is reported as failed as before. The fallback serves only a request it has seen, so a stock asked for the first time during an outage gets no answer. A licensed source before any public release remains the full answer (TR.1).
- **Monitoring:** see the course report, section Risk Monitoring and Control. In the product: the count of fallback answers of each run is on the timeline (`fallback_answers`), throttled or blocked responses are measure MOS.3, and `scripts/live_smoke.py` is run before each release.

## Risks of an AI-enabled product

| Id | Risk | Model | L | Cost | Sched. | Tech. | Overall | Level | What the product does today | Gap |
|---|---|---|---|---|---|---|---|---|---|---|
| P.1 | The model returns well-formed output that is wrong. In the event scores: R.1. In the narrative: a sentence that reads well and reasons badly. | IF.4.1, IF.4.2, A6.3, A4.3, TR.5 | 4 | 2 | 2 | 4 | 3.00 | 12.0 medium | The model never writes a probability: it picks and scores events and code turns scores into points by a fixed rule, capped at 5 points (`event_tilt.py`). An event is kept only if its source passes the check of the register. Every number of a narrative is checked against the fact table (C.6.3, SR.1.6.2). The page shows the index before and after the events. | The event part has no backtest. The number check does not judge the reasoning. |
| P.2 | The model is unavailable or slow. | IF.4, X.C.4, NFR.8 | 3 | 1 | 1 | 2 | 1.50 | 4.5 low | The main number needs no model call: reading the claim and the four methods are code. The narrative and the event update run only when the reader asks, and the page stays usable when they fail. | None for the main path. |
| P.3 | Untrusted input from a data source reaches the prompt (R.3). | IO.53, IF.4.2 | 2 | 2 | 1 | 4 | 2.75 | 5.5 low | The model's answer is read as data with a fixed set of fields; a score outside the allowed values is dropped; the points are capped. | Headlines go into the prompt as they are. No test yet feeds a hostile headline. |
| P.4 | The response format drifts between model versions. | IF.4.1, IF.4.2, TR.5 | 3 | 2 | 2 | 3 | 2.50 | 7.5 medium | The model name is a setting (`TICKERCASE_NARRATIVE_MODEL`). Each prompt has a version stored with its output, and stored narratives are rechecked under the current rules. | No scheduled run against a new model version. |
| P.5 | A dependency is abandoned or changes: the unofficial market endpoints (R.2), the page framework, the AI client library. | X.C.2.2, C.6.2, TR.1, TR.8 | 3 | 3 | 3 | 3 | 3.00 | 9.0 medium | Each source sits behind one adapter (C.2.1 to C.2.3); the offline test suite runs without any of them (NFR.7); the fallback of R.2. | Versions are not pinned to exact releases. |
| P.6 | Data leaves the machine by a path nobody intended. | IF.4, IF.2, NFR.4 | 2 | 2 | 1 | 2 | 1.75 | 3.5 low | What leaves: the claim text and the public figures of a case go to the AI service, only when the reader asks for a narrative or an event update; the contact address of the User-Agent goes to the SEC, as the SEC asks. The service key is read from the environment and never written to a case, a log or a page (NFR.4). Cases are stored on the local disk only. | The page does not list, before the call, what the request to the AI service will hold. |

## Other risks carried in the model

TR.1 to TR.9 were entered in the model in October with a probability and a consequence each. R.2 continues TR.1, R.4
continues TR.6, P.1 and P.4 continue TR.5. TR.2 (methods not calibrated) was answered in part by the backtest of v0.18.0;
TR.7 (the filing service blocks the client) is unchanged by parallel acquisition, because each source keeps its own
request spacing (ADR 0001).

## Cross-reference to the model

| Register | Model entity | Caused by | Mitigated by |
|---|---|---|---|
| R.1 | Risk R.1 | IF.4.2, X.C.4 | C.4.3, SR.1.4.3 |
| R.2 | Risk R.2 | IF.19, X.C.2.2 | C.2.4 HTTP Fetcher and Snapshot Store, C.2.5 Source Guard, NFR.2, test case VT.2 |
| R.3 | Risk R.3 | IO.53, IF.4.2 | C.4.3 |
| R.4 | Risk R.4 | IF.1.2 | NFR.5, SR.1.6.5, C.6.2 |
| R.5 | Risk R.5 | IO.48 to IO.51 | Decision D.1, NFR.1, C.2 |
