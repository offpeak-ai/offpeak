# Flex lanes — the dealer back-test

Daily flex probes across 7 lanes, 4 day(s) (2026-09-11 → 2026-09-14). Rescue fires at 60s. Percentiles and counts only; rows stay private.

| lane | attempts | fill@60s | shed | flex p50/p95 | ctrl p50/p95 | ladder p95 | blended | verdict |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| gemini/gemini-3.1-pro-preview | 21 | 81% | 10% | 4.5/45.0s | 4.0/4.4s | 64.3s | 0.84 | OPEN |
| gemini/gemini-3.5-flash | 21 | 95% | 5% | 2.0/8.8s | 2.0/2.2s | 11.1s | 0.58 | DEALER |
| gemini/gemini-3.5-flash-lite | 21 | 100% | 0% | 0.8/2.6s | 0.7/0.8s | 2.6s | 0.50 | DEALER |
| gemini/gemini-3.7-flash | 21 | 57% | 43% | 3.9/16.7s | 1.7/1.8s | 12.3s | 0.82 | OPEN |
| openai/gpt-5.6-luna | 21 | 95% | 0% | 3.0/4.3s | 2.9/3.4s | 4.6s | 0.56 | DEALER |
| openai/gpt-5.6-sol | 21 | 81% | 0% | 3.4/10.2s | 3.1/3.9s | 8.2s | 0.78 | OPEN |
| openai/gpt-5.6-terra | 21 | 100% | 0% | 2.7/3.2s | 2.9/3.3s | 3.2s | 0.50 | DEALER |
| **pooled** | 147 | 87% | 8% | 2.8/11.8s | 2.2/4.0s | 13.6s | 0.70 | DEALER |

Verdict rule (pre-committed): DEALER when fill@60s ≥ 80%, blended ≤ 0.70 and ladder p95 ≤ 2× budget; CHECKBOX when fill@60s < 50% or blended ≥ 0.85; OPEN otherwise. Spend to date 0.3156 USD.
