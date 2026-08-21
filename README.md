# offpeak

**Deadline-priced inference.** Same model, same tokens, a different hour — for half the price.

[![CI](https://github.com/offpeak-ai/offpeak/actions/workflows/ci.yml/badge.svg)](https://github.com/offpeak-ai/offpeak/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/offpeak)](https://pypi.org/project/offpeak/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

OpenAI, Anthropic, and Google all sell batch inference at **50% off list price**. Almost nobody uses it, because no API lets work say it can wait: every token runs "now" by default, and the batch workflow — build a file, upload, poll, download, match results back up — is enough friction that urgency gets bought by accident.

`offpeak` gives your code one new argument.

```python
import offpeak

jobs = [offpeak.job("claude-haiku-4-5", f"Summarize:\n\n{doc}") for doc in docs]

results = offpeak.run(jobs, deadline="06:00")   # done by 6am, at batch prices

print(offpeak.receipt(results))
```

```
OFFPEAK SETTLEMENT ────────────────────────────
jobs      1,000 (1,000 ok, 2 sync fallback)
sla       1,000/1,000 met
venues    anthropic:batch 1,000
tokens    12,410,332 in · 3,104,551 out
list      $27.93
paid      $14.02
captured  $13.91 (49.8%)
prices    snapshot 2026-08 — override via offpeak.prices
───────────────────────────────────────────────
```

## What it does

- **Know the price before you spend it.** `quote(jobs, deadline=...)` prices a run against the published sheets with no API calls and no key — list versus batch, per venue, plus what the wait is worth.
- **One argument, not a workflow.** `run(jobs, deadline=...)` handles batching, submission, polling, collection, and result matching across providers.
- **Deadlines are guarded, not hoped for.** If a batch hasn't landed by the time the remaining window shrinks to a risk buffer, `offpeak` cancels and re-runs the stragglers synchronously at list price. You state the deadline; it gets met.
- **Every run settles a receipt.** List cost, paid cost, captured spread — arithmetic against public price sheets, not estimates.
- **Your keys, your perimeter.** `offpeak` talks directly to the providers with your own API keys. There is no proxy and no third party in the data path.
- **Zero-dependency core.** Provider SDKs load only via extras.

## Install

```bash
pip install "offpeak[all]"        # OpenAI + Anthropic venues
pip install "offpeak[anthropic]"  # or one provider
pip install "offpeak[openai]"
```

Venues use the standard environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), or pass a configured client: `OpenAIBatch(client=my_client)`.

## The free quote

What is the wait worth? Ask before you spend anything — `quote()` makes no API calls and needs no key.

```bash
python -m offpeak quote --model gpt-5.6-luna --input-tokens 800 --output-tokens 200 --jobs 5000
```

```
OFFPEAK QUOTE ─────────────────────────────────
jobs      5000 across 1 venue(s)
deadline  2026-08-21 21:11 PDT (24.0h out)
tokens    4,000,000 in · 1,000,000 out

  openai:batch      5000 job(s)  list $1.00  batch $0.50  save $0.50 (50.0%)

list      $1.00   (run now, synchronously)
batch     $0.50   (run by the deadline)
save      $0.50 (50.0%)
basis     input explicit; output explicit
prices    snapshot 2026-08 — estimate only, not a bill
───────────────────────────────────────────────
```

From Python, `offpeak.quote(jobs, deadline="06:00")` takes the same jobs you would pass to `run()`. Token counts come from the job where it knows them (`metadata={"input_tokens": ..., "output_tokens": ...}`, or `max_tokens` as an output ceiling) and are a labeled chars/4 estimate where it does not — every figure reports its provenance in `basis`, and a quote with no output signal is marked a **floor**, not an estimate.

## Deadlines

Deadlines are how software says "this can wait" — the full semantics live in [SPEC.md](SPEC.md).

| Form | Meaning |
| --- | --- |
| `"06:00"` | the next 6am, local time (the canonical overnight form) |
| `"4h"`, `"90m"`, `"2d"` | relative to now |
| `"2026-08-21T06:00:00-07:00"` | ISO 8601, absolute |
| `datetime` / `timedelta` / seconds | native Python forms |

## How a run works

1. Jobs are grouped by venue (`claude-*` → Anthropic Message Batches, `gpt-*`/`o*` → OpenAI Batch) and submitted at the batch tier — 50% of list.
2. `offpeak` polls the venues, backing off while the window is long.
3. When remaining time reaches the **risk buffer** (default: 15% of the window, clamped to 1–10 minutes), unfinished jobs are cancelled and re-run synchronously so the deadline holds. Set `fallback="none"` to report them instead.
4. Results come back in input order, each with a per-job `Receipt`; `offpeak.receipt(results)` settles the run.

```python
results = offpeak.run(
    jobs,
    deadline="06:00",
    fallback="sync",       # meet the deadline at list price if the batch is at risk
    risk_buffer=600,       # seconds held in reserve (optional)
)
```

## Receipts and prices

Receipts are computed against a bundled snapshot of public list prices (batch = 50% of list, as published). Providers change prices — verify and override at runtime:

```python
import offpeak

offpeak.prices.register_price("my-fine-tune", input_per_m=4.0, output_per_m=16.0)
```

Unknown models settle with `cost = None` rather than a guess.

## What this is (and the roadmap)

`offpeak` is the open client and spec for a simple claim: **intelligence has a time value**. A large share of AI work — embeddings, evals, backfills, report generation, overnight agents — has no human waiting on it, and the venues already price that patience at −50%. This library is the missing workflow.

The **[night board](https://github.com/offpeak-ai/offpeak/blob/board-data/nightly/BOARD.md)** marks the same claim against open grid data every night — power and carbon peak/off-peak spreads, alongside the 2.0x token spread the batch tiers already publish.

The roadmap follows the same interface upward: more venues (Google batch, spot capacity, off-peak windows on your own GPUs), queue-latency forecasting instead of a fixed risk buffer, portfolio placement across venues, energy- and carbon-aware scheduling with per-job receipts. The venue interface (`offpeak.Venue`) is deliberately the extension point — a venue is anywhere deferred work can run.

A hosted desk that does the forecasting, cross-venue portfolio scheduling, and SLA insurance at fleet scale — payloads never leaving your perimeter — is being built by the same team. The SDK and the deadline spec stay open, Apache-2.0.

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Spec changes start as issues against [SPEC.md](SPEC.md).

## License

Apache-2.0 © Offpeak
