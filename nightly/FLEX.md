# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 3 day(s) (2026-09-11 → 2026-09-13). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 13 | 92% | 0% | 4.5/78.6s | 4.0/4.4s | 41.2s | 0.75 | OPEN |
| gemini/gemini-3.5-flash | 13 | 100% | 0% | 1.9/5.4s | 2.0/2.1s | 5.4s | 0.50 | DEALER |
| gemini/gemini-3.5-flash-lite | 13 | 100% | 0% | 0.8/1.1s | 0.7/0.8s | 1.1s | 0.51 | DEALER |
| gemini/gemini-3.7-flash | 13 | 62% | 38% | 2.8/10.6s | 1.7/1.8s | 12.3s | 0.80 | OPEN |
| openai/gpt-5.6-luna | 13 | 92% | 0% | 3.0/3.8s | 2.9/2.9s | 4.3s | 0.62 | DEALER |
| openai/gpt-5.6-sol | 13 | 77% | 0% | 3.4/3.9s | 3.0/3.1s | 5.7s | 0.88 | CHECKBOX |
| openai/gpt-5.6-terra | 13 | 100% | 0% | 2.7/3.1s | 3.2/3.3s | 3.1s | 0.49 | DEALER |
| **pooled** | 91 | 89% | 5% | 2.7/8.6s | 2.1/4.0s | 11.3s | 0.68 | DEALER |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.1879 USD.
