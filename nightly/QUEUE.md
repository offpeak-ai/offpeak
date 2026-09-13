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
| 30d | 21 | 2m03s | 4m51s | 20m54s | 24m50s |
| all-time | 21 | 2m03s | 4m51s | 20m54s | 24m50s |

Completed: 21/21. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 7 | 2m29s | 5m18s | 7m59s | 8m17s |
| 30d | 18 | 2m57s | 5m24s | 7m55s | 8m17s |
| all-time | 18 | 2m57s | 5m24s | 7m55s | 8m17s |

Completed: 18/19. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 32s | 3m22s | 8m13s | 8m45s |
| 30d | 18 | 4m54s | 16h05m12s | 20h04m07s | 20h17m15s |
| all-time | 18 | 4m54s | 16h05m12s | 20h04m07s | 20h17m15s |

Completed: 18/19. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 7 | 2m47s | 3h33m25s | 6h48m10s | 7h09m48s |
| 30d | 18 | 2m35s | 39m09s | 6h08m30s | 7h09m48s |
| all-time | 18 | 2m35s | 39m09s | 6h08m30s | 7h09m48s |

Completed: 18/21. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 21 |
| gemini | 19 |
| mistral | 19 |
| openai | 21 |

