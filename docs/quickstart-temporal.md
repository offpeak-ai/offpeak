# Temporal

Your activity gains one argument.

Temporal already knows when work must finish — that is most of what a workflow
engine is for. `offpeak` needs the same fact. So the integration is not a
package, an interceptor or a plugin: it is a `deadline` parameter threaded from
the workflow into the activity, and a normal `offpeak.run()` call inside it.

There is no `offpeak-temporal` adapter, and there will not be one. `run()` holds
no state between calls — it takes jobs and a deadline, returns one `Result` per
job, and forgets you. Anything an adapter would own, Temporal already owns
better.

## Install

```bash
pip install "offpeak[all]" temporalio
```

## The activity

```python
import asyncio

import offpeak
from temporalio import activity


@activity.defn
async def summarize(docs: list[str], deadline: str) -> list[str]:
    """Summarize documents by `deadline`, on the cheapest venue that makes it."""
    jobs = [
        offpeak.job("claude-haiku-4-5", f"Summarize:\n\n{d}", max_tokens=512)
        for d in docs
    ]

    # run() blocks — it submits, then polls until the batch lands. Off the event
    # loop it goes, with a heartbeat so Temporal can tell "waiting on a batch
    # tier" apart from "the worker died".
    task = asyncio.create_task(asyncio.to_thread(offpeak.run, jobs, deadline))
    while True:
        done, _ = await asyncio.wait({task}, timeout=30)
        activity.heartbeat()
        if done:
            break

    results = task.result()
    activity.logger.info("offpeak settlement\n%s", offpeak.receipt(results))
    return [r.text or "" for r in results]
```

That is the whole integration. `deadline` is the one new argument.

The deadline is the consumer's need — *summaries ready by 06:00* — not a request to run the work late. `offpeak` submits immediately and the venue is free to return any time before the window closes; observed batch completion on the [queue board](https://github.com/offpeak-ai/offpeak/blob/board-data/nightly/QUEUE.md) runs in minutes, not hours. The window buys the discount and the provider's freedom to choose when — never a delay you asked for.

## The workflow

```python
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import summarize


@workflow.defn
class NightlyDigest:
    @workflow.run
    async def run(self, docs: list[str]) -> list[str]:
        # Resolve the deadline HERE, on the workflow's deterministic clock, and
        # pass it down as an absolute instant.
        deadline = (workflow.now() + timedelta(hours=8)).isoformat()

        return await workflow.execute_activity(
            summarize,
            args=[docs, deadline],
            # Must outlast the window itself — the activity is alive for as long
            # as the batch tier takes.
            start_to_close_timeout=timedelta(hours=9),
            heartbeat_timeout=timedelta(minutes=2),
        )
```

## Three things that will bite you

!!! warning "Resolve the deadline in the workflow, not the activity"
    `workflow.now()` is Temporal's deterministic clock: it replays to the same
    instant every time. `datetime.now()` inside a workflow is not, and Temporal
    will tell you so. Computing the deadline once, in the workflow, and passing
    it down is both the deterministic move and the honest one — the deadline is
    a property of the business problem, not of whichever worker picked the task
    up.

!!! danger "Pass an absolute instant — never `\"06:00\"`"
    `offpeak` accepts `"06:00"` and resolves it to **the next occurrence**:
    today if that is still ahead, otherwise tomorrow. That is the right rule for
    a session and the wrong one under a retry policy. An activity that fails at
    05:58 and retries at 06:01 would silently reprice against tomorrow morning —
    a 24-hour window where you meant three minutes, and a deadline nobody chose.

    ISO 8601 from `workflow.now()` pins it. Every retry then targets the same
    instant, and once that instant is genuinely past, `run()` raises
    `ValueError` rather than inventing a new one.

!!! warning "`start_to_close_timeout` must exceed the window"
    The activity stays alive for the whole wait — that is the point. Set the
    timeout to the deadline window plus margin, or Temporal will kill the
    activity mid-batch and you will pay for work you cancel. Pair it with
    `heartbeat_timeout` and the loop above so a genuinely dead worker is still
    detected in minutes rather than hours.

## Retries need no special handling

`offpeak.run()` does not raise on provider failure. A venue that dies at submit,
poll or fallback comes back as a failed `Result` carrying the provider's
message, and every other job in the batch settles normally. Exceptions are
reserved for programming errors — a malformed deadline, or a model no configured
venue supports.

So a Temporal `RetryPolicy` retries the things worth retrying (the worker died,
the process was evicted) and does not retry a batch that already returned
answers. If you want a partial failure to fail the activity, say so explicitly:

```python
failed = [r for r in results if not r.ok]
if failed:
    raise RuntimeError(f"{len(failed)}/{len(results)} jobs failed: {failed[0].error}")
```

## Quote before you commit the window

`quote()` makes no API calls and needs no key, so a pre-flight activity can
price the wait before the workflow commits to it:

```python
@activity.defn
async def price_the_wait(docs: list[str], deadline: str) -> float:
    jobs = [offpeak.job("claude-haiku-4-5", f"Summarize:\n\n{d}", max_tokens=512) for d in docs]
    return offpeak.quote(jobs, deadline=deadline).spread_usd
```

It belongs in an activity rather than in workflow code: it reads the clock to
measure the window, which makes it non-deterministic in Temporal's sense even
though it touches no network.

## What you get back

`offpeak.receipt(results)` settles the run — what ran where, and what the hour
was worth:

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
prices    snapshot 2026-08-23 — override via offpeak.prices
───────────────────────────────────────────────
```

Logging that line from the activity puts the spread in the same place you
already look when a workflow misbehaves.

See the **[Quickstart](quickstart.md)** for `quote()`, `run()` and receipts in
isolation, **[Airflow](quickstart-airflow.md)** for the same retelling under a
scheduler, and the **[Spec](spec.md)** for the full deadline semantics.
