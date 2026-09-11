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
| 7d | 8 | 1m46s | 2m40s | 2m56s | 2m58s |
| 30d | 19 | 2m03s | 3m37s | 5m05s | 5m08s |
| all-time | 19 | 2m03s | 3m37s | 5m05s | 5m08s |

Completed: 19/19. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 2m39s | 4m48s | 7m56s | 8m17s |
| 30d | 17 | 3m04s | 5m30s | 7m56s | 8m17s |
| all-time | 17 | 3m04s | 5m30s | 7m56s | 8m17s |

Completed: 17/17. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 47s | 2h24m08s | 7h08m27s | 7h40m02s |
| 30d | 16 | 13m12s | 16h55m09s | 20h05m40s | 20h17m15s |
| all-time | 16 | 13m12s | 16h55m09s | 20h05m40s | 20h17m15s |

Completed: 16/17. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 6m18s | 2h57m21s | 6h44m33s | 7h09m48s |
| 30d | 17 | 2m34s | 43m26s | 6h12m06s | 7h09m48s |
| all-time | 17 | 2m34s | 43m26s | 6h12m06s | 7h09m48s |

Completed: 17/19. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 19 |
| gemini | 17 |
| mistral | 17 |
| openai | 19 |

