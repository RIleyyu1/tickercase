# Backtest of the methods that can be replayed

When a method said "30 %", did the thing happen about 30 % of the time? This note answers that for the two methods
that can be replayed on past prices. Run of 2026-11-17, versions 0.17.0 and 0.18.0, `python scripts/backtest_methods.py`.

## What was tested

- **Stocks:** the 55 largest US companies of 2005 that still trade under the same symbol.
- **Cases:** the last trading day of every quarter from 2006-03-31 to 2025-09-30; horizons of 1, 2 and 3 years; rises of
  10, 25, 50 and 100 %. 49,332 cases.
- **M2, the stock's own volatility:** `oracle.p_end_above` and `oracle.p_touch`, with the volatility of the five years
  before the day and the 10-year Treasury yield of the day, as the product computes it.
- **M4, the stock's own history:** `price_base_rate` and `price_touch_hits` on the month-end closes up to the day, only
  where the history holds at least four separate periods (the product's rule).
- **Not tested:** M1, option prices (past option chains are in no free source); M3, which is about the business.

## Result

| Method and question | Cases | Said, on average | Happened | Skill against a constant guess |
|---|---|---|---|---|
| M2, at or above on the last day | 49,332 | 19 % | 34 % | 0.09 |
| M2, reached on any day | 49,332 | 40 % | 48 % | 0.37 |
| M4, at or above on the last day | 48,868 | 39 % | 34 % | 0.19 |
| M4, reached on any day (daily closes) | 48,868 | 51 % | 48 % | 0.36 |
| M4, reached at a month-end (its own definition) | 48,868 | 51 % | 44 % | 0.33 |

Skill is 1 minus the Brier score divided by the Brier score of always saying the overall frequency; 0 is no better
than that guess, 1 is perfect.

### M2 says too little, and more so the longer the horizon

| When M2 said (reached on any day) | 5 % | 15 % | 25 % | 35 % | 45 % | 55 % | 65 % | 76 % | 83 % |
|---|---|---|---|---|---|---|---|---|---|
| it happened | 5 % | 19 % | 33 % | 44 % | 57 % | 68 % | 77 % | 85 % | 91 % |

| Horizon | M2 said (any day) | happened | M2 said (last day) | happened |
|---|---|---|---|---|
| 1 year | 32 % | 34 % | 15 % | 23 % |
| 2 years | 42 % | 50 % | 20 % | 35 % |
| 3 years | 48 % | 61 % | 23 % | 45 % |

The formula lets the price drift at the Treasury yield. Stocks rose faster than that over these twenty years, so the
gap grows with the horizon. For "on the last day" the method is close to useless at two and three years.

### M4 says a little too much

| When M4 said (reached on any day) | 4 % | 15 % | 25 % | 35 % | 45 % | 55 % | 65 % | 75 % | 85 % | 93 % |
|---|---|---|---|---|---|---|---|---|---|---|
| it happened (daily closes) | 4 % | 12 % | 17 % | 26 % | 39 % | 47 % | 63 % | 75 % | 86 % | 89 % |

It is close at the two ends and about 8 points high in the middle.

### Stocks that had already tripled

The stocks people ask about have usually risen a lot. For the 4,396 cases where the price had at least tripled in the
five years before:

| Method (reached on any day) | Said | Happened |
|---|---|---|
| M2 | 40 % | 51 % |
| M4 | 56 % | 51 % |

M4 is 5 points high here against 3 points for the other stocks: the selection effect is there, and it is small in this
set. M2 is 11 points low.

## The weighted index of v0.18.0

Since v0.18.0 the main number is a weighted index (M1 40 %, M2 40 %, M4 15 %, M3 5 %). It is replayed here with M2
standing in for M1, because past option prices are missing, and without M3: M2 at 84 %, M4 at 16 %.

| Index and question | Cases | Said, on average | Happened | Skill |
|---|---|---|---|---|
| Index, reached on any day | 49,332 | 42 % | 48 % | 0.38 |
| Index, at or above on the last day | 49,332 | 22 % | 34 % | 0.14 |

| When the index said (reached on any day) | 4 % | 15 % | 25 % | 35 % | 45 % | 55 % | 65 % | 75 % | 84 % |
|---|---|---|---|---|---|---|---|---|---|
| it happened | 4 % | 16 % | 29 % | 43 % | 54 % | 66 % | 76 % | 84 % | 91 % |

| When the index said (on the last day) | 4 % | 15 % | 25 % | 35 % | 44 % |
|---|---|---|---|---|---|
| it happened | 5 % | 21 % | 40 % | 56 % | 66 % |

These two tables are `src/tickercase/calibration.json` (`--export`), which the page reads for the line "when this index
said X, the claim came true about Y of the time". The index is still low, by 6 points for "on any day" and 12 for "on
the last day", because M2 carries most of it. The page states the checked figure beside the main number and does not
shift the main number.

**The weight of the history is a choice, not a fit.** Beside M2 alone, the share of M4 with the best Brier score for
"reached on any day" was 50 % (Brier 0.151 against 0.154 for the index as weighted). It is kept at 15 % because the
backtest holds survivors and no stock with a past like the ones M4 misjudges most; the gain from a larger share is small
and rests on the part of the evidence that is weakest.

## What this does and does not show

- It shows that M2, one of the two methods of the headline since v0.17.0, has been **low** by 8 to 13 points for
  "reached on any day" at two and three years, and that M4, moved to context in v0.17.0 as "optimistic", has been high by
  only 3 to 5 points. The note on M2 in the simple view ("it may be low") is supported strongly; the note on M4
  ("optimistic") is supported weakly.
- It does **not** show what option prices (M1) would have said.
- **Survivors only.** Companies of 2005 that failed or were bought are missing, so "happened" is higher here than it was
  for an investor of 2005. That flatters M4 and makes M2 look lower than it was.
- **One market period.** 2006 to 2025 holds one deep fall and a long rise. The gap of M2 is the size of the equity
  premium of that period, which the next twenty years need not repeat.
- **Few independent observations.** The cases overlap in time and the stocks move together: there are about seven
  separate three-year periods. The table has many rows and little independent evidence, so no interval is given.
- **No stock like NVDA.** "At least tripled" is the strongest past in this set; nothing in it rose tenfold in five
  years. What M4 is worth for such a stock is not tested here.

Data: `data/backtest/v2/cases.csv` and `summary.json` (not in the repository; rerun the script to rebuild them).
