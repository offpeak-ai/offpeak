# DeepSeek — clock-priced

`deepseek:clock` is the first venue in `offpeak` that is not a batch tier.
DeepSeek publishes **no batch API**. It publishes a clock instead: peak hours
are **01:00–04:00 and 06:00–10:00 UTC, Monday through Friday**, and every
other hour — evenings, the gap between the two blocks, the whole weekend — is
off-peak at **half the peak rate**, on input, output and cache-hit alike.

That is the same 2.0x spread the batch venues sell, on a different axis. A
batch tier prices *how long you can wait*. DeepSeek prices *when the request
lands*. Same discount, different mechanism — and `offpeak` gives it the same
one-argument interface.

!!! warning "Unverified live"
    No request has yet been made through this driver to `api.deepseek.com`.
    The clock, the hold, the request shape and the settlement are exercised
    network-free, and every price is a transcription of DeepSeek's page. The
    plan-gating lesson from Groq (403), Mistral (402) and Gemini (billing) is
    that the first sub-cent live run is what verifies a driver; this one has
    not had it. When it does, the receipt goes in `receipts/`.

## Install and configure

```bash
pip install "offpeak[deepseek]"     # an alias of the openai extra
export DEEPSEEK_API_KEY=sk-...
```

The API is OpenAI-compatible at `https://api.deepseek.com`, so the `openai`
SDK is the client. The driver refuses to build one without `DEEPSEEK_API_KEY`
rather than letting the SDK fall back to `OPENAI_API_KEY` and send it to the
wrong provider.

It is **opt-in** — not in `default_venues()`, for the same reason Groq,
Mistral and Gemini are not: a model name should not start costing money at a
venue nobody asked for.

```python
import offpeak
from offpeak.venues import DeepSeekClock

jobs = [offpeak.job("deepseek-v4-flash", f"Summarize:\n\n{d}", max_tokens=2048) for d in docs]
results = offpeak.run(jobs, deadline="06:00", venues=[DeepSeekClock()])
print(offpeak.receipt(results))
```

## How the hold works

There is nothing to upload and nothing to poll, so the driver does not batch.
It **holds**:

1. `submit()` sends nothing. It records the jobs with a `release_at` — now, if
   now is off-peak, else the end of the current peak block — and returns an
   in-process handle. The discount is for *when* you run, not for how long you
   waited, so there is nothing to give the venue yet.
2. `run()` polls `status()` as it would any batch. Before `release_at` the
   hold reports `in_progress`. At or after it, `status()` runs every held job
   through `chat.completions` — one request per job, a small thread pool,
   standard library only — and reports `completed`.
3. `collect()` returns the stored results; `cancel()` drops a hold that has
   not released (once executed there is nothing remote to cancel).
4. If the deadline cannot wait for the boundary, `run()` does what it does at
   every venue: cancels the hold under the risk buffer and runs the stragglers
   through `run_sync()` — **now, at whatever rate the clock says now**. That
   may be peak. It is the honest outcome, and the receipt says so.

The longest a hold can last is four hours (the 06:00–10:00 block). A deadline
that clears the next boundary captures the spread; one that does not takes the
fallback. Same contract as the batch venues and their 24h windows.

A hold lives in the process that placed it. That is the honest limit of a
venue with no server-side queue: a process that exits during a hold has not
spent anything, and has not run anything either.

## The clock, as functions

The schedule is exported so a caller can plan against it rather than discover
it. All take an aware datetime (a naive one is read as UTC) and answer in UTC.

```python
from datetime import datetime, timezone
from offpeak.venues.deepseek_clock import is_peak, next_offpeak_start, offpeak_until, rate_multiplier

now = datetime.now(timezone.utc)
is_peak(now)             # True inside a weekday block
next_offpeak_start(now)  # now if off-peak, else the end of the current block
offpeak_until(now)       # when this off-peak stretch ends; None at peak
rate_multiplier(now)     # 2.0 at peak, 1.0 off-peak
```

Block ends are exclusive — 04:00:00 is off-peak, 03:59:59 is peak — and the
weekend is one stretch: Friday 10:00 UTC through Monday 01:00 UTC, 63 hours,
which `offpeak_until` answers as a single instant.

## Settlement

The bundled sheet stores DeepSeek's **peak** rate as the standard row, and
the library's 50% batch rule reproduces the published off-peak column
exactly:

| Model | Peak (standard) | Off-peak (= batch rule) |
| --- | --- | --- |
| `deepseek-v4-flash` | $0.44 / $1.32 | $0.22 / $0.66 |
| `deepseek-v4-pro` | $1.32 / $3.96 | $0.66 / $1.98 |

USD per 1M tokens, input / output, read off
[api-docs.deepseek.com/quick_start/pricing](https://api-docs.deepseek.com/quick_start/pricing)
on 2026-08-28 and re-read 2026-08-30. `prices.lane_for("deepseek-v4-flash")`
answers `"clock"` so a caller can tell the lane apart from a queue.

Every result this driver produces carries the regime it ran under, stamped
**per request at the moment it was made** — `regime`, `rate_multiplier`,
`paid_fraction`, `executed_at_utc` in `Result.raw`. `run()` copies
`paid_fraction` onto the receipt, where it outranks the batch/fallback rule.
That is what makes both outcomes settle correctly:

- a hold released off-peak pays half of list — the same arithmetic as a batch;
- a fallback at peak pays list, and the receipt reads `(sync fallback) (paid 1x list)`;
- a fallback that happened to run off-peak — a failed request rescued while
  the clock was still cheap — pays half, and the receipt reads
  `(sync fallback) (paid 0.5x list)`, with nothing left on the table;
- a hold that drains across a boundary prices its last jobs at peak, per job.

**Cache hits are not modelled.** DeepSeek prices a cache-hit input token at
$0.007 / $0.022 off-peak and reports hits in `usage.prompt_cache_hit_tokens`;
the driver records that count but the sheet has no cache dimension, so every
input token settles at the miss rate. A cache-heavy run's receipt overstates
what was paid — the conservative direction, stated rather than hidden.

## Thinking mode and ceilings

The V4 family thinks by default, and the reasoning is billed as output. The
driver leaves that at the venue's default rather than fighting it — a job that
wants it off can say so through its own params — but a small `max_tokens` buys
the reasoning and an empty answer. Size the ceiling for a model that thinks.
Context is 1M tokens; the output ceiling is 384K. `max_tokens` is DeepSeek's
own spelling and is passed through untouched.
