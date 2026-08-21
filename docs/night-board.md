# The night board

`offpeak` rests on a claim: **intelligence has a time value.** The token side of
that claim is already settled — OpenAI and Anthropic both publish batch tiers at
50% of list, a flat **2.0x** spread for work that can wait.

The night board marks the same claim against the other side of the trade: the
grid the compute runs on. Every night it records what power and carbon actually
did between the evening peak and the small hours, from open, keyless sources.

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

## Sources

- **Carbon** — [NESO carbon intensity](https://api.carbonintensity.org.uk),
  GB, keyless.
- **Power** — [Octopus Agile](https://api.octopus.energy) day-ahead unit rates,
  GB region C, keyless.

Both are public and free. The board costs nothing to run and spends nothing at
any venue.

## Honest limits

- **GB only.** US zones (CAISO, ERCOT) are not wired up yet.
- **Observation, not advice.** The board records what the grid did. It does not
  forecast, and `offpeak` does not currently schedule against it — the SDK
  routes on published token prices alone.
- **Carbon actuals lag** roughly two hours. The 06:30Z mark clears the 00–05 BST
  trough comfortably; the tail of the night can still be sparse.
- **A dead source costs its column, not the run.** Legs degrade independently,
  and a night where both sources are down is recorded as unavailable rather than
  guessed at.
