# Qwen — Alibaba Model Studio batch

`qwen:batch` is Alibaba Model Studio's batch tier for the Qwen family: **50%
off** standard, OpenAI-shaped end to end, with two things the OpenAI venue
does not have — a region, and a completion window you choose.

!!! warning "Unverified live"
    No batch has yet been submitted through this driver, and there is no
    receipt in `receipts/` for `qwen:batch`. The request dialect, region base
    URLs, window validation and status mapping are exercised network-free
    against a fake client; the 50% spread is a published number here, not a
    settled one. Groq answered `403 not_available_for_plan`, Mistral `402
    enable billing`, and Gemini was gated until billing was switched on — the
    first sub-cent live run is what verifies a driver, and this one has not
    had it.

## Install and configure

```bash
pip install "offpeak[qwen]"        # an alias of the openai extra
export DASHSCOPE_API_KEY=sk-...    # or ALIBABA_API_KEY; both names are in use
```

The driver refuses to build a client without one of those keys rather than
letting the `openai` SDK fall back to `OPENAI_API_KEY`.

It is **opt-in** — not in `default_venues()`.

```python
import offpeak
from offpeak.venues import QwenBatch

jobs = [offpeak.job("qwen3.7-max", f"Summarize:\n\n{d}", max_tokens=512) for d in docs]
results = offpeak.run(
    jobs, deadline="2d", venues=[QwenBatch(region="intl", completion_window="48h")]
)
```

## Region

Model Studio is two deployments, priced and provisioned separately. A key is
issued for one and does not work at the other.

| `region` | Base URL |
| --- | --- |
| `"intl"` (default) | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` — Singapore |
| `"cn"` | `https://dashscope.aliyuncs.com/compatible-mode/v1` — Beijing |

The bundled sheet carries the **international** rows in USD. The Beijing
region is priced separately, in its own currency, and runs its promotions per
region, so a `region="cn"` run settles against a rate that is not that
region's. Override with `prices.register_price()` if you run there.

## The window

`completion_window` accepts an integer with an `h` or `d` unit anywhere in the
documented **24h–336h** range — `"24h"`, `"72h"`, `"14d"`. The driver refuses
anything outside it before the upload, where the venue would refuse it after.

The price does not vary with the window: the docs publish one batch rate and
one window range and say nothing about the two interacting. That is the same
term structure Groq publishes — a longer window buys completion probability,
not price — and it is read the same way here. If Alibaba ever prices the
curve, the driver's docstring says where that assumption lives.

## What is on the sheet

| Model | Standard | Batch (50%) |
| --- | --- | --- |
| `qwen3.7-max` | $2.50 / $7.50 | $1.25 / $3.75 |
| `qwen3.8-max` | $2.00 / $6.00 | $1.00 / $3.00 |

USD per 1M tokens, input / output, international region, from
[alibabacloud.com/help/en/model-studio/model-pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing)
— `qwen3.7-max` first read 2026-08-21 and confirmed 2026-08-30, `qwen3.8-max`
read 2026-08-30. The page marks `qwen3.7-max` "Limited-time 50% off" with no
date it runs through; a `PromoNote` needs one, so there is none, and the number
may step up unannounced.

Only the flagship rows are carried. The plus and flash families are tiered by
context length and priced differently in thinking and non-thinking mode, and
the sheet has neither dimension — a single number for them would be wrong for
most requests, so they resolve to `None` rather than a guess.

## What was checked, and what was not

Read from
[the batch interface docs](https://www.alibabacloud.com/help/en/model-studio/batch-interfaces-compatible-with-openai/)
on 2026-08-30: batch "costs are only 50% of real-time calls"; files upload with
`purpose="batch"`; the batch is created with `endpoint` matching the `url` on
every JSONL line (`/v1/chat/completions` for text); the two base URLs above;
`completion_window` "Range: 24h-336h"; the key is `DASHSCOPE_API_KEY`.

Not checked: the Singapore model list beyond the four the batch page names
(`qwen-max`, `qwen-plus`, `qwen-flash`, `qwen-turbo`), and whether the
versioned ids on the pricing page are batchable there. A batch on an
unsupported model fails at the venue, after the upload, and `run()` rescues it
through the sync fallback at list.

## Routing

`supports()` claims Model Studio's own spelling — `qwen-max`, `qwen3.7-max`,
`qwen-plus` — and deliberately **not** the `qwen/…` namespace, which is how
another catalogue (Groq's) spells the open-weight models it serves. A bare
`qwen` prefix would route a Groq-spelled id to Alibaba.

`max_tokens` is passed through as the caller spelled it; the
`max_completion_tokens` rewrite the OpenAI driver applies to its newer
families does not touch Qwen ids.
