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
| 7d | 8 | 1m40s | 3m52s | 5m00s | 5m08s |
| 30d | 13 | 2m18s | 4m33s | 5m06s | 5m08s |
| all-time | 13 | 2m18s | 4m33s | 5m06s | 5m08s |

Completed: 13/13. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 2m57s | 5m03s | 5m59s | 6m06s |
| 30d | 11 | 3m04s | 5m06s | 6m00s | 6m06s |
| all-time | 11 | 3m04s | 5m06s | 6m00s | 6m06s |

Completed: 11/11. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 9h12m22s | 19h23m11s | 20h11m51s | 20h17m15s |
| 30d | 10 | 8h04m46s | 19h07m44s | 20h10m18s | 20h17m15s |
| all-time | 10 | 8h04m46s | 19h07m44s | 20h10m18s | 20h17m15s |

Completed: 10/11. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 2m34s | 20m07s | 25m40s | 26m17s |
| 30d | 11 | 2m34s | 17m28s | 25m24s | 26m17s |
| all-time | 11 | 2m34s | 17m28s | 25m24s | 26m17s |

Completed: 11/13. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 13 |
| gemini | 11 |
| mistral | 11 |
| openai | 13 |

