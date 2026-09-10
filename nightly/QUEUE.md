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
| 7d | 8 | 2m08s | 3m37s | 4m59s | 5m08s |
| 30d | 18 | 2m10s | 3m47s | 5m05s | 5m08s |
| all-time | 18 | 2m10s | 3m47s | 5m05s | 5m08s |

Completed: 18/18. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 3m01s | 4m49s | 7m56s | 8m17s |
| 30d | 16 | 3m08s | 5m36s | 7m57s | 8m17s |
| all-time | 16 | 3m08s | 5m36s | 7m57s | 8m17s |

Completed: 16/16. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 1m03s | 2h30m22s | 7h09m04s | 7h40m02s |
| 30d | 15 | 17m39s | 17h20m07s | 20h06m26s | 20h17m15s |
| all-time | 15 | 17m39s | 17h20m07s | 20h06m26s | 20h17m15s |

Completed: 15/16. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 7 | 2m47s | 38m09s | 1h06m04s | 1h09m10s |
| 30d | 15 | 2m34s | 22m45s | 1h03m10s | 1h09m10s |
| all-time | 15 | 2m34s | 22m45s | 1h03m10s | 1h09m10s |

Completed: 15/18. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 18 |
| gemini | 16 |
| mistral | 16 |
| openai | 18 |

