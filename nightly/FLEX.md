# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 5 day(s) (2026-09-11 → 2026-09-15). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 24 | 75% | 8% | 4.5/92.9s | 4.0/4.4s | 65.4s | 0.96 | CHECKBOX |
| gemini/gemini-3.5-flash | 24 | 96% | 4% | 2.0/8.5s | 2.0/2.2s | 10.8s | 0.58 | DEALER |
| gemini/gemini-3.5-flash-lite | 24 | 100% | 0% | 0.8/2.4s | 0.7/0.8s | 2.4s | 0.50 | DEALER |
| gemini/gemini-3.7-flash | 24 | 58% | 42% | 4.2/23.0s | 1.7/1.8s | 20.7s | 0.83 | OPEN |
| openai/gpt-5.6-luna | 24 | 96% | 0% | 2.9/4.4s | 2.9/3.4s | 4.6s | 0.56 | DEALER |
| openai/gpt-5.6-sol | 24 | 79% | 0% | 3.4/9.2s | 3.1/3.9s | 8.1s | 0.81 | OPEN |
| openai/gpt-5.6-terra | 24 | 100% | 0% | 2.7/3.7s | 2.9/3.3s | 3.7s | 0.50 | DEALER |
| **pooled** | 168 | 86% | 8% | 2.8/12.7s | 2.2/4.0s | 18.4s | 0.74 | OPEN |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.3561 USD.
