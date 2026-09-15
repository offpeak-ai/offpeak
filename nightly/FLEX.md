# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 5 day(s) (2026-09-11 → 2026-09-15). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 27 | 70% | 11% | 4.5/89.4s | 4.0/4.6s | 65.8s | 0.97 | CHECKBOX |
| gemini/gemini-3.5-flash | 27 | 96% | 4% | 2.0/10.5s | 2.0/2.2s | 24.1s | 0.56 | DEALER |
| gemini/gemini-3.5-flash-lite | 27 | 100% | 0% | 0.8/2.2s | 0.7/0.8s | 2.2s | 0.50 | DEALER |
| gemini/gemini-3.7-flash | 27 | 56% | 44% | 4.7/22.9s | 1.7/1.8s | 23.8s | 0.87 | CHECKBOX |
| openai/gpt-5.6-luna | 27 | 96% | 0% | 3.0/11.1s | 2.9/3.4s | 10.9s | 0.55 | DEALER |
| openai/gpt-5.6-sol | 27 | 70% | 0% | 3.4/9.2s | 3.3/4.0s | 7.9s | 0.86 | CHECKBOX |
| openai/gpt-5.6-terra | 27 | 96% | 0% | 2.7/3.7s | 3.0/3.3s | 4.1s | 0.57 | DEALER |
| **pooled** | 189 | 84% | 8% | 2.8/13.5s | 2.6/4.0s | 23.6s | 0.75 | OPEN |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.4362 USD.
