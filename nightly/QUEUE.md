# Offpeak queue latency — summary

How long a batch tier actually takes to land, measured by submitting a couple
of tiny jobs and watching the clock. This spends real money at real venues and
is therefore **not** the Spread Board: that one marks open grid data and
spends nothing. Same separation, and the same reason, as `SETTLED.md`.

Every number below is a percentile over completed sessions — not a single
row. A session still running when a probe stopped watching is *open*: it
stays on the desk's worklist and is resolved from its stored handle once a
later run checks again, so it is excluded from these numbers until it has an
outcome. A session marked *expired* or *overran_window* is the venue missing
its own declared window — the failure mode this table exists to catch. A
session marked *censored* predates resolution: it was cancelled after a fixed
wait with no completion in sight, so its true turnaround is only known to be
at least that wait — it contributes to the attempt count below but not to any
percentile, since it has no elapsed time to report.

The rows this is built from are private, kept in the desk's own repository.
Private tail since 2026-08-28; days before that were imported from the
public series this table replaces.

Written by `tools/queue_summary.py`, never by hand.


## anthropic (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 1m56s | 3m52s | 5m00s | 5m08s |
| 30d | 16 | 2m10s | 4m05s | 5m05s | 5m08s |
| all-time | 16 | 2m10s | 4m05s | 5m05s | 5m08s |

Completed: 16/16. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 3m08s | 4m09s | 5m54s | 6m06s |
| 30d | 14 | 3m08s | 4m57s | 5m58s | 6m06s |
| all-time | 14 | 3m08s | 4m57s | 5m58s | 6m06s |

Completed: 14/14. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 13m12s | 11h58m22s | 14h33m05s | 14h50m17s |
| 30d | 13 | 18m50s | 18h10m04s | 20h07m59s | 20h17m15s |
| all-time | 13 | 18m50s | 18h10m04s | 20h07m59s | 20h17m15s |

Completed: 13/14. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 2m40s | 12m07s | 16m55s | 17m28s |
| 30d | 14 | 2m34s | 15m10s | 25m08s | 26m17s |
| all-time | 14 | 2m34s | 15m10s | 25m08s | 26m17s |

Completed: 14/16. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 16 |
| gemini | 14 |
| mistral | 14 |
| openai | 16 |

