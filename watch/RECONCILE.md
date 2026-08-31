# RECONCILE — sheet against page

`tools/sheet_watch.py` asks whether a page moved since yesterday. This asks
whether the page and `src/offpeak/prices.py` agree **today** — which is the only
question that catches a row that was already wrong when the watch took its first
reading, because no hash diff spans a baseline.

Rates are parsed out of the page text committed in `pages/`, so every figure
below is reproducible from this branch with no network at all.

**No number here has edited the price sheet**, and nothing in CI may. Same rule
as the watch, for the same reason: a parser confident enough to rewrite
`prices.py` from scraped text would eventually launder a table-layout change
into a receipt.

`classification` is the sheet watch's most recent LLM label for that source —
what moved, per the classifier that already ran. It answers a different question
than the rows below it and is here so a reader has both at once.

Reconciled 2026-08-31 against `offpeak.prices` sheet **2026-08-30**.

| source | status | mismatches | missing | unverifiable | models on page | classification |
| --- | --- | --- | --- | --- | --- | --- |
| `anthropic` | ok | 0 | 0 | 16 | 15 | unclassified (2026-08-29) |
| `google` | **drift** | 10 | 0 | 0 | 31 | unclassified (2026-08-29) |
| `groq` | skipped | 0 | 0 | 1 | 0 | — |
| `mistral` | ok | 0 | 1 | 37 | 17 | unclassified (2026-08-27) |
| `openai` | ok | 0 | 0 | 0 | 5 | copy change (2026-08-31) |

## `anthropic`

Sheet watch's latest classification: **unclassified (2026-08-29)**.

### Unverifiable (16)

Not compared, and not counted as agreement.

- no figure for this field in the page text — 16 field(s)

### On the page, not on the sheet (5)

Informational. The sheet omits models on purpose; see the comments in `prices.py` before adding one.

`claude-haiku-3-5`, `claude-mythos-5`, `claude-opus-4`, `claude-opus-4-1`, `claude-sonnet-4`

## `google`

Sheet watch's latest classification: **unclassified (2026-08-29)**.

### Mismatches

| model | field | page | sheet | note |
| --- | --- | --- | --- | --- |
| `gemini-3.1-pro-preview` | fast_input | $3.60 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.1-pro-preview` | fast_output | $21.60 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.5-flash` | fast_input | $2.70 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.5-flash` | fast_output | $16.20 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.5-flash-lite` | fast_input | $0.54 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.5-flash-lite` | fast_output | $4.50 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.6-flash` | fast_input | $1.35 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.6-flash` | fast_output | $6.75 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.7-flash` | fast_input | $1.35 | — | the page publishes this tier; the sheet carries no row for it |
| `gemini-3.7-flash` | fast_output | $6.75 | — | the page publishes this tier; the sheet carries no row for it |

### On the page, not on the sheet (25)

Informational. The sheet omits models on purpose; see the comments in `prices.py` before adding one.

`gemini-2.5-computer-use-preview-10-2025`, `gemini-2.5-flash`, `gemini-2.5-flash-image`, `gemini-2.5-flash-lite`, `gemini-2.5-flash-native-audio-preview-12-2025`, `gemini-2.5-flash-preview-tts`, `gemini-2.5-pro`, `gemini-2.5-pro-preview-tts`, `gemini-3-flash-preview`, `gemini-3-pro-image`, `gemini-3.1-flash-image`, `gemini-3.1-flash-lite`, `gemini-3.1-flash-lite-image`, `gemini-3.1-flash-live-preview`, `gemini-3.1-flash-tts-preview`, `gemini-3.1-pro-preview-customtools`, `gemini-3.5-live-translate-preview`, `gemini-3.5-transcribe`, `gemini-3.5-transcribe-live`, `gemini-embedding-001`, `gemini-omni-1.1-flash`, `gemini-omni-flash-preview`, `gemini-robotics-er-1.6-preview`, `gemini-robotics-er-2-preview`, `gemini-robotics-er-2-streaming-preview`

## `groq`

### Unverifiable (1)

Not compared, and not counted as agreement.

- groq.com/pricing renders its rate table client-side — the committed page text holds no per-model rates to reconcile against — 1 field(s)

## `mistral`

Sheet watch's latest classification: **unclassified (2026-08-27)**.

### Missing from the page

- `glm-5-2` — on the sheet, no row found on the page

### Unverifiable (37)

Not compared, and not counted as agreement.

- batch is an account-level toggle (-50%), not a per-model row — 18 field(s)
- priority is an account-level toggle with no published rate — 18 field(s)
- no figure for this field in the page text — 1 field(s)

### On the page, not on the sheet (2)

Informational. The sheet omits models on purpose; see the comments in `prices.py` before adding one.

`codestral-embed`, `voxtral-small-latest`

## `openai`

Sheet watch's latest classification: **copy change (2026-08-31)**.

### On the page, not on the sheet (2)

Informational. The sheet omits models on purpose; see the comments in `prices.py` before adding one.

`chat-latest`, `gpt-5.3-codex`
