# The night board

`offpeak` rests on a claim: **intelligence has a time value.** The token side of
that claim is already settled, and it is wider than the headline discount.

- **Patience is priced at −50%.** OpenAI and Anthropic both publish batch tiers
  at half of list: a flat **2.0x** spread for work that can wait.
- **Haste is priced too.** Hold the model and the venue constant and read the
  same sheet across its urgency tiers: `gpt-5.6-sol` is **$8.00 / $40.00** per
  1M tokens on OpenAI's fast tier against **$2.00 / $10.00** on its batch tier
  — a **4x intra-venue urgency spread**, on both legs, for the hour alone.
  Source: [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing).

!!! note "The promo caveat"
    `gpt-5.6-sol`'s standard rate is promotional — the sheet says it runs *at
    least* through **2026-11-21**, after which list is $5/$30. Fast and batch
    are both defined off that list (2x and 0.5x), so the promo moves the
    dollars and leaves the ratio: **the 4x is the durable figure, the prices
    are the perishable ones.** Both are in the SDK rather than in prose —
    `offpeak.prices.urgency_spread("gpt-5.6-sol")` returns `4.0`, and
    `promo_decay()` returns the step-up the date will bring.

The night board marks the same claim against the other side of the trade: the
grid the compute runs on. Every night it records what power and carbon actually
did between the evening peak and the small hours, from open, keyless sources.
The grid's spread is not published anywhere — it has to be observed — and on the
night of 2026-08-20 the ERCOT Houston hub marked **3.94x** between its evening
peak and the trough that followed, against the 4x a venue charges for the same
hour of impatience.

The two sides of the grid do not move together, which is the point of marking
both. On 2026-08-18 and 08-19, CAISO's carbon ran **cleaner at the evening peak
than in the small hours** — spreads of 0.76x and 0.73x — because the sun that
serves the California evening has set by midnight. Cheap hours are not
automatically clean hours, and a board that only recorded price would have
implied otherwise.

**[→ Read the board](https://github.com/offpeak-ai/offpeak/blob/board-data/nightly/BOARD.md)**

## How it works

Two passes over the same night, run by
[a scheduled workflow](https://github.com/offpeak-ai/offpeak/blob/main/.github/workflows/nightly.yml):

| Pass | When | What it records |
|---|---|---|
| `quote` | 19:00Z | The night ahead — carbon **forecast**, day-ahead power |
| `mark` | 06:30Z | The night just finished — carbon **actuals** |

A night runs **16:00Z–07:00Z**: it opens with the 17:00 BST evening peak and
closes after the 00–05 BST trough, so one span carries both windows the board
compares.

Output lands on the
[`board-data` branch](https://github.com/offpeak-ai/offpeak/tree/board-data) —
`nightly/BOARD.md` plus the raw JSON per night — because `main` is protected and
would reject a nightly bot push.

## Settled runs are a different ledger

`BOARD.md` observes; it spends nothing at any venue. Runs that actually
executed and actually billed go in `nightly/SETTLED.md` on the same branch,
written by
[`tools/settle_report.py`](https://github.com/offpeak-ai/offpeak/blob/main/tools/settle_report.py)
from the receipts in
[`receipts/`](https://github.com/offpeak-ai/offpeak/tree/main/receipts) — never
by hand — and published by a manual workflow, because a settlement is a
deliberate act.

Every settled row carries its **scale**, and the column is not decoration. A
few dozen jobs proving the mechanics end to end and a production book are both
real settlements and are not the same evidence. A ledger that lets a reader
confuse them is doing marketing rather than accounting, so the scale is printed
before the money is.

## Sources

- **Carbon** — [NESO carbon intensity](https://api.carbonintensity.org.uk),
  GB, keyless.
- **Power, GB** — [Octopus Agile](https://api.octopus.energy) day-ahead unit
  rates, GB region C, keyless.
- **Power, US** — CAISO SP15 and ERCOT Houston day-ahead hourly, via
  [gridstatus](https://github.com/gridstatus/gridstatus), keyless.
- **Carbon, US** — [EIA-930](https://www.eia.gov/electricity/gridmonitor/)
  hourly generation by fuel for the CAISO and ERCOT balancing authorities,
  through the EIA Hourly Grid Monitor. Needs a free API key; without one the
  column records itself unavailable and nothing else changes.
- **Tokens** — the published price sheets, not a measurement:
  [OpenAI](https://developers.openai.com/api/docs/pricing) and
  [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing).

All are public and free. The board costs nothing to run and spends nothing at
any venue.

## Honest limits

- **Four zones, unevenly covered.** GB carbon and GB power are half-hourly and
  complete. CAISO SP15 and ERCOT Houston are **day-ahead hourly** prices, not
  settled real-time ones.
- **US carbon is derived, GB carbon is measured.** NESO publishes an intensity;
  EIA does not. The US columns are computed from EIA-930's hourly generation
  mix times EIA's own CO2 coefficients and fleet heat rates — every input
  published, the product an estimate, and marked `"basis": "derived"` in the
  record so it is never confused with a measurement. It counts generation, not
  consumption: imports and the carbon already stored in a battery are outside
  what the method can see, and the share of generation EIA files under "other"
  is reported per night rather than averaged in.
- **EIA runs about a day behind.** The 06:30Z mark usually lands before EIA has
  published the night it is marking, so the US carbon columns are often empty
  at first sight and fill in on a later re-mark. A column that is not there yet
  is recorded as unavailable, never as zero.
- **The token column is published, not observed.** The 2.0x and the 4x are read
  off price sheets; only the grid columns are measurements. A published number
  and a marked one are different kinds of claim, and the board should not blur
  them.
- **Observation, not advice.** The board records what the grid did. It does not
  forecast, and `offpeak` does not currently schedule against it — the SDK
  routes on published token prices alone.
- **Carbon actuals lag** roughly two hours. The 06:30Z mark clears the 00–05 BST
  trough comfortably; the tail of the night can still be sparse.
- **A dead source costs its column, not the run.** Legs degrade independently,
  and a night where both sources are down is recorded as unavailable rather than
  guessed at.
