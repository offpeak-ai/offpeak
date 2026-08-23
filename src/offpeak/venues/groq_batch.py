"""Groq Batch venue — a term structure on the deadline.

.. warning::
   **The batch tier is unverified, and not because of this code.** Groq gates
   its whole Batch API behind a plan: on 2026-08-22 a live key answered
   ``403 {"code": "not_available_for_plan"}`` to ``POST /openai/v1/files``
   with ``purpose="batch"`` and to ``GET /openai/v1/batches`` alike, so
   :meth:`GroqBatch.submit` cannot complete its first call. Until an entitled
   key runs one, the 50% spread this venue exists to capture is a published
   number here rather than a settled one.

   What *has* been exercised against the live API on 2026-08-22: the model
   catalogue behind :data:`_MODEL_PREFIXES` (``GET /openai/v1/models``), the
   request dialect, the price rows, and the synchronous fallback path — 24 real
   jobs, settled, in ``receipts/2026-08-23-groq-1.json``. Every one of them
   fell back to sync and paid list, which is exactly what the receipt says.

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

__all__ = [
    "GroqBatch",
    "COMPLETION_WINDOWS",
    "window_for_seconds",
    "MAX_JSONL_LINES",
    "MAX_INPUT_BYTES",
]

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

# What this driver claims it can batch through ``/v1/chat/completions``.
#
# Audited 2026-08-22 against Groq's live model list (``GET /openai/v1/models``)
# and its deprecations page. **Groq's batch documentation page is stale** — it
# still lists the Llama models below as batchable, and they have been shut off.
# Where the two disagree, the deprecation notice wins and the live list settles
# it.
#
# Retired, and gone rather than merely unfashionable:
#
#   ``llama-3.3-70b-versatile``, ``llama-3.1-8b-instant``   shut down 2026-08-16
#   ``qwen/qwen3-32b``, ``meta-llama/llama-4-scout``        shut down 2026-07-17
#   ``mixtral-*``, ``gemma2-*``, the DeepSeek distills      long gone
#   ``kimi`` / ``moonshotai/``, ``allam``                   off the chat lineup
#   ``compound-beta*``                                      renamed ``groq/compound``
#
# ``openai/gpt-oss`` is spelled out in full rather than reached for with a bare
# ``openai/``. The namespace belongs to Groq's catalogue, not to OpenAI's venue,
# and claiming all of it would silently poach every future OpenAI-authored model
# that lands there — the same mistake as claiming ``gpt-``, one level deeper.
_MODEL_PREFIXES = (
    "openai/gpt-oss",  # gpt-oss-120b, gpt-oss-20b, gpt-oss-safeguard-20b
    "groq/",  # groq/compound, groq/compound-mini
)

# ``qwen`` is deliberately not here, and it is the one judgement call in the
# list. The live model endpoint does serve ``qwen/qwen3.6-27b`` — a successor to
# the ``qwen3-32b`` that died on 2026-07-17 — but it is not on the production
# chat lineup, and a bare ``qwen`` prefix would claim the dead 32b alongside the
# live one. Add the exact live spelling here if you want it, deliberately.

# Audio models are deliberately absent. Whisper is live on Groq and this driver
# still cannot run it: :func:`~offpeak.venues.openai_batch.build_jsonl` writes
# ``/v1/chat/completions`` with a ``messages`` body, and transcription wants
# ``/v1/audio/transcriptions`` with a file. Claiming a model the request shape
# cannot serve buys a batch of 400s hours after submission.

# Groq's published per-batch limits. Checked before upload rather than
# discovered at the venue: a rejected 200MB upload costs the wall-clock of
# sending it, and a rejected batch costs the deadline it was submitted against.
MAX_JSONL_LINES = 50_000
MAX_INPUT_BYTES = 200 * 1024 * 1024


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
    """Groq's batch tier. **Batch is plan-gated; see the module warning.**

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

        payload = build_jsonl(jobs)
        self._check_limits(jobs, payload)
        upload = self.client.files.create(
            file=("offpeak_batch.jsonl", payload), purpose="batch"
        )
        batch = self.client.batches.create(
            input_file_id=upload.id,
            endpoint=_ENDPOINT,
            completion_window=self.completion_window,
        )
        return batch.id

    @staticmethod
    def _check_limits(jobs: list[Job], payload: bytes) -> None:
        """Refuse a batch Groq would refuse, before spending the upload on it.

        Both limits are the venue's published ones. Raising here rather than
        letting the venue answer is the whole point: a rejection at the far end
        arrives after the file has crossed the wire, and the caller learns that
        their deadline is unmet at the moment the deadline is closest.
        """
        if len(jobs) > MAX_JSONL_LINES:
            raise ValueError(
                f"Groq batches take at most {MAX_JSONL_LINES:,} requests; "
                f"got {len(jobs):,}. Split the book and submit it as several "
                f"batches."
            )
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError(
                f"Groq batch input files are capped at {MAX_INPUT_BYTES:,} bytes "
                f"(200MB); this one renders to {len(payload):,}. Split the book "
                f"and submit it as several batches."
            )
