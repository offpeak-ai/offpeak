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
| 7d | 8 | 1m56s | 3m37s | 4m59s | 5m08s |
| 30d | 17 | 2m18s | 3m56s | 5m05s | 5m08s |
| all-time | 17 | 2m18s | 3m56s | 5m05s | 5m08s |

Completed: 17/17. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 3m08s | 4m49s | 7m56s | 8m17s |
| 30d | 15 | 3m12s | 5m42s | 7m59s | 8m17s |
| all-time | 15 | 3m12s | 5m42s | 7m59s | 8m17s |

Completed: 15/15. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 4m54s | 8h35m26s | 10h31m45s | 10h44m41s |
| 30d | 14 | 18m14s | 17h45m06s | 20h07m13s | 20h17m15s |
| all-time | 14 | 18m14s | 17h45m06s | 20h07m13s | 20h17m15s |

Completed: 14/15. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 3m33s | 32m58s | 1h05m33s | 1h09m10s |
| 30d | 15 | 2m34s | 22m45s | 1h03m10s | 1h09m10s |
| all-time | 15 | 2m34s | 22m45s | 1h03m10s | 1h09m10s |

Completed: 15/17. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 17 |
| gemini | 15 |
| mistral | 15 |
| openai | 17 |

