# offpeak

**Deadline-priced inference.** The deadline is the input; the discount follows.

A large share of AI work — embeddings, evals, backfills, report generation,
daily agent runs — has no human waiting on it. The providers already price that
patience: OpenAI and Anthropic both publish their batch tiers at **50% off
list**. `offpeak` is the workflow that collects the difference.

```python
import offpeak

jobs = [offpeak.job("claude-haiku-4-5", f"Summarize:\n\n{d}") for d in docs]

print(offpeak.quote(jobs, deadline="06:00"))   # what is the wait worth?
results = offpeak.run(jobs, deadline="06:00")  # collect it
print(offpeak.receipt(results))                # what it actually cost
```

- **[Quickstart](quickstart.md)** — install, quote, run, read the receipt.
- **[The Spread Board](night-board.md)** — the same claim, marked daily against open grid data.
- **[Spec](spec.md)** — deadline semantics, statuses, receipts.
- **[API reference](reference.md)** — every public symbol.
- **[Roadmap](roadmap.md)** — what exists, what does not, and what is being built.

## What it guarantees

**One `Result` per job, always.** Provider failures at submit, poll, cancel or
sync are captured, not raised. Affected jobs take the sync fallback where the
deadline still allows it, and otherwise return failed with the provider's
message attached. Exceptions are reserved for programming errors — a deadline
in the past, or a model no venue supports.

**Your keys, your perimeter.** `offpeak` talks straight to the providers with
your own credentials. There is no proxy and no third party in the data path.

**Receipts are arithmetic, not estimates.** Every figure traces to a published
price sheet, and a model that is not on one settles as `None` rather than a
guess.
