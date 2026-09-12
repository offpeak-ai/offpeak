# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 2 day(s) (2026-09-11 → 2026-09-12). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 9 | 89% | 0% | 4.3/105.4s | 4.2/4.5s | 49.3s | 0.84 | OPEN |
| gemini/gemini-3.5-flash | 9 | 100% | 0% | 1.9/6.3s | 1.9/2.0s | 6.3s | 0.50 | DEALER |
| gemini/gemini-3.5-flash-lite | 9 | 100% | 0% | 0.7/1.0s | 0.7/0.8s | 1.0s | 0.51 | DEALER |
| gemini/gemini-3.7-flash | 9 | 44% | 56% | 5.0/11.5s | 1.8/1.8s | 12.3s | 0.88 | CHECKBOX |
| openai/gpt-5.6-luna | 9 | 89% | 0% | 3.2/3.8s | 2.9/2.9s | 4.5s | 0.66 | DEALER |
| openai/gpt-5.6-sol | 9 | 67% | 0% | 3.4/4.0s | 3.0/3.1s | 5.8s | 1.00 | CHECKBOX |
| openai/gpt-5.6-terra | 9 | 100% | 0% | 2.7/3.0s | 3.2/3.3s | 3.0s | 0.50 | DEALER |
| **pooled** | 63 | 84% | 8% | 2.8/9.9s | 2.9/4.2s | 12.1s | 0.75 | OPEN |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.1335 USD.
