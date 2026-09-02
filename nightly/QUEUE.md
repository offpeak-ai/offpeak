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
| 7d | 8 | 2m02s | 2m59s | 3m17s | 3m19s |
| 30d | 10 | 2m10s | 3m28s | 4m43s | 4m51s |
| all-time | 10 | 2m10s | 3m28s | 4m43s | 4m51s |

Completed: 10/10. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 3m12s | 5m24s | 6m02s | 6m06s |
| 30d | 8 | 3m12s | 5m24s | 6m02s | 6m06s |
| all-time | 8 | 3m12s | 5m24s | 6m02s | 6m06s |

Completed: 8/8. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 6 | 11h39m54s | 19h38m38s | 20h13m23s | 20h17m15s |
| 30d | 6 | 11h39m54s | 19h38m38s | 20h13m23s | 20h17m15s |
| all-time | 6 | 11h39m54s | 19h38m38s | 20h13m23s | 20h17m15s |

Completed: 6/8. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 7 | 2m34s | 13m06s | 24m58s | 26m17s |
| 30d | 8 | 2m34s | 10m54s | 24m45s | 26m17s |
| all-time | 8 | 2m34s | 10m54s | 24m45s | 26m17s |

Completed: 8/10. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 10 |
| gemini | 8 |
| mistral | 8 |
| openai | 10 |

