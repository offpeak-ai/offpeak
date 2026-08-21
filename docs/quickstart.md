# Quickstart

## Install

```bash
pip install "offpeak[all]"        # OpenAI + Anthropic venues
pip install "offpeak[anthropic]"  # or just one
pip install "offpeak[openai]"
```

The core has zero dependencies; provider SDKs load only through the extras.
Venues read the standard environment variables (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`), or take a configured client:
`OpenAIBatch(client=my_client)`.

## 1. Quote — before you spend anything

`quote()` makes **no API calls and needs no key**. It prices your jobs against
the bundled sheet: list versus batch, per venue.

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

From Python, the same jobs you would pass to `run()`:

```python
q = offpeak.quote(jobs, deadline="06:00")
print(q.spread_usd, q.spread_pct)
```

!!! warning "Quotes that omit output are marked a floor"
    Output costs more than input on every model on the sheet. If a job carries
    no output-token signal, `quote()` prices its output at **zero** and labels
    the whole quote a `FLOOR` — a stated floor is safer than an invented
    number. Give it `max_tokens`, or explicit counts:

    ```python
    offpeak.job("claude-haiku-4-5", prompt, max_tokens=512)
    offpeak.Job(model=..., messages=[...],
                metadata={"input_tokens": 800, "output_tokens": 200})
    ```

    `Quote.basis` reports the provenance of every figure.

## 2. Run — against a deadline

```python
results = offpeak.run(jobs, deadline="06:00")
```

Each job goes to the batch tier of a venue that supports its model. `offpeak`
polls until the work lands. If the batch has not completed by the time the
remaining window shrinks to the risk buffer, it cancels and re-runs the
stragglers synchronously at list price — you stated a deadline, and it is met.

Deadlines accept `"06:00"` (next occurrence), `"6h"`, `"90m"`, a `datetime`, a
`timedelta`, seconds, or an ISO 8601 string.

## 3. Read the receipt

```python
print(offpeak.receipt(results))
```

```
OFFPEAK SETTLEMENT ────────────────────────────
jobs      5000 (5000 ok, 120 sync fallback, 0 failed)
sla       5000/5000 met
venues    anthropic:batch 3000 · openai:batch 2000
tokens    41,000,000 in · 3,200,000 out
list      $2,469.00
paid      $1,234.50
captured  $1,234.50 (50.0%)
left      $29.63 on the table (120 job(s) missed the batch tier)
prices    snapshot 2026-08 — override via offpeak.prices
───────────────────────────────────────────────
```

`left on the table` is what the sync fallback gave up by missing the batch
tier. Per-job receipts render the same way — `print(results[0].receipt)` — with
sub-cent precision, so a small run reports what it cost rather than `$0.00`.

## Prices

The bundled sheet is a dated snapshot. Providers move prices; override at
runtime rather than waiting for a release:

```python
offpeak.prices.register_price("my-fine-tune", input_per_m=4.0, output_per_m=16.0)
```

Unknown models settle as `None`, never a guess.
