# Offpeak queue latency — observed, not modelled

How long a batch tier actually takes to land, measured by submitting a couple of tiny jobs and watching the clock. This table spends real money at real venues and is therefore **not** the Spread Board: that one marks open grid data and spends nothing. Same separation, and the same reason, as `SETTLED.md`.

`offpeak` currently abandons a slow batch on a fixed risk buffer — 15% of the window, clamped to 1–10 minutes — because nobody had the number. These are the observations that would replace it.

**Every row is one submission.** There is no percentile here and no fitted curve: a handful of observations does not have a distribution, and a model over them would read as knowledge rather than as the few numbers it came from. A row marked *open* is a batch still running when the probe stopped watching — it stays on the venue's queue and the row is rewritten with the real outcome once a later run resolves it from the stored handle. A row marked *expired* or *overran_window* is the venue missing its own declared window — the failure mode this table exists to catch. Early rows marked *censored* predate resolution: those batches were cancelled at 30 minutes and are lower bounds, never completion times.

Written by `tools/queue_probe.py`, never by hand.

| session | venue | model | declared | jobs | elapsed | % of window | status | paid |
|---|---|---|---|---|---|---|---|---|
| 2026-08-23 | anthropic | claude-haiku-4-5 | 24h | 2 | — | — | skipped — no ANTHROPIC_API_KEY in the environment | — |
| 2026-08-23 | openai | gpt-5.6-luna | 24h | 2 | — | — | skipped — no OPENAI_API_KEY in the environment | — |
| 2026-08-24 | anthropic | claude-haiku-4-5 | 24h | 2 | 2m02s | 0.142% | completed | $0.0000360 |
| 2026-08-24 | openai | gpt-5.6-luna | 24h | 2 | 1m34s | 0.110% | completed | $0.00000780 |
| 2026-08-25 | anthropic | claude-haiku-4-5 | 24h | 2 | 4m51s | 0.337% | completed | $0.0000360 |
| 2026-08-25 | openai | gpt-5.6-luna | 24h | 2 | — | — | censored | — |
| 2026-08-26 | anthropic | claude-haiku-4-5 | 24h | 2 | 2m35s | 0.179% | completed | $0.0000360 |
| 2026-08-26 | openai | gpt-5.6-luna | 24h | 2 | — | — | censored | — |
| 2026-08-26 | gemini | gemini-3.7-flash | 24h | 2 | 5m06s | 0.355% | completed | $0.000613 |
| 2026-08-26 | mistral | mistral-small-latest | 24h | 2 | — | — | censored | — |
| 2026-08-27 | anthropic | claude-haiku-4-5 | 24h | 2 | 2m50s | 0.197% | completed | $0.0000360 |
| 2026-08-27 | openai | gpt-5.6-luna | 24h | 2 | 1m32s | 0.107% | completed | $0.00000780 |
| 2026-08-27 | gemini | gemini-3.7-flash | 24h | 2 | 3m20s | 0.232% | completed | $0.000527 |
| 2026-08-27 | mistral | mistral-small-latest | 24h | 2 | — | — | open | — |
