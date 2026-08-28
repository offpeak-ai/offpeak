# Airflow

Your task gains one argument.

Airflow has said when work must finish since long before anyone batched an LLM.
Two of its native concepts are already deadlines, stated in Airflow's own words:

| Airflow says | It means | `offpeak` deadline |
| --- | --- | --- |
| `schedule="0 6 * * *"` | this runs again tomorrow at 06:00 | finish before the next run |
| `sla=timedelta(hours=4)` | late after four hours | `data_interval_end + sla` |

So there is no `offpeak-airflow` package, and there will not be one.
`offpeak.run()` takes jobs and a deadline, returns one `Result` per job, and
keeps nothing between calls. Airflow already owns the schedule, the retries and
the alerting.

## Install

```bash
pip install "offpeak[all]" "apache-airflow>=2.7"
```

## Deadline = the next run

The DAG's own cadence is sitting in the task context. The data interval is one
schedule period, so one period past its end is when this DAG runs again:

```python
import offpeak
import pendulum
from airflow.decorators import dag, task


@dag(
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
)
def nightly_digest():
    @task
    def summarize(docs: list[str], **context) -> list[str]:
        # One schedule period past the end of this interval: the instant this
        # DAG runs again. Finish before then, or the next run laps this one.
        start = context["data_interval_start"]
        end = context["data_interval_end"]
        deadline = end + (end - start)

        jobs = [
            offpeak.job("claude-haiku-4-5", f"Summarize:\n\n{d}", max_tokens=512)
            for d in docs
        ]
        results = offpeak.run(jobs, deadline)

        print(offpeak.receipt(results))
        return [r.text or "" for r in results]

    summarize(docs=["..."])


nightly_digest()
```

`data_interval_start` and `data_interval_end` arrive as `pendulum.DateTime`,
which subclasses `datetime.datetime` and is already timezone-aware. `offpeak`
takes it as-is — no string, no conversion, no assumed timezone.

The deadline is the consumer's need — *summaries ready by 06:00* — not a request to run the work late. `offpeak` submits immediately and the venue is free to return any time before the window closes; observed batch completion on the [queue board](https://github.com/offpeak-ai/offpeak/blob/board-data/nightly/QUEUE.md) runs in minutes, not hours. The window buys the discount and the provider's freedom to choose when — never a delay you asked for.

## Deadline = the SLA

When a task declares an SLA, that is the deadline, and it is the more honest one
— it is the promise you actually made. Airflow measures an SLA from the run's
`data_interval_end`, so the arithmetic is the same shape:

```python
from datetime import timedelta

SLA = timedelta(hours=4)


@task(sla=SLA)
def summarize(docs: list[str], **context) -> list[str]:
    deadline = context["data_interval_end"] + SLA

    jobs = [offpeak.job("claude-haiku-4-5", f"Summarize:\n\n{d}", max_tokens=512) for d in docs]
    results = offpeak.run(jobs, deadline)
    return [r.text or "" for r in results]
```

Declaring the constant once and using it for both `sla=` and the deadline keeps
the two from drifting. If they drift, Airflow alerts on one number while you
priced against another.

!!! note "`sla=` is Airflow 2"
    Airflow 3 removed the task-level `sla` parameter and its
    `sla_miss_callback` in favour of a separate deadline-alerting mechanism. The
    interval arithmetic in the first example uses only `data_interval_start` and
    `data_interval_end` and is unaffected — prefer it if you are on 3.x, or pass
    an explicit deadline from a DAG param.

## Both, safely: the earlier of the two

An SLA shorter than the schedule is the real constraint; an SLA longer than it
is aspirational, because the next run is already on its way. Taking the minimum
is one line and never wrong:

```python
end = context["data_interval_end"]
next_run = end + (end - context["data_interval_start"])
deadline = min(next_run, end + SLA)
```

## Quote it in the same DAG

`quote()` makes no API calls and needs no key, so pricing the wait is a free
upstream task — useful as a short-circuit when the spread does not justify the
latency:

```python
@task
def price_the_wait(docs: list[str], **context) -> float:
    end = context["data_interval_end"]
    deadline = end + (end - context["data_interval_start"])
    jobs = [offpeak.job("claude-haiku-4-5", f"Summarize:\n\n{d}", max_tokens=512) for d in docs]
    return offpeak.quote(jobs, deadline=deadline).spread_usd
```

## Two things that will bite you

!!! danger "Backfill and catchup put the deadline in the past"
    With `catchup=True`, or on any manual backfill, the data interval is
    historical — and so is a deadline derived from it. `parse_deadline` raises
    `ValueError` for a deadline that is not in the future, by design: a deadline
    that has already passed is not a deadline, and quietly sliding it forward
    would invent an SLA nobody agreed to.

    Decide explicitly what a backfilled run means. Usually it means "there is no
    one waiting, take the cheapest window you have":

    ```python
    horizon = max(deadline, pendulum.now("UTC") + timedelta(hours=24))
    results = offpeak.run(jobs, horizon)
    ```

    That is a choice about your data, so it belongs in your DAG rather than in
    the library.

!!! warning "Airflow retries do not re-run a settled batch"
    `offpeak.run()` does not raise on provider failure. A venue that dies at
    submit, poll or fallback returns a failed `Result` carrying the provider's
    message while every other job settles normally, so a task-level `retries`
    will not fire on it. Exceptions are reserved for programming errors — a bad
    deadline, or a model no configured venue supports.

    If a partial failure should fail the task, say so:

    ```python
    failed = [r for r in results if not r.ok]
    if failed:
        raise RuntimeError(f"{len(failed)}/{len(results)} jobs failed: {failed[0].error}")
    ```

    Note that a retry re-derives the deadline from the same data interval, so it
    targets the same instant rather than sliding — which is the behaviour you
    want, right up until that instant is past and `run()` says so.

## What you get back

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

`sla 5000/5000 met` is `offpeak`'s own accounting of the deadline you handed it,
which is the same promise Airflow is watching from the outside.

See the **[Quickstart](quickstart.md)** for `quote()`, `run()` and receipts in
isolation, **[Temporal](quickstart-temporal.md)** for the same retelling under a
workflow engine, and the **[Spec](spec.md)** for the full deadline semantics.
