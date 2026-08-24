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
