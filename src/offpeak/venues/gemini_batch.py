"""Google Gemini Batch venue (50% off list, 24h target turnaround).

Requires the ``gemini`` extra: ``pip install "offpeak[gemini]"``.

The first venue here that is **not** OpenAI-shaped. OpenAI, Groq and Mistral all
speak a JSONL file of chat-completion requests; Gemini speaks its own object
model, and the difference is not cosmetic:

* **Messages become contents.** ``[{"role": "user", "content": "..."}]`` becomes
  ``[{"role": "user", "parts": [{"text": "..."}]}]``. The assistant role is
  spelled ``model``, and a system message is not a message at all — it is
  ``system_instruction`` on the config. See :func:`to_contents`.
* **Params become a config.** ``max_tokens`` is ``max_output_tokens``,
  and the rest are renamed or dropped rather than passed through. See
  :func:`to_config`.
* **There is no custom_id.** Requests carry a free-form ``metadata`` dict and
  responses hand it back, so the job id rides in ``metadata["key"]`` — which is
  what makes results matchable at all.
* **Twelve job states**, against OpenAI's eight and Mistral's seven.

Settled, not asserted
---------------------

``receipts/2026-08-24-gemini-1.json`` — 5 jobs, batch tier, **50.0% captured,
zero sync fallbacks**, 2m36s end to end. The first venue outside OpenAI and
Anthropic here to capture the spread rather than record why it could not.

Reasoning is billed as output, and it is *not* optional
-------------------------------------------------------

``usageMetadata`` reports ``thoughtsTokenCount`` separately from
``candidatesTokenCount``, and both are output tokens on the bill.
:func:`usage_tokens` adds them, because a receipt that counted only the visible
answer would under-report what was charged.

It is also the ceiling trap again, and Gemini walks into it faster than most.
The first live batch run from this code set ``max_output_tokens=16``, spent
**13 tokens thinking**, and returned ``finishReason: MAX_TOKENS`` with an empty
``content`` — billed, no answer. Size a Gemini ceiling in the hundreds.
"""

from __future__ import annotations

from ..job import Job, Result
from .base import BatchState, Venue, iso_utc

__all__ = [
    "GeminiBatch",
    "to_contents",
    "to_config",
    "usage_tokens",
    "response_text",
]

_MODEL_PREFIXES = ("gemini-", "models/gemini-")

# Gemini's twelve job states, onto the four this library speaks.
_STATUS = {
    "JOB_STATE_UNSPECIFIED": "in_progress",
    "JOB_STATE_QUEUED": "in_progress",
    "JOB_STATE_PENDING": "in_progress",
    "JOB_STATE_RUNNING": "in_progress",
    "JOB_STATE_UPDATING": "in_progress",
    "JOB_STATE_PAUSED": "in_progress",
    "JOB_STATE_SUCCEEDED": "completed",
    # Some landed and some did not. The batch is over, and per-job errors are
    # already carried on the individual results, so this is a completion rather
    # than a failure — treating it as failure would discard the work that
    # succeeded.
    "JOB_STATE_PARTIALLY_SUCCEEDED": "completed",
    "JOB_STATE_FAILED": "failed",
    "JOB_STATE_EXPIRED": "failed",
    "JOB_STATE_CANCELLING": "cancelled",
    "JOB_STATE_CANCELLED": "cancelled",
}

#: Where a job's id travels, so a response can be matched back to its request.
_KEY = "key"

# OpenAI's role names on the left, Gemini's on the right. "system" is absent on
# purpose: it is not a role here, it is a field on the config.
_ROLES = {"user": "user", "assistant": "model", "model": "model"}

# Caller-facing param -> Gemini config field. Anything not here is dropped
# rather than forwarded: the config is a typed object and an unknown key is a
# hard error at request time, which on a batch means finding out hours later.
_CONFIG_KEYS = {
    "max_tokens": "max_output_tokens",
    "max_output_tokens": "max_output_tokens",
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "stop": "stop_sequences",
    "stop_sequences": "stop_sequences",
    "seed": "seed",
    "response_mime_type": "response_mime_type",
    "system_instruction": "system_instruction",
}


def _get(obj: object, name: str, default=None):
    """Read *name* off a dict or an SDK model, whichever this is.

    The SDK returns typed objects and the REST API returns dicts; the same
    parsing has to survive both, and both spellings of every field.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content blocks
        return "".join(
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return "" if content is None else str(content)


def to_contents(messages: list[dict]) -> tuple[list[dict], str | None]:
    """OpenAI-style *messages* as Gemini ``(contents, system_instruction)``.

    System messages are lifted out and joined, because Gemini has no system
    role — passing one through as a turn would put the instruction in the
    conversation instead of above it, and the model would answer it.
    """
    contents: list[dict] = []
    system: list[str] = []
    for m in messages:
        role = str(m.get("role", "user"))
        text = _text_of(m.get("content"))
        if role == "system":
            if text:
                system.append(text)
            continue
        contents.append({"role": _ROLES.get(role, "user"), "parts": [{"text": text}]})
    return contents, ("\n\n".join(system) if system else None)


def to_config(params: dict, system_instruction: str | None = None) -> dict:
    """A job's params as Gemini's generation config.

    Renamed where Gemini renames them, dropped where it has no equivalent. An
    unknown key is a hard error at request time here, and on a batch that means
    learning about it hours after submission — so unknown keys never leave.
    """
    config: dict = {}
    for name, value in params.items():
        target = _CONFIG_KEYS.get(name)
        if target:
            config[target] = value
    if system_instruction:
        config.setdefault("system_instruction", system_instruction)
    return config


def build_requests(jobs: list[Job]) -> list[dict]:
    """*jobs* as Gemini inlined batch requests, each carrying its id."""
    out = []
    for j in jobs:
        contents, system = to_contents(j.messages)
        out.append(
            {
                "contents": contents,
                "config": to_config(j.params, system),
                "metadata": {_KEY: j.id},
            }
        )
    return out


def usage_tokens(usage: object) -> tuple[int, int]:
    """``(input, output)`` from a Gemini ``usageMetadata``.

    Thinking tokens are output tokens on the bill, so they are added to the
    visible answer rather than reported beside it. A receipt that counted only
    what the model said would under-report what it charged — and on a reasoning
    model the gap is most of the bill.
    """
    if usage is None:
        return 0, 0

    def field(snake: str, camel: str) -> int:
        return int(_get(usage, snake, None) or _get(usage, camel, 0) or 0)

    prompt = field("prompt_token_count", "promptTokenCount")
    answer = field("candidates_token_count", "candidatesTokenCount")
    thoughts = field("thoughts_token_count", "thoughtsTokenCount")
    total = field("total_token_count", "totalTokenCount")
    output = answer + thoughts
    # Trust the total where it disagrees and is larger: it is the figure the
    # bill is drawn from, and a missing sub-count must not shrink the receipt.
    if total and total - prompt > output:
        output = total - prompt
    return prompt, output


def response_text(response: object) -> str | None:
    """The text of a Gemini response, or ``None`` if it produced none.

    An empty string and a missing one are different: a model that spent its
    ceiling thinking returns a candidate with no parts, and that is an answer of
    nothing rather than an absent answer.
    """
    if response is None:
        return None
    candidates = _get(response, "candidates") or []
    if not candidates:
        return None
    content = _get(candidates[0], "content")
    if content is None:
        return ""
    parts = _get(content, "parts") or []
    return "".join(
        text for part in parts if isinstance(text := _get(part, "text"), str)
    )


class GeminiBatch(Venue):
    """Google's Gemini batch tier — 50% of list, 24h target turnaround."""

    name = "gemini:batch"

    def __init__(self, client: object | None = None, *, display_name: str = "offpeak"):
        self._client = client
        self.display_name = display_name

    @property
    def client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    'Gemini venue requires the google-genai SDK: '
                    'pip install "offpeak[gemini]"'
                ) from exc
            self._client = genai.Client()
        return self._client

    def supports(self, model: str) -> bool:
        return model.startswith(_MODEL_PREFIXES)

    def submit(self, jobs: list[Job]) -> str:
        models = {j.model for j in jobs}
        if len(models) > 1:
            raise ValueError(
                "a Gemini batch carries one model: batches.create takes `model` "
                f"for the whole job, so {sorted(models)} cannot share a batch. "
                "Submit one batch per model, or pass a separate GeminiBatch per "
                "model to run()."
            )
        job = self.client.batches.create(
            model=models.pop(),
            src=build_requests(jobs),
            config={"display_name": self.display_name},
        )
        return job.name

    def status(self, handle: str) -> BatchState:
        job = self.client.batches.get(name=handle)
        stats = getattr(job, "completion_stats", None)
        state = getattr(job, "state", None)
        return BatchState(
            status=_STATUS.get(str(getattr(state, "name", state)), "in_progress"),
            completed=int(getattr(stats, "successful_count", 0) or 0),
            failed=int(getattr(stats, "failed_count", 0) or 0),
            total=int(
                (getattr(stats, "successful_count", 0) or 0)
                + (getattr(stats, "failed_count", 0) or 0)
                + (getattr(stats, "incomplete_count", 0) or 0)
            ),
            raw_status=str(getattr(state, "name", state)) if state else None,
            completed_at_utc=iso_utc(
                getattr(job, "end_time", None) or getattr(job, "update_time", None)
            ),
            created_at_utc=iso_utc(getattr(job, "create_time", None)),
        )

    def collect(self, handle: str) -> dict[str, Result]:
        job = self.client.batches.get(name=handle)
        dest = getattr(job, "dest", None)
        responses = getattr(dest, "inlined_responses", None) or []
        results: dict[str, Result] = {}
        for item in responses:
            meta = getattr(item, "metadata", None) or {}
            key = meta.get(_KEY) if isinstance(meta, dict) else getattr(meta, _KEY, None)
            if not key:
                continue
            error = getattr(item, "error", None)
            response = getattr(item, "response", None)
            if error is not None:
                results[key] = Result(job=None, text=None, raw={}, error=str(error))
                continue
            usage = getattr(response, "usage_metadata", None) or getattr(
                response, "usageMetadata", None
            )
            prompt, output = usage_tokens(usage)
            results[key] = Result(
                job=None,
                text=response_text(response),
                raw={"prompt_tokens": prompt, "completion_tokens": output},
                error=None,
            )
        return results

    def cancel(self, handle: str) -> None:
        try:
            self.client.batches.cancel(name=handle)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def run_sync(self, job: Job) -> Result:
        contents, system = to_contents(job.messages)
        try:
            response = self.client.models.generate_content(
                model=job.model, contents=contents, config=to_config(job.params, system)
            )
        except Exception as exc:  # noqa: BLE001
            return Result(job=job, error=str(exc))
        prompt, output = usage_tokens(getattr(response, "usage_metadata", None))
        return Result(
            job=job,
            text=response_text(response),
            raw={"prompt_tokens": prompt, "completion_tokens": output},
        )
