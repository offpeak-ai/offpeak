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
| 7d | 8 | 2m26s | 3m47s | 4m45s | 4m51s |
| 30d | 9 | 2m18s | 3m37s | 4m44s | 4m51s |
| all-time | 9 | 2m18s | 3m37s | 4m44s | 4m51s |

Completed: 9/9. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 7 | 3m20s | 5m30s | 6m02s | 6m06s |
| 30d | 7 | 3m20s | 5m30s | 6m02s | 6m06s |
| all-time | 7 | 3m20s | 5m30s | 6m02s | 6m06s |

Completed: 7/7. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 6 | 11h39m54s | 19h38m38s | 20h13m23s | 20h17m15s |
| 30d | 6 | 11h39m54s | 19h38m38s | 20h13m23s | 20h17m15s |
| all-time | 6 | 11h39m54s | 19h38m38s | 20h13m23s | 20h17m15s |

Completed: 6/7. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 6 | 2m34s | 14m26s | 25m06s | 26m17s |
| 30d | 7 | 2m34s | 12m04s | 24m52s | 26m17s |
| all-time | 7 | 2m34s | 12m04s | 24m52s | 26m17s |

Completed: 7/9. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 9 |
| gemini | 7 |
| mistral | 7 |
| openai | 9 |

