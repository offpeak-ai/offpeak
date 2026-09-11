# Roadmap

What exists, what does not, and what is being built. This page is meant to be
checkable — if something here is not true yet, it says so.

## What exists today

- **`run(jobs, deadline=...)`** across the OpenAI and Anthropic batch tiers,
  with a sync fallback that protects the deadline.
- **`submit()` / `collect()` and the `Ticket`** — the same run split across
  processes: submit from a laptop or a CI step, save the ticket, collect from
  a cron the next morning. Nothing is orphaned at the provider when the
  caller dies.
- **`quote(jobs, deadline=...)`** — pre-trade pricing with no API calls.
- **Receipts and settlements** — list, paid, captured spread, and what a
  fallback left on the table, as arithmetic against published price sheets.
- **A `Venue` interface** — the extension point. A venue is anywhere deferred
  work can run.
- **[The Spread Board](spread-board.md)** — GB power and carbon, marked daily.

## What does not exist yet

Stated plainly, because a roadmap that reads like a feature list is a
misleading one:

- **No queue-latency forecasting.** Deadline risk is a fixed buffer, not a
  prediction. `offpeak` does not know how long a given venue's queue is; it
  watches the clock and falls back.
- **No cross-venue portfolio placement.** Jobs route to the first venue that
  supports the model, not to the cheapest or fastest across a portfolio.
- **No carbon-aware scheduling.** The Spread Board observes the grid; the
  scheduler does not read it.
- **A third venue settles: Google Gemini.** 5 jobs, batch tier, **50.0%
  captured, zero fallbacks**
  ([receipt](https://github.com/offpeak-ai/offpeak/blob/main/receipts/2026-08-24-gemini-1.json)).
  Opt-in, like the rest.
- **Two more venues are written and cannot batch.** Groq answers
  `403 not_available_for_plan`; Mistral answers `402 ... enable billing via the
  console`. Both drivers are exercised end to end and both have a receipt
  recording the attempt and what the sync fallback cost. Gemini was gated the
  same way until billing was enabled, so these are plan problems rather than
  code ones.
- **Two venues are written and have not run.** [DeepSeek](venues-deepseek.md)
  is the first venue that is not a batch tier: it has no batch API and prices
  by the clock — half price outside 01:00–04:00 and 06:00–10:00 UTC on
  weekdays — so the driver *holds* jobs until the boundary instead of
  uploading them. [Qwen](venues-qwen.md) on Alibaba Model Studio is an
  OpenAI-shaped batch tier with a region and a 24h–336h window. Neither has
  a receipt yet; both are opt-in, and the first sub-cent live run is what will
  verify them.
- **No venues beyond those.** Spot capacity and off-peak windows on your own
  GPUs are interface-shaped but unwritten. A Groq
  batch driver exists in the tree, opt-in, excluded from the `all` extra and
  not in `default_venues()`. Its routing, request dialect, price rows and sync
  fallback are verified against the live API; its **batch tier is not**, and
  cannot be from here — Groq answers `403 not_available_for_plan` to the entire
  Batch API on an unentitled key. The receipt for that attempt is
  [`2026-08-23-groq-1.json`](https://github.com/offpeak-ai/offpeak/blob/main/receipts/2026-08-23-groq-1.json):
  24 real jobs, all of them through the sync fallback at list price, nothing
  captured.

## The hosted desk

A hosted desk that does the forecasting, cross-venue portfolio scheduling, and
SLA insurance at fleet scale — with payloads never leaving your perimeter — is
being built by the same team.

The seam is the one already in the library: pass `desk=` alongside the venues
you already use, so moving from local scheduling to hosted scheduling is a
keyword argument rather than a rewrite.

!!! note "Private beta"
    Keys are issued by hand — ask. The SDK and the deadline spec stay open,
    Apache-2.0, either way.

## The spec

Deadline semantics are versioned separately in [SPEC.md](spec.md), so a second
implementation can be written against them. Spec changes start as issues.
