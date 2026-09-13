# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 3 day(s) (2026-09-11 → 2026-09-13). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 16 | 94% | 0% | 4.4/58.4s | 4.0/4.4s | 35.0s | 0.67 | DEALER |
| gemini/gemini-3.5-flash | 16 | 100% | 0% | 2.0/7.6s | 2.0/2.2s | 7.6s | 0.50 | DEALER |
| gemini/gemini-3.5-flash-lite | 16 | 100% | 0% | 0.8/1.5s | 0.7/0.8s | 1.5s | 0.50 | DEALER |
| gemini/gemini-3.7-flash | 16 | 62% | 38% | 3.8/10.1s | 1.7/1.8s | 12.3s | 0.76 | OPEN |
| openai/gpt-5.6-luna | 16 | 94% | 0% | 3.0/3.8s | 2.9/3.4s | 4.1s | 0.56 | DEALER |
| openai/gpt-5.6-sol | 16 | 81% | 0% | 3.4/3.9s | 3.1/3.9s | 5.6s | 0.75 | OPEN |
| openai/gpt-5.6-terra | 16 | 100% | 0% | 2.7/3.1s | 2.9/3.3s | 3.1s | 0.50 | DEALER |
| **pooled** | 112 | 90% | 5% | 2.8/7.5s | 2.2/4.0s | 9.5s | 0.63 | DEALER |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.2530 USD.
