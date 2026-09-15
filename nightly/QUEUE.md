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
| 7d | 8 | 1m46s | 19m46s | 24m20s | 24m50s |
| 30d | 23 | 2m03s | 5m04s | 23m15s | 24m50s |
| all-time | 23 | 2m03s | 5m04s | 23m15s | 24m50s |

Completed: 23/23. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 8 | 2m16s | 30m32s | 1h17m15s | 1h22m26s |
| 30d | 21 | 2m50s | 6m06s | 1h07m36s | 1h22m26s |
| all-time | 21 | 2m50s | 6m06s | 1h07m36s | 1h22m26s |

Completed: 21/21. Expired: 0. Overran window: 0. Failed: 0.

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
| 7d | 8 | 3m11s | 2h57m21s | 6h44m33s | 7h09m48s |
| 30d | 21 | 2m41s | 26m17s | 5h57m40s | 7h09m48s |
| all-time | 21 | 2m41s | 26m17s | 5h57m40s | 7h09m48s |

Completed: 21/23. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 23 |
| gemini | 21 |
| mistral | 21 |
| openai | 23 |

