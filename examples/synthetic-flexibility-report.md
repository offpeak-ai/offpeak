> ## ⚠️ SYNTHETIC DATA — NOT A CUSTOMER
>
> Every figure below is computed from a **generated** job log, written by
> `tools/make_synthetic_log.py` to demonstrate the report's shape. No real
> customer, no real fleet, no real spend. The prices are real; the jobs are
> not.

# Deadline flexibility report — v0

Generated 2026-08-23T03:34:38+00:00 by `tools/flexibility_report.py` against the `offpeak` price sheet dated **2026-08-21**. Every dollar below traces to a row on that sheet; a model that is not on it renders as an em dash and is counted as unpriced, never as free.

## What the window cost

| | |
|---|---|
| observed window | 26.0 days |
| jobs in the log | 1,464,404 requests across 38 log rows |
| **spend over the window** | **$4,877.00** |
| annualised | **$68,465.58** = $4,877.00 x 365/26.0 (14.0x) |

The annualised figure is a **projection of the observed window**, not a forecast: it assumes the window is representative, and the multiplier is printed so a reader who disagrees can redo it in one step.

**8 of 38 log rows are unpriced** — `deepseek-chat`, `text-embedding-3-large` are not on the bundled sheet. They are excluded from every total above and counted here, rather than silently valued at zero: a price nobody published is not a price of nothing, and a fleet is not smaller because this sheet is missing a row. Register a rate with `offpeak.prices.register_price()` and re-run to fold them in. **Every total in this report is therefore a floor.**

## The classification rule

A job is **deferrable** when its own metadata says it can
wait longer than the venue needs. Three classes, decided per job, from `submitted_at` and
`required_by` only — never from the job's name:

| class | test | counted as deferrable |
|---|---|---|
| `interactive` | `required_by` is absent, `none`, or `interactive` — somebody is waiting | no |
| `deferrable` | slack of **24h or more** — the window every batch tier publishes | yes |
| `marginal` | slack is positive but **under 24h** | no |

`marginal` is deliberately excluded from the headline. A batch usually lands far
inside its window — measured at 85s and 2m26s on a 24h window — but *usually* is
not an SLA, and a deadline shorter than the window rests on a fallback that pays
list. Counting that as captured would be selling a number the tier does not
guarantee.

## Deferrable share, by job class

| job class | requests | tokens (in / out) | spend | deferrable spend | share | class | unpriced |
|---|---|---|---|---|---|---|---|
| release evals | 16,800 | 52,080,000 / 7,056,000 `EST` | $174.72 | $174.72 | 100.0% | deferrable | — |
| embedding backfill | 720,000 | 368,640,000 / 0 `EST` | — | — | — | deferrable | 4 |
| corpus summarisation | 140,000 | 585,600,000 / 40,560,000 `EST` | $102.96 | $102.96 | 100.0% | deferrable | 4 |
| weekly report generation | 4 | 21,600,000 / 2,760,000 | $35.40 | $35.40 | 100.0% | deferrable | — |
| pre-merge checks | 13,600 | 28,560,000 / 2,448,000 `EST` | $40.80 | $0.00 | 0.0% | marginal | — |
| interactive product surface | 574,000 | 1,090,600,000 / 195,160,000 `EST` | $4,523.12 | $0.00 | 0.0% | interactive | — |
| **total** | **1,464,404** | | **$4,877.00** | **$313.08** | **6.4%** | | **8** |

`EST` marks a row whose token counts were inferred from a request count and an average rather than measured. The prices are exact; the tokens they multiply are not, and the dollars inherit that.

## What the wait is worth

Per venue, at that venue's **own** published rule. There is no flat 50% in this table, because there is no flat 50% in the market.

| venue | rule | deferrable spend | already captured | **incremental** | source |
|---|---|---|---|---|---|
| anthropic | 50% off, any model on Message Batches | $210.12 | $0.00 | **$105.06** | platform.claude.com/docs/en/about-claude/pricing |
| deepseek | **clock-priced** — no batch tier | — | $0.00 | **—** | api-docs.deepseek.com/quick_start/pricing |
| openai | 50% off, any model on the Batch API | $102.96 | $34.32 | **$34.32** | developers.openai.com/api/docs/pricing |
| **total** | | **$313.08** | **$34.32** | **$139.38** | |

**Incremental is the number that matters.** $34.32 of this window's saving is already being captured on tiers this fleet already uses. That is subtracted rather than counted twice: telling somebody they could save half on work they already batch is how a report gets thrown away. The incremental column is what deferring the *remaining* deferrable work would add, on top of what is already being captured.

Annualised on the same 14.0x multiplier as the headline: **$1,956.68 a year**, on a total annualised spend of $68,465.58 — and both are floors while any row is unpriced.

**Not priced here: deepseek.** These venues sell no batch tier. Their discount is a wall-clock window, so the saving is realised by moving *when* a job runs rather than *which tier* it runs on — a different instrument, with a different operational cost, and a promotional rate behind it in at least one case. Putting a dollar on it here would be an estimate, and there are none of those in the dollar layer.

## The arithmetic

Rates are USD per 1M tokens from the `offpeak` sheet dated **2026-08-21**, one line per log row so every total above can be re-derived by hand.

| job class | model | venue · tier | in x rate | out x rate | = spend | class | incremental |
|---|---|---|---|---|---|---|---|
| release evals | `claude-sonnet-5` | anthropic · standard | 13,020,000 x $2.0000 `EST` | 1,764,000 x $10.0000 | $43.68 | deferrable | $21.84 |
| release evals | `claude-sonnet-5` | anthropic · standard | 13,020,000 x $2.0000 `EST` | 1,764,000 x $10.0000 | $43.68 | deferrable | $21.84 |
| release evals | `claude-sonnet-5` | anthropic · standard | 13,020,000 x $2.0000 `EST` | 1,764,000 x $10.0000 | $43.68 | deferrable | $21.84 |
| release evals | `claude-sonnet-5` | anthropic · standard | 13,020,000 x $2.0000 `EST` | 1,764,000 x $10.0000 | $43.68 | deferrable | $21.84 |
| embedding backfill | `text-embedding-3-large` | openai · batch | — (off sheet) `EST` | — (off sheet) | — | deferrable | $0.00 |
| embedding backfill | `text-embedding-3-large` | openai · batch | — (off sheet) `EST` | — (off sheet) | — | deferrable | $0.00 |
| embedding backfill | `text-embedding-3-large` | openai · batch | — (off sheet) `EST` | — (off sheet) | — | deferrable | $0.00 |
| embedding backfill | `text-embedding-3-large` | openai · batch | — (off sheet) `EST` | — (off sheet) | — | deferrable | $0.00 |
| corpus summarisation | `gpt-5.6-luna` | openai · standard | 124,800,000 x $0.2000 `EST` | 7,800,000 x $1.2000 | $34.32 | deferrable | $17.16 |
| corpus summarisation | `gpt-5.6-luna` | openai · batch | 124,800,000 x $0.2000 `EST` | 7,800,000 x $1.2000 | $17.16 | deferrable | $0.00 |
| corpus summarisation | `gpt-5.6-luna` | openai · standard | 124,800,000 x $0.2000 `EST` | 7,800,000 x $1.2000 | $34.32 | deferrable | $17.16 |
| corpus summarisation | `gpt-5.6-luna` | openai · batch | 124,800,000 x $0.2000 `EST` | 7,800,000 x $1.2000 | $17.16 | deferrable | $0.00 |
| weekly report generation | `claude-haiku-4-5` | anthropic · standard | 5,400,000 x $1.0000 | 690,000 x $5.0000 | $8.85 | deferrable | $4.42 |
| weekly report generation | `claude-haiku-4-5` | anthropic · standard | 5,400,000 x $1.0000 | 690,000 x $5.0000 | $8.85 | deferrable | $4.42 |
| weekly report generation | `claude-haiku-4-5` | anthropic · standard | 5,400,000 x $1.0000 | 690,000 x $5.0000 | $8.85 | deferrable | $4.42 |
| weekly report generation | `claude-haiku-4-5` | anthropic · standard | 5,400,000 x $1.0000 | 690,000 x $5.0000 | $8.85 | deferrable | $4.42 |
| corpus summarisation | `deepseek-chat` | deepseek · standard | — (off sheet) `EST` | — (off sheet) | — | deferrable | — |
| corpus summarisation | `deepseek-chat` | deepseek · standard | — (off sheet) `EST` | — (off sheet) | — | deferrable | — |
| corpus summarisation | `deepseek-chat` | deepseek · standard | — (off sheet) `EST` | — (off sheet) | — | deferrable | — |
| corpus summarisation | `deepseek-chat` | deepseek · standard | — (off sheet) `EST` | — (off sheet) | — | deferrable | — |
| pre-merge checks | `claude-haiku-4-5` | anthropic · standard | 7,140,000 x $1.0000 `EST` | 612,000 x $5.0000 | $10.20 | marginal | $0.00 |
| pre-merge checks | `claude-haiku-4-5` | anthropic · standard | 7,140,000 x $1.0000 `EST` | 612,000 x $5.0000 | $10.20 | marginal | $0.00 |
| pre-merge checks | `claude-haiku-4-5` | anthropic · standard | 7,140,000 x $1.0000 `EST` | 612,000 x $5.0000 | $10.20 | marginal | $0.00 |
| pre-merge checks | `claude-haiku-4-5` | anthropic · standard | 7,140,000 x $1.0000 `EST` | 612,000 x $5.0000 | $10.20 | marginal | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |
| interactive product surface | `gpt-5.6-terra` | openai · standard | 77,900,000 x $2.0000 `EST` | 13,940,000 x $12.0000 | $323.08 | interactive | $0.00 |

## What was inferred, and what the log did not say

**Inferred** — read from the log generously, and written down:

- `release evals` / `claude-sonnet-5`: tokens in = 3,100 avg x 4,200 requests *(x4 rows)*
- `release evals` / `claude-sonnet-5`: tokens out = 420 avg x 4,200 requests *(x4 rows)*
- `embedding backfill` / `text-embedding-3-large`: tokens in = 512 avg x 180,000 requests *(x4 rows)*
- `embedding backfill` / `text-embedding-3-large`: tokens out = 0 avg x 180,000 requests *(x4 rows)*
- `embedding backfill` / `text-embedding-3-large`: venue = openai (from the model name) *(x4 rows)*
- `corpus summarisation` / `gpt-5.6-luna`: tokens in = 4,800 avg x 26,000 requests *(x4 rows)*
- `corpus summarisation` / `gpt-5.6-luna`: tokens out = 300 avg x 26,000 requests *(x4 rows)*
- `corpus summarisation` / `gpt-5.6-luna`: venue = openai (from the model name) *(x4 rows)*
- `corpus summarisation` / `deepseek-chat`: tokens in = 2,400 avg x 9,000 requests *(x4 rows)*
- `corpus summarisation` / `deepseek-chat`: tokens out = 260 avg x 9,000 requests *(x4 rows)*
- `pre-merge checks` / `claude-haiku-4-5`: tokens in = 2,100 avg x 3,400 requests *(x4 rows)*
- `pre-merge checks` / `claude-haiku-4-5`: tokens out = 180 avg x 3,400 requests *(x4 rows)*
- `interactive product surface` / `gpt-5.6-terra`: tokens in = 1,900 avg x 41,000 requests *(x14 rows)*
- `interactive product surface` / `gpt-5.6-terra`: tokens out = 340 avg x 41,000 requests *(x14 rows)*

## Caveats — prices in here with an expiry

Some of the rates above are promotional or introductory. A promotional rate is a real price today and a wrong one later, and an annualised figure built on one is the optimistic reading by construction.

| subject | good through | what changes | source |
|---|---|---|---|
| gpt-5.6-sol promotional list price | 2026-11-21 | Post-promo list is $5.00 / $30.00 per 1M. Both the standard and batch legs are defined off list, so the dollars move and the ratio does not. | developers.openai.com/api/docs/pricing |
| Gemini 3.7 Flash introductory pricing | 2026-12-31 | Introductory rates. Anything priced off them — including the batch half — steps up when they lapse, so a Google-heavy annualised figure is the optimistic one. | ai.google.dev/pricing |
| Qwen night-hours promotion | see Alibaba Cloud Model Studio | Qwen's off-peak saving is promotional, not a standing tier. It is the most perishable number a report like this can lean on, which is part of why clock-priced venues are not given a dollar figure here. | alibabacloud.com/help/en/model-studio/models |

**No carbon in v0.** The grid side of the claim is observation on the Spread Board and does not belong in a document that quotes somebody a number. It is not here partially, and not as a placeholder column.
