# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 4 day(s) (2026-09-11 → 2026-09-14). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 19 | 89% | 0% | 4.5/45.0s | 4.0/4.4s | 64.4s | 0.79 | OPEN |
| gemini/gemini-3.5-flash | 19 | 100% | 0% | 2.0/8.9s | 2.0/2.2s | 8.9s | 0.50 | DEALER |
| gemini/gemini-3.5-flash-lite | 19 | 100% | 0% | 0.8/3.7s | 0.7/0.8s | 3.7s | 0.50 | DEALER |
| gemini/gemini-3.7-flash | 19 | 63% | 37% | 3.9/16.7s | 1.7/1.8s | 13.3s | 0.78 | OPEN |
| openai/gpt-5.6-luna | 19 | 95% | 0% | 2.9/3.8s | 2.9/3.4s | 3.9s | 0.56 | DEALER |
| openai/gpt-5.6-sol | 19 | 84% | 0% | 3.4/7.6s | 3.1/3.9s | 7.1s | 0.75 | OPEN |
| openai/gpt-5.6-terra | 19 | 100% | 0% | 2.7/3.1s | 2.9/3.3s | 3.1s | 0.50 | DEALER |
| **pooled** | 133 | 90% | 5% | 2.8/12.3s | 2.2/4.0s | 12.6s | 0.66 | DEALER |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.2861 USD.
