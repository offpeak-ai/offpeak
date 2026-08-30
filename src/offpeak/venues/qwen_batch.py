"""Qwen on Alibaba Model Studio — an OpenAI-shaped batch tier with a region.

.. warning::
   **Unverified live.** No batch has yet been submitted through this driver:
   there is no receipt in ``receipts/`` for ``qwen:batch``, and until there
   is, the 50% spread it exists to capture is a published number here rather
   than a settled one. The plan-gating lesson is the reason that matters —
   Groq answered ``403 not_available_for_plan``, Mistral ``402 enable
   billing``, and Gemini was gated until billing was switched on — and the
   first sub-cent live run is what verifies a driver, not the docs. What is
   exercised network-free: the request dialect, the region base URLs, the
   window validation and the status mapping, all against a fake client.

What was and was not verified from the documentation, and when:

* **Verified 2026-08-30** by reading
  alibabacloud.com/help/en/model-studio/batch-interfaces-compatible-with-openai:
  batch "costs are only 50% of real-time calls"; files are uploaded with
  ``purpose="batch"``; the batch is created with ``endpoint`` matching the
  ``url`` on every JSONL line, ``/v1/chat/completions`` for text; the
  Singapore (international) base URL is
  ``https://dashscope-intl.aliyuncs.com/compatible-mode/v1`` and the Beijing
  (China) one ``https://dashscope.aliyuncs.com/compatible-mode/v1``;
  ``completion_window`` takes an integer with an ``h`` or ``d`` unit in the
  range **24h–336h** (14 days); the key is read from ``DASHSCOPE_API_KEY``.
* **Verified 2026-08-30** by reading
  alibabacloud.com/help/en/model-studio/model-pricing, which rendered on this
  read (it is client-side rendered and may not on another): the international
  rows the sheet carries, and that the batch discount is 50% on both input and
  output for them. The same page prices the Beijing region separately, in a
  different currency, and runs its promotions per region — the sheet holds
  the **international** row only, so a ``region="cn"`` run settles against a
  rate that is not that region's. See :mod:`offpeak.prices`.
* **Not verified**: that the price is constant across the window. The docs
  publish one batch rate and one window range and say nothing about the two
  interacting, which is read here as "the window is free" — the same term
  structure Groq publishes, where a longer window buys completion probability
  and not price. If Alibaba ever prices the curve, this docstring is wrong
  and :data:`~offpeak.prices.BATCH_DISCOUNT` stops covering it.
* **Not verified**: the Singapore model list beyond the four the batch page
  names (``qwen-max``, ``qwen-plus``, ``qwen-flash``, ``qwen-turbo``), and
  whether the versioned ids the pricing page lists are batchable there. A
  batch on an unsupported model fails at the venue, after the upload.

The API is OpenAI-shaped end to end — ``/v1/files``, ``/v1/batches``, the
same JSONL in and out, the same status vocabulary — so this driver is
:class:`~offpeak.venues.openai_batch.OpenAIBatch` with a different client, a
region, and a window, exactly the way :class:`~offpeak.venues.groq_batch.GroqBatch`
is. The JSONL builder and the output parser are reused, not copied.

``max_tokens`` is passed through as the caller spelled it. Qwen ids do not
match the OpenAI driver's ``max_completion_tokens`` rewrite table, so the
inherited :func:`~offpeak.venues.openai_batch.body_params` leaves them alone.

Requires the ``qwen`` extra (an alias of ``openai``):
``pip install "offpeak[qwen]"``.
"""

from __future__ import annotations

import os
import re

from ..job import Job
from .openai_batch import OpenAIBatch

__all__ = [
    "QwenBatch",
    "REGIONS",
    "MIN_WINDOW_HOURS",
    "MAX_WINDOW_HOURS",
    "window_hours",
]

#: Region -> base URL. Two deployments, priced and provisioned separately;
#: a key is issued for one and does not work at the other.
REGIONS: dict[str, str] = {
    "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}

#: The documented ``completion_window`` range, in hours: "Range: 24h-336h".
MIN_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 336

#: Both names are in use for the same key. ``DASHSCOPE_API_KEY`` is the one
#: Alibaba's own SDKs and docs read; ``ALIBABA_API_KEY`` is what a good deal of
#: third-party tooling settled on. Checked in that order.
_KEY_ENV = ("DASHSCOPE_API_KEY", "ALIBABA_API_KEY")

# ``qwen`` unprefixed — ``qwen-max``, ``qwen3.7-max``, ``qwen-plus`` — is Model
# Studio's own spelling. ``qwen/…`` is a namespace another catalogue (Groq's)
# uses for the open-weight models it serves, and a bare ``qwen`` prefix would
# claim those too and route a Groq-spelled id to a venue that has never heard
# of it. The namespace is excluded by name.
_MODEL_PREFIX = "qwen"
_FOREIGN_NAMESPACE = "qwen/"

_WINDOW = re.compile(r"^(\d+)([hd])$")


def window_hours(window: str) -> int:
    """*window* as a number of hours, validated against the documented range.

    Accepts the two spellings the docs accept — ``"24h"``, ``"14d"`` — and
    refuses anything outside 24h–336h before it reaches the venue, where the
    refusal would arrive after the upload.
    """
    match = _WINDOW.match(str(window).strip())
    if match is None:
        raise ValueError(
            f"completion_window must be an integer with an 'h' or 'd' unit "
            f'("24h", "14d"), got {window!r}'
        )
    amount, unit = int(match.group(1)), match.group(2)
    hours = amount * 24 if unit == "d" else amount
    if not MIN_WINDOW_HOURS <= hours <= MAX_WINDOW_HOURS:
        raise ValueError(
            f"completion_window must be between {MIN_WINDOW_HOURS}h and "
            f"{MAX_WINDOW_HOURS}h (14d), got {window!r} ({hours}h)"
        )
    return hours


class QwenBatch(OpenAIBatch):
    """Alibaba Model Studio's batch tier for the Qwen family. **Unverified
    live; see the module warning.**

    *region* is ``"intl"`` (Singapore, the default) or ``"cn"`` (Beijing).
    *completion_window* is any value in the documented 24h–336h range; the
    price does not vary with it, so a longer window is free patience.
    """

    name = "qwen:batch"

    def __init__(
        self,
        client: object | None = None,
        *,
        region: str = "intl",
        completion_window: str = "24h",
    ):
        if region not in REGIONS:
            raise ValueError(f"region must be one of {sorted(REGIONS)}, got {region!r}")
        window_hours(completion_window)
        super().__init__(client=client)
        self.region = region
        self.base_url = REGIONS[region]
        self.completion_window = completion_window

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    'Qwen venue requires the openai SDK: pip install "offpeak[qwen]"'
                ) from exc
            key = next((os.environ[n] for n in _KEY_ENV if os.environ.get(n)), None)
            if not key:
                # Handed None, the OpenAI client would read OPENAI_API_KEY and
                # send an OpenAI key to Alibaba. Refuse and name the variables.
                raise RuntimeError(
                    "Qwen venue needs DASHSCOPE_API_KEY (or ALIBABA_API_KEY) in the environment"
                )
            self._client = OpenAI(api_key=key, base_url=self.base_url)
        return self._client

    def supports(self, model: str) -> bool:
        return model.startswith(_MODEL_PREFIX) and not model.startswith(_FOREIGN_NAMESPACE)

    def submit(self, jobs: list[Job]) -> str:
        from .openai_batch import _ENDPOINT, build_jsonl

        upload = self.client.files.create(
            file=("offpeak_batch.jsonl", build_jsonl(jobs)), purpose="batch"
        )
        batch = self.client.batches.create(
            input_file_id=upload.id,
            endpoint=_ENDPOINT,
            completion_window=self.completion_window,
        )
        return batch.id
