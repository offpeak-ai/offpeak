"""Groq Batch venue — a term structure on the deadline.

.. warning::
   **UNTESTED LIVE.** Every line below is exercised by network-free tests only.
   No request has ever been made against Groq's API from this code. Treat it as
   a skeleton to verify against a real key, not as a supported venue.

Groq's batch API is OpenAI-shaped — ``/openai/v1/files`` and
``/openai/v1/batches``, the same JSONL request format, the same batch status
vocabulary — so this driver is the OpenAI one with a different client and one
extra dimension.

That dimension is the interesting part. OpenAI and Anthropic publish a single
24h completion window; Groq publishes a **range**, ``"24h"`` through ``"7d"``,
and recommends the longest window you can tolerate for a better chance of
completing rather than expiring under load. That is a term structure on
patience: the deadline stops being one bit (can this wait?) and becomes a
number that buys you something.

The discount does not vary with the window today — it is 50% off standard
across the range, the same as the other venues. What a longer window buys is
completion probability, not price. If Groq ever prices the curve, this is
where that would land.

Requires the ``groq`` extra: ``pip install "offpeak[groq]"``.
"""

from __future__ import annotations

from ..job import Job
from .openai_batch import OpenAIBatch

__all__ = ["GroqBatch", "COMPLETION_WINDOWS", "window_for_seconds"]

# Published range, shortest first. Groq accepts 24h through 7d.
COMPLETION_WINDOWS: tuple[str, ...] = ("24h", "48h", "72h", "96h", "120h", "144h", "7d")

_WINDOW_SECONDS: dict[str, int] = {
    "24h": 24 * 3600,
    "48h": 48 * 3600,
    "72h": 72 * 3600,
    "96h": 96 * 3600,
    "120h": 120 * 3600,
    "144h": 144 * 3600,
    "7d": 7 * 24 * 3600,
}

# Groq hosts open-weight families. Deliberately excludes the ``gpt-`` prefix:
# OpenAI's venue claims that, and a job should not silently change provider
# because two venues both answer to the same model name.
_MODEL_PREFIXES = (
    "llama",
    "meta-llama/",
    "mixtral",
    "gemma",
    "qwen",
    "deepseek",
    "kimi",
    "moonshotai/",
    "allam",
    "compound",
    "whisper",
    "groq/",
)


def window_for_seconds(seconds: float) -> str:
    """The longest published window that still fits inside *seconds*.

    Groq recommends the longest window you can afford, so this reaches for the
    top of the curve rather than the bottom. A deadline shorter than the
    shortest published window still returns ``"24h"`` — the batch may not land,
    which is exactly the case ``offpeak``'s sync fallback exists to cover.
    """
    fitting = [w for w in COMPLETION_WINDOWS if _WINDOW_SECONDS[w] <= seconds]
    return fitting[-1] if fitting else COMPLETION_WINDOWS[0]


class GroqBatch(OpenAIBatch):
    """Groq's batch tier. **Untested against the live API.**

    ``completion_window`` selects a point on the term structure; see
    :func:`window_for_seconds` to derive one from a deadline.
    """

    name = "groq:batch"

    def __init__(self, client: object | None = None, *, completion_window: str = "24h"):
        if completion_window not in _WINDOW_SECONDS:
            raise ValueError(
                f"completion_window must be one of {COMPLETION_WINDOWS}, got {completion_window!r}"
            )
        super().__init__(client=client)
        self.completion_window = completion_window

    @property
    def client(self):
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    'Groq venue requires the groq SDK: pip install "offpeak[groq]"'
                ) from exc
            self._client = Groq()
        return self._client

    def supports(self, model: str) -> bool:
        return model.startswith(_MODEL_PREFIXES)

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
