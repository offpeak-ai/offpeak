# Roadmap

What exists, what does not, and what is being built. This page is meant to be
checkable — if something here is not true yet, it says so.

## What exists today

- **`run(jobs, deadline=...)`** across the OpenAI and Anthropic batch tiers,
  with a sync fallback that protects the deadline.
- **`quote(jobs, deadline=...)`** — pre-trade pricing with no API calls.
- **Receipts and settlements** — list, paid, captured spread, and what a
  fallback left on the table, as arithmetic against published price sheets.
- **A `Venue` interface** — the extension point. A venue is anywhere deferred
  work can run.
- **[The night board](night-board.md)** — GB power and carbon, marked nightly.

## What does not exist yet

Stated plainly, because a roadmap that reads like a feature list is a
misleading one:

- **No queue-latency forecasting.** Deadline risk is a fixed buffer, not a
  prediction. `offpeak` does not know how long a given venue's queue is; it
  watches the clock and falls back.
- **No cross-venue portfolio placement.** Jobs route to the first venue that
  supports the model, not to the cheapest or fastest across a portfolio.
- **No carbon-aware scheduling.** The night board observes the grid; the
  scheduler does not read it.
- **No venues beyond the two batch tiers.** Google batch, spot capacity, and
  off-peak windows on your own GPUs are interface-shaped but unwritten.

## The hosted desk

A hosted desk that does the forecasting, cross-venue portfolio scheduling, and
SLA insurance at fleet scale — with payloads never leaving your perimeter — is
being built by the same team.

The intended seam is the one already in the library: a desk would be selected
per run, alongside the venues you already pass, so that moving from local
scheduling to hosted scheduling is a keyword argument rather than a rewrite.

!!! note "Not implemented"
    That parameter does not exist in the public API today, and nothing in this
    release accepts it. It is described here so the shape of the plan is
    legible — not as something you can call. The SDK and the deadline spec stay
    open, Apache-2.0, either way.

## The spec

Deadline semantics are versioned separately in [SPEC.md](spec.md), so a second
implementation can be written against them. Spec changes start as issues.
