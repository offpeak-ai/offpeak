# Offpeak settled runs — real money

Every row here is a run that executed and billed: list price, price paid, and the spread captured, as arithmetic against the price sheet named in the row. This is the ledger `BOARD.md` is not — that one marks open grid data and spends nothing at any venue.

**Scale is on every row on purpose.** A mechanics proof and a production book are both real settlements and are not the same evidence. Read the scale column before the money column.

Written by `tools/settle_report.py` from the receipts in the main branch's `receipts/`, never by hand. Those receipts come from `tools/mechanics_run.py`, which is in the tree for the same reason: a number you cannot reproduce is a number you are asked to trust.

| run | scale | jobs | venues | tokens | list | paid | captured | SLA |
|---|---|---|---|---|---|---|---|---|
| 2026-08-22-mechanics-1 | mechanics proof (48 jobs, two venues) | 48 | anthropic:batch 24 · openai:batch 24 | 787 in · 155 out | $0.00156 | $0.000781 | $0.000781 (50.0%) | 24/48 |
| 2026-08-22-mechanics-2 | mechanics proof (24 jobs, OpenAI, ceiling too low) | 24 | openai:batch 24 | 728 in · 374 out | $0.000594 | $0.000297 | $0.000297 (50.0%) | 24/24 |
| 2026-08-22-mechanics-3 | mechanics proof (24 jobs, OpenAI, ceiling sized to the model) | 24 | openai:batch 24 | 728 in · 971 out | $0.00131 | $0.000655 | $0.000655 (50.0%) | 24/24 |
| 2026-08-23-groq-1 | venue probe (24 jobs, Groq, sync fallback only — batch tier 403) | 24 | groq:batch 24 | 2,288 in · 7,307 out | $0.00236 | $0.00236 | $0.00 (0.0%) | 24/24 (24 fell back) |
| 2026-08-24-gemini-1 | venue proof (5 jobs, Gemini, batch tier — first third venue to capture) | 5 | gemini:batch 5 | 124 in · 2,427 out | $0.01 | $0.00460 | $0.00460 (50.0%) | 5/5 |
| 2026-08-24-mistral-1 | venue probe (24 jobs, Mistral, sync fallback only — batch tier 402) | 24 | mistral:batch 24 | 948 in · 94 out | $0.000199 | $0.000199 | $0.00 (0.0%) | 24/24 (24 fell back) |
| 2026-08-26-anthropic-1 | venue proof (6 jobs, Anthropic, batch tier) | 6 | anthropic:batch 6 | 188 in · 38 out | $0.000378 | $0.000189 | $0.000189 (50.0%) | 6/6 |
| 2026-08-26-gemini-1 | venue proof (5 jobs, Gemini, batch tier) | 5 | gemini:batch 5 | 124 in · 2,422 out | $0.01 | $0.00459 | $0.00459 (50.0%) | 5/5 |
| 2026-08-26-mistral-2 | venue probe (24 jobs, Mistral, batch completed but was discarded — driver bug, sync fallback at list) | 24 | mistral:batch 24 | 948 in · 93 out | $0.000198 | $0.000198 | $0.00 (0.0%) | 24/24 (24 fell back) |
| 2026-08-26-mistral-3 | venue proof (24 jobs, Mistral, batch tier — fourth venue to capture) | 24 | mistral:batch 24 | 948 in · 96 out | $0.000200 | $0.0000999 | $0.0000999 (50.0%) | 24/24 |
| 2026-08-26-openai-1 | venue proof (24 jobs, OpenAI, batch tier) | 24 | openai:batch 24 | 728 in · 938 out | $0.00127 | $0.000636 | $0.000636 (50.0%) | 24/24 |
