# The published sheet

`offpeak` prices every quote and every receipt against a **bundled snapshot** of
numbers other people publish. That snapshot moves when a release moves, and not
before.

That is deliberate, and it is the reason a receipt is worth anything:

```
prices    snapshot 2026-08-28 — override via offpeak.prices
```

A receipt that names its sheet can be re-derived later by anyone. A library that
silently repriced itself overnight could not offer that — last month's receipt
would settle against this month's numbers, and "checkable against the published
sheet" would quietly become "trust us".

The cost of that choice is staleness: providers move prices whenever they like,
and your install froze on whatever shipped. This page is the other half — the
same sheet, published as data, picked up **deliberately**.

## There is no database

The sheet is a file. Dated, immutable, on the `board-data` branch:

| what | where |
|---|---|
| newest sheet | `https://raw.githubusercontent.com/offpeak-ai/offpeak/board-data/sheet/latest.json` |
| a specific sheet | `.../board-data/sheet/2026-08-23.json` |
| what exists | `.../board-data/sheet/index.json` |
| settled runs | `.../board-data/nightly/SETTLED.json` |

No service, no account, no key, no uptime obligation, and nothing that can go
down and take your job with it. A git host and its CDN serve it; `curl` and a
browser can both read it. If this project disappeared tomorrow the files would
still be checkable against the provider pages they name.

!!! note "`sheet/<date>.json` never changes"
    Once published, a dated sheet is frozen. `tools/publish_sheet.py` refuses to
    rewrite one whose contents moved without its date moving, because that would
    silently change what an old receipt settled against. `latest.json` is a copy
    of the newest — and it names its own date, so a caller that wants
    reproducibility back can pin the dated file it came from.

## Using one

Nothing fetches this for you. The default is always the bundled sheet, so
`offpeak` keeps working offline and on a locked-down network.

```python
import offpeak
from offpeak import prices

SHEET = "https://raw.githubusercontent.com/offpeak-ai/offpeak/board-data/sheet/latest.json"

load = prices.load_sheet(SHEET)
print(load)
# loaded price sheet 2026-08-28 from https://…/latest.json: 30 model(s) —
# 0 new, 0 changed, 30 unchanged; 5 fast row(s), 3 promo note(s)

print(prices.sheet_date())   # what is pricing jobs right now
print(prices.PRICE_SHEET_DATE)  # what this release bundled — never moves
```

`load_sheet` takes an `https://` URL, a filesystem path, or an already-parsed
dict. It **merges** by default, so `register_price()` overrides and older models
you still run survive. Pass `replace=True` to make the loaded sheet the whole
truth, and anything it omits resolves to `None`.

`prices.reset_sheet()` puts the release's own numbers back.

### Pin it in production

`latest.json` is convenient and moves under you. If you need two runs a month
apart to price identically, pin the dated file:

```python
prices.load_sheet(".../board-data/sheet/2026-08-23.json")
```

Then record `prices.sheet_date()` on whatever you write out, the way
`tools/mechanics_run.py` does. A figure whose sheet you cannot name is not
checkable.

## What it refuses

Loading a sheet is loading numbers that will settle real bills, so the guards
are strict and they fail closed — a refused sheet leaves the table exactly as it
was, never half-applied.

- **Plain `http://`** — refused outright. A sheet modifiable in transit must not
  price a bill.
- **An unknown schema** — a sheet this build only half-understands would price
  jobs against fields it guessed at.
- **No `sheet_date`** — an undated sheet is not checkable, which is the whole
  point of having one.
- **A different `batch_discount`** — refused rather than applied. The discount is
  a rule the venues publish identically, not a row, and `client` and `quote` bind
  their copy of it at import. Honouring a new one here would price some
  arithmetic at the new rate and some at the old. That is a release, not a
  download.

## The format

```json
{
  "schema": "offpeak.price-sheet/1",
  "sheet_date": "2026-08-28",
  "generated_utc": "2026-08-28T05:12:00+00:00",
  "batch_discount": 0.5,
  "prices": {
    "claude-haiku-4-5": {"input_per_m": 1.0, "output_per_m": 5.0}
  },
  "fast_prices": {
    "gpt-5.6-sol": {"input_per_m": 8.0, "output_per_m": 40.0},
    "claude-opus-5": {"input_per_m": 10.0, "output_per_m": 50.0}
  },
  "promo_notes": {
    "gpt-5.6-sol": {
      "through": "2026-11-21",
      "post_promo": [5.0, 30.0],
      "source": "developers.openai.com/api/docs/pricing",
      "note": "GPT-5.6 Sol's promotional pricing is available at least through November 21, 2026."
    }
  },
  "lanes": {
    "deepseek-": "clock"
  }
}
```

`prices.export_sheet()` produces exactly this from whatever sheet is in force,
which is how the published files are generated — the format is not a second
description of the sheet that can drift from it.

`lanes` (added 2026-08-30, still schema `/1`) says how a venue sells its
discount: `"batch"` — the default, and absent for every model that has no row —
or `"clock"`, for a venue with no batch API whose half price is decided by the
wall clock at the moment a request is made. DeepSeek is the one clock lane today;
`prices.lane_for("deepseek-v4-flash")` answers `"clock"`. The key is additive: a
reader that predates it ignores it, and a sheet that omits it retracts nothing,
so `load_sheet(..., replace=True)` on an older file leaves the bundled lanes in
place. See [DeepSeek](venues-deepseek.md).

## The settled ledger, as data

`nightly/SETTLED.json` is written beside `SETTLED.md` by
`tools/settle_report.py`. Same receipts, same numbers; the Markdown is for
people and the JSON is for anything that would otherwise scrape a table or
hand-copy rows into a web page.

```json
{
  "schema": "offpeak.settled-runs/1",
  "summary": {
    "runs": 11, "jobs": 232,
    "list_usd": 0.02645, "paid_usd": 0.01460, "captured_usd": 0.01184,
    "runs_capturing": 8, "runs_capturing_nothing": 3,
    "venues_capturing": ["anthropic:batch", "gemini:batch", "mistral:batch", "openai:batch"]
  },
  "runs": [{
    "run_id": "2026-08-26-mistral-3",
    "receipt_uuid": "763f5fdf-e83f-5a41-a8f3-6d6550d3e46e",
    "venue_handles": {"mistral:batch": ["1ae160f2-fcd4-4bdb-afe1-17457fa1985f"]},
    "captured_pct": 50.0,
    "notes": ["…"]
  }]
}
```

### The id is derived, not minted

`receipt_uuid` is a **uuid5 over `run_id|settled_utc`**, not a random token.
A uuid4 would identify a receipt and prove nothing about it — only whoever
generated it could say it was right. This one anyone holding the receipt can
recompute:

```python
import uuid
NS = uuid.UUID("6f1b1a3e-6a2f-5c4d-9f0e-0b7a2c9d4e51")
uuid.uuid5(NS, f"{r['run_id']}|{r['settled_utc']}")
```

Which means it cannot drift from the run it names. A receipt may state its own
`receipt_uuid`, but a stated id that disagrees with the derived one is
**refused** rather than trusted. `run_id` stays the ledger's readable key: it
opens with the settlement date, so a row is unambiguous and sorts correctly, and
two receipts claiming one `run_id` are a hard error — that collision used to
drop a settlement from `SETTLED.md` while double-counting it in `SETTLED.json`.

### `venue_handles` is the checkable part

The venue's own batch id — `batch_6a8e…`, `msgbatch_01BH…`, `4a7ccbb3-…`. It is
the one identifier in a receipt a **third party can verify**: ask the venue
about the handle and it will answer. Everything else on the row is our
arithmetic; this is the provider's record of the same event.

Publishing one is safe. Handles are opaque and account-scoped — holding one
grants nothing without the key, and none encodes an org or project id.

## What a receipt may never contain

Receipts are **aggregate by construction**, and the ledger enforces it rather
than trusting care:

| tier | holds | published |
|---|---|---|
| receipt (`receipts/*.json`) | counts, money, handles, prose | **yes** |
| run artifact (`settlement.json`) | + per-job model output, + verbatim provider errors | no |
| provider response | everything | never persisted |

`tools/settle_report.py` refuses a receipt carrying `per_job`, `results`,
`messages`, `prompts` or `raw`, and refuses anything secret-shaped — an `sk-`
key, a bearer token, an `AKIA…`, an `AIza…`, an `api_key:` pair.

That guard exists because receipts are written by reading the run artifact, and
provider error strings routinely carry request URLs, org ids and account hints.
Copying one verbatim into a public file is a single careless paste, and care is
not a control.

**If you run `offpeak` on your own keys, the same rule binds harder.** Your
prompts and outputs are your data, and a receipt has to be provable without
them — which it is, because every figure on it is aggregate. The handle is
yours to include or omit.

`venues_capturing` counts venues that reached a batch tier **and kept it**. A run
that reached the tier and then lost the results is not a capturing venue — see
`2026-08-26-mistral-2`, where the batch succeeded and the driver discarded it. A
summary that counted that would be the marketing version of this ledger rather
than the accounting one.

## How drift gets noticed in the first place

[`tools/sheet_watch.py`](https://github.com/offpeak-ai/offpeak/blob/main/tools/sheet_watch.py)
hashes the provider pages this sheet cites, daily, and records what moved on
`board-data`. It never edits the sheet — detection and resolution are different
jobs, and a tool that rewrote `prices.py` on a hash diff would eventually launder
a marketing rewrite into a receipt.

So the loop is: **watch** notices a page moved → a human reads the diff and
settles what it meant → the sheet moves in a release → **publish** writes the new
dated file → callers who opted in pick it up. Every step is either checkable or
deliberate, and the automated ones never touch a number.
