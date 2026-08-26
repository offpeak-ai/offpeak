# The published sheet

`offpeak` prices every quote and every receipt against a **bundled snapshot** of
numbers other people publish. That snapshot moves when a release moves, and not
before.

That is deliberate, and it is the reason a receipt is worth anything:

```
prices    snapshot 2026-08-23 — override via offpeak.prices
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
# loaded price sheet 2026-08-23 from https://…/latest.json: 27 model(s) —
# 0 new, 0 changed, 27 unchanged; 3 fast row(s), 3 promo note(s)

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
  "sheet_date": "2026-08-23",
  "generated_utc": "2026-08-26T05:12:00+00:00",
  "batch_discount": 0.5,
  "prices": {
    "claude-haiku-4-5": {"input_per_m": 1.0, "output_per_m": 5.0}
  },
  "fast_prices": {
    "gpt-5.6-sol": {"input_per_m": 8.0, "output_per_m": 40.0}
  },
  "promo_notes": {
    "gpt-5.6-sol": {
      "through": "2026-11-21",
      "post_promo": [5.0, 30.0],
      "source": "developers.openai.com/api/docs/pricing",
      "note": "GPT-5.6 Sol's promotional pricing is available at least through November 21, 2026."
    }
  }
}
```

`prices.export_sheet()` produces exactly this from whatever sheet is in force,
which is how the published files are generated — the format is not a second
description of the sheet that can drift from it.

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
  "runs": [{"run_id": "…", "scale": "…", "captured_pct": 50.0, "notes": ["…"]}]
}
```

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
