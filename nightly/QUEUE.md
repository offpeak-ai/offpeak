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
| 7d | 8 | 1m46s | 9m32s | 23m18s | 24m50s |
| 30d | 20 | 2m10s | 4m53s | 21m05s | 24m50s |
| all-time | 20 | 2m10s | 4m53s | 21m05s | 24m50s |

Completed: 20/20. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 2m14s | 4m48s | 7m56s | 8m17s |
| 30d | 18 | 2m57s | 5m24s | 7m55s | 8m17s |
| all-time | 18 | 2m57s | 5m24s | 7m55s | 8m17s |

Completed: 18/18. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 40s | 2h24m08s | 7h08m27s | 7h40m02s |
| 30d | 17 | 8m45s | 16h30m11s | 20h04m54s | 20h17m15s |
| all-time | 17 | 8m45s | 16h30m11s | 20h04m54s | 20h17m15s |

Completed: 17/18. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 6m18s | 2h57m21s | 6h44m33s | 7h09m48s |
| 30d | 18 | 2m35s | 39m09s | 6h08m30s | 7h09m48s |
| all-time | 18 | 2m35s | 39m09s | 6h08m30s | 7h09m48s |

Completed: 18/20. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 20 |
| gemini | 18 |
| mistral | 18 |
| openai | 20 |

