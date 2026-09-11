# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 1 day(s) (2026-09-11 → 2026-09-11). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 1 | 100% | 0% | 4.1/4.1s | 4.0/4.0s | 4.1s | 0.50 | DEALER |
| gemini/gemini-3.5-flash | 1 | 100% | 0% | 1.8/1.8s | 2.0/2.0s | 1.8s | 0.50 | DEALER |
| gemini/gemini-3.5-flash-lite | 1 | 100% | 0% | 0.8/0.8s | 0.7/0.7s | 0.8s | 0.51 | DEALER |
| gemini/gemini-3.7-flash | 1 | 0% | 100% | –/–s | 1.8/1.8s | 61.4s | 0.80 | CHECKBOX |
| openai/gpt-5.6-luna | 1 | 100% | 0% | 3.3/3.3s | 2.9/2.9s | 3.3s | 0.50 | DEALER |
| openai/gpt-5.6-sol | 1 | 0% | 0% | –/–s | 3.1/3.1s | 61.6s | 1.00 | CHECKBOX |
| openai/gpt-5.6-terra | 1 | 100% | 0% | 2.7/2.7s | 3.2/3.2s | 2.7s | 0.49 | DEALER |
| **pooled** | 7 | 71% | 14% | 2.7/4.0s | 2.9/3.8s | 61.5s | 0.62 | OPEN |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.0302 USD.
