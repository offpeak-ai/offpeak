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
| 7d | 8 | 2m10s | 3m26s | 4m43s | 4m51s |
| 30d | 8 | 2m10s | 3m26s | 4m43s | 4m51s |
| all-time | 8 | 2m10s | 3m26s | 4m43s | 4m51s |

Completed: 8/8. Expired: 0. Overran window: 0. Failed: 0.

## gemini (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 6 | 3m05s | 4m51s | 5m05s | 5m06s |
| 30d | 6 | 3m05s | 4m51s | 5m05s | 5m06s |
| all-time | 6 | 3m05s | 4m51s | 5m05s | 5m06s |

Completed: 6/6. Expired: 0. Overran window: 0. Failed: 0.

## mistral (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 4 | 4h24m10s | 15h50m52s | 18h41m06s | 19h00m01s |
| 30d | 4 | 4h24m10s | 15h50m52s | 18h41m06s | 19h00m01s |
| all-time | 4 | 4h24m10s | 15h50m52s | 18h41m06s | 19h00m01s |

Completed: 4/6. Expired: 0. Overran window: 0. Failed: 0.

## openai (24h)

| range | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 7d | 6 | 2m34s | 14m26s | 25m06s | 26m17s |
| 30d | 6 | 2m34s | 14m26s | 25m06s | 26m17s |
| all-time | 6 | 2m34s | 14m26s | 25m06s | 26m17s |

Completed: 6/8. Expired: 0. Overran window: 0. Failed: 0.

## Days of continuous accrual

| venue | days |
|---|---|
| anthropic | 8 |
| gemini | 6 |
| mistral | 6 |
| openai | 8 |

