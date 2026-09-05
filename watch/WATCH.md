# WATCH — provider sheet drift

`offpeak` ships a **dated snapshot** of numbers other people publish. This table
is the record of those pages moving underneath it.

Every row is a hash diff of one page's visible text against the reading
committed here the day before. The page text itself is committed alongside, in
`pages/`, so `git diff` on this branch shows the line that moved — this table
says *that* something moved; the diff says *what*.

**No number here has edited the price sheet.** Detection and resolution are
different jobs. A page can move for a dozen reasons that are not a price change,
so `tools/sheet_watch.py` never writes to `src/offpeak/prices.py`: a human reads
a row and settles what it meant.

The `classification` column is produced by an LLM job submitted **through
`offpeak` itself** — batch tier, cheapest model on the sheet whose key is
present, deadline before the 06:30Z mark — and is *advisory*. It is allowed to
be absent: rows publish whether or not it ran, and `unclassified` in that column
means the classifier did not answer, never that the page did not move.

## Sources

`rates visible` is how many price-like figures (`$0.75`) the last reading found
in the page's static text. A source at **0** renders its rates in the browser:
this watch still sees that page change, but it cannot see a rate change on it,
and no row about it should be read as rate coverage.

| source | why | rates visible | url |
| --- | --- | --- | --- |
| `anthropic:pricing` | cited by prices.py | 157 | <https://platform.claude.com/docs/en/about-claude/pricing> |
| `openai:pricing` | cited by prices.py | 220 | <https://developers.openai.com/api/docs/pricing> |
| `groq:pricing` | cited by prices.py | 1 | <https://groq.com/pricing> |
| `mistral:pricing` | cited by prices.py | 50 | <https://mistral.ai/pricing/api> |
| `google:pricing` | cited by prices.py | 522 | <https://ai.google.dev/pricing> |
| `groq:plans` | watched, not yet priced | **0 — rendered client-side** | <https://console.groq.com/docs/service-tiers> |
| `xai:pricing` | watched, not yet priced | 8 | <https://docs.x.ai/docs/models> |
| `deepseek:pricing` | cited by prices.py | 18 | <https://api-docs.deepseek.com/quick_start/pricing> |
| `qwen:pricing` | watched, not yet priced | **0 — rendered client-side** | <https://www.alibabacloud.com/help/en/model-studio/models> |

## Drift

| date (UTC) | source | status | lines | classification | classifier | cost | note |
| --- | --- | --- | --- | --- | --- | --- | --- |
<!-- rows appended below by tools/sheet_watch.py -->
| 2026-08-26 | `anthropic:pricing` | baseline | — | — | — | — | 28,298 chars recorded |
| 2026-08-26 | `openai:pricing` | baseline | — | — | — | — | 17,988 chars recorded |
| 2026-08-26 | `groq:pricing` | baseline | — | — | — | — | 885 chars recorded |
| 2026-08-26 | `mistral:pricing` | baseline | — | — | — | — | 10,250 chars recorded |
| 2026-08-26 | `google:pricing` | baseline | — | — | — | — | 58,981 chars recorded |
| 2026-08-26 | `groq:plans` | baseline | — | — | — | — | 4,426 chars recorded |
| 2026-08-26 | `xai:pricing` | baseline | — | — | — | — | 5,303 chars recorded |
| 2026-08-26 | `deepseek:pricing` | baseline | — | — | — | — | 2,747 chars recorded |
| 2026-08-26 | `qwen:pricing` | baseline | — | — | — | — | 28,645 chars recorded |
| 2026-08-27 | `anthropic:pricing` | changed | +8 / −0 | unclassified | — | — | fa208c529936 -> 537c5d18eb4c |
| 2026-08-27 | `openai:pricing` | changed | +5 / −0 | unclassified | — | — | d6a925dd5749 -> f103352cd90d |
| 2026-08-27 | `mistral:pricing` | changed | +2 / −1 | unclassified | — | — | ea1005b813fe -> c916c6693705 |
| 2026-08-27 | `google:pricing` | changed | +82 / −256 | unclassified | — | — | 5acfb597938b -> c5e78760d87f |
| 2026-08-28 | `openai:pricing` | changed | +0 / −2 | copy change | gpt-5.6-luna | $0.0000540 | Navigation links “Deep dive” and “Tools” were removed; no pricing numbers changed. |
| 2026-08-29 | `anthropic:pricing` | changed | +1 / −0 | unclassified | — | — | 537c5d18eb4c -> d775f55319a6 |
| 2026-08-29 | `openai:pricing` | changed | +7 / −6 | unclassified | — | — | 210b9c7d3674 -> 18761f53d324 |
| 2026-08-29 | `google:pricing` | changed | +1 / −1 | unclassified | — | — | c5e78760d87f -> 38aacfed0a79 |
| 2026-08-29 | `qwen:pricing` | changed | +14 / −702 | unclassified | — | — | e28982155951 -> 2433e55658a9 |
| 2026-08-30 | `openai:pricing` | changed | +6 / −1 | copy change | gpt-5.6-luna | $0.0000469 | Navigation and documentation links were added; no pricing figures or chargeable rates changed. |
| 2026-08-31 | `openai:pricing` | changed | +5 / −5 | copy change | gpt-5.6-luna | $0.0000559 | Navigation labels changed, with no pricing numbers or money-charged rates added, removed, or modified. |
| 2026-09-01 | `anthropic:pricing` | changed | +37 / −74 | noise | gpt-5.6-luna | $0.000255 | Only a formatting marker moved around headings; no pricing figures or substantive wording changed. |
| 2026-09-02 | `anthropic:pricing` | changed | +85 / −82 | price change | gpt-5.6-luna | $0.000144 | New models and cache pricing were added, including a reduced cache-hit rate for Fable 5.1 and Mythos 5.1. |
| 2026-09-02 | `openai:pricing` | changed | +2 / −0 | copy change | gpt-5.6-luna | $0.0000368 | Navigation items were added, but no pricing amounts or chargeable rates changed. |
| 2026-09-02 | `google:pricing` | changed | +7 / −1 | copy change | gpt-5.6-luna | $0.0000756 | Added explanatory notes about agentic video token usage; no pricing rates changed. |
| 2026-09-02 | `qwen:pricing` | changed | +1 / −1 | noise | gpt-5.6-luna | $0.0000399 | Only the page's “Last Updated” date changed; no pricing or substantive content changed. |
| 2026-09-03 | `google:pricing` | changed | +106 / −12 | unclassified | — | — | deadline 2026-09-03T07:28:54+00:00 is not before the 2026-09-03T06:30:00+00:00 board mark |
| 2026-09-03 | `xai:pricing` | changed | +4 / −2 | unclassified | — | — | deadline 2026-09-03T07:28:54+00:00 is not before the 2026-09-03T06:30:00+00:00 board mark |
| 2026-09-04 | `openai:pricing` | changed | +44 / −2 | price change | gpt-5.6-luna | $0.000104 | New GPT-6 Astra pricing tiers and regional processing surcharge details were added. |
| 2026-09-04 | `mistral:pricing` | changed | +10 / −12 | copy change | gpt-5.6-luna | $0.0000556 | Navigation labels and menu items changed, but no pricing values or billing rates were modified. |
| 2026-09-04 | `google:pricing` | changed | +18 / −3 | price change | gpt-5.6-luna | $0.0000929 | New Lyria 3.5 models add paid rates of $0.04 per song and $0.08 per song. |
| 2026-09-05 | `openai:pricing` | changed | +2 / −3 | copy change | gpt-5.6-luna | $0.0000701 | Navigation items and a rollout sentence changed, but no pricing amount or rate was added, removed, or altered. |
| 2026-09-05 | `mistral:pricing` | changed | +0 / −1 | copy change | gpt-5.6-luna | $0.0000355 | The “Select language” text was removed, changing page copy without altering any pricing. |
| 2026-09-05 | `google:pricing` | changed | +4 / −8 | price change | gpt-5.6-luna | $0.0000971 | The $0.04-per-song price for Lyria 3.5 Clip Preview was removed. |
| 2026-09-05 | `qwen:pricing` | changed | +1 / −1 | noise | gpt-5.6-luna | $0.0000393 | Only the page's last-updated date changed; no pricing or substantive content changed. |
