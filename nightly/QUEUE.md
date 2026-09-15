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
| 7d | 7 | 1m33s | 11m43s | 23m31s | 24m50s |
| 30d | 22 | 2m00s | 4m42s | 20m42s | 24m50s |
| all-time | 22 | 2m00s | 4m42s | 20m42s | 24m50s |

Completed: 22/23. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 7 | 2m03s | 5m14s | 7m59s | 8m17s |
| 30d | 20 | 2m49s | 5m12s | 7m52s | 8m17s |
| all-time | 20 | 2m49s | 5m12s | 7m52s | 8m17s |

Completed: 20/21. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 32s | 52s | 1m02s | 1m03s |
| 30d | 20 | 1m03s | 15h15m15s | 20h02m35s | 20h17m15s |
| all-time | 20 | 1m03s | 15h15m15s | 20h02m35s | 20h17m15s |

Completed: 20/21. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 7 | 2m59s | 3h33m25s | 6h48m10s | 7h09m48s |
| 30d | 20 | 2m38s | 30m35s | 6h01m17s | 7h09m48s |
| all-time | 20 | 2m38s | 30m35s | 6h01m17s | 7h09m48s |

Completed: 20/23. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 23 |
| gemini | 21 |
| mistral | 21 |
| openai | 23 |

