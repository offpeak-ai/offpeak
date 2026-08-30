"""Mistral Batch venue (50% off list, deadline set per job).

Requires the ``mistral`` extra: ``pip install "offpeak[mistral]"``.

.. warning::
   **The batch tier is behind billing, and this code cannot open it.** On
   2026-08-24 ``POST /v1/batch/jobs`` answered ``402`` — *"You do not have
   access to this service. You can enable billing via the console."* — so
   :meth:`MistralBatch.submit` cannot complete. The paywall is on batch
   creation alone: ``POST /v1/files`` with ``purpose="batch"`` is reachable and
   ``POST /v1/chat/completions`` returns 200, which is how every job in
   ``receipts/2026-08-24-mistral-1.json`` settled — through the sync fallback,
   at list, capturing nothing.

   Worth recording *how* that was missed. The entitlement probe a day earlier
   read Mistral as reachable because a deliberately malformed batch create came
   back ``422`` naming the field it lacked — the request had clearly reached the
   validator. It had. **Validation runs before the billing check**, so a
   malformed request can never reach the paywall, and a probe that cannot fail
   the way the real thing fails is not a probe of the real thing. Only a
   well-formed submission finds this, which is what the receipt is.

Mistral's batch API is OpenAI-shaped at both ends and unlike it in the middle.
The JSONL that goes in and the JSONL that comes out are near enough identical —
which is why :func:`~offpeak.venues.openai_batch.parse_output_line` is reused
verbatim rather than reimplemented — but the job in between is described
differently, in three ways that matter.

**The model belongs to the job, not to the line.** OpenAI puts a model on every
request and will happily mix them inside one batch. Mistral takes one ``model``
at create and applies it to the file. ``Model must be provided`` is a hard
validation error, so a batch spanning two models is not a thing this API can
express — see :meth:`MistralBatch.submit`, which refuses one rather than
quietly running every job on the first job's model.

**The deadline is a parameter.** ``timeout_hours`` is Mistral's completion
window, set per job rather than fixed at 24h. That is the same dimension Groq
exposes as ``completion_window``, and the same thing ``offpeak`` calls a
deadline — so it is plumbed through rather than left on its default.

**The status vocabulary is its own.** Seven states, mapped in
:data:`_STATUS` onto the four this library uses.

Prices are on the bundled sheet as of 2026-08-23 and the batch tier is the
standard 50% of list, so :data:`~offpeak.prices.BATCH_DISCOUNT` covers it with
no special case.
"""

from __future__ import annotations

import json

from ..job import Job, Result
from .base import BatchState, Venue, iso_utc
from .openai_batch import parse_output_line

__all__ = ["MistralBatch", "build_jsonl", "DEFAULT_TIMEOUT_HOURS"]

_ENDPOINT = "/v1/chat/completions"

#: Mistral's own default completion window, in hours.
DEFAULT_TIMEOUT_HOURS = 24

# Mistral spells its models plainly and owns every prefix here. ``codestral``
# and ``mistral-`` do not overlap with any other venue's catalogue, and
# ``ministral`` is deliberately listed separately from ``mistral`` because it is
# not a prefix of it — a fact worth one line of explicitness, since reading it
# as one would silently drop the whole small-model family.
_MODEL_PREFIXES = (
    "mistral-",
    "ministral-",
    "magistral-",
    "codestral",
    "devstral",
    "open-mistral",
    "open-mixtral",
    "pixtral",
)

# Mistral's seven job states, onto the four this library speaks.
_STATUS = {
    "QUEUED": "in_progress",
    "RUNNING": "in_progress",
    "SUCCESS": "completed",
    "FAILED": "failed",
    # A batch that ran out of its own window is a failure, not a cancellation:
    # nobody asked for it to stop.
    "TIMEOUT_EXCEEDED": "failed",
    "CANCELLATION_REQUESTED": "cancelled",
    "CANCELLED": "cancelled",
}


def build_jsonl(jobs: list[Job]) -> bytes:
    """Render *jobs* as Mistral batch JSONL (one request per line).

    Two fields, ``custom_id`` and ``body`` — not OpenAI's four. Mistral takes
    the endpoint once at job creation, so there is no ``method`` or ``url`` on
    the line, and the model rides on the job rather than the request.

    ``max_tokens`` is passed through untranslated: it is Mistral's own spelling,
    and the ``max_completion_tokens`` rewrite that OpenAI's newer families
    require would be rejected here.
    """
    lines = []
    for j in jobs:
        body = {"messages": j.messages, **j.params}
        lines.append(json.dumps({"custom_id": j.id, "body": body}, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")


class MistralBatch(Venue):
    """Mistral's batch tier — 50% of list, with the window as an argument."""

    name = "mistral:batch"

    def __init__(
        self,
        client: object | None = None,
        *,
        timeout_hours: int = DEFAULT_TIMEOUT_HOURS,
    ):
        if timeout_hours <= 0:
            raise ValueError(f"timeout_hours must be positive, got {timeout_hours!r}")
        self._client = client
        self.timeout_hours = timeout_hours

    @property
    def client(self):
        if self._client is None:
            try:
                # 2.9 moved the client under `mistralai.client`; older releases
                # export it from the top level. Try both before giving up, so a
                # working install is never reported as a missing one.
                try:
                    from mistralai.client import Mistral
                except ImportError:
                    from mistralai import Mistral
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    'Mistral venue requires the mistralai SDK: pip install "offpeak[mistral]"'
                ) from exc
            self._client = Mistral()
        return self._client

    def supports(self, model: str) -> bool:
        return model.startswith(_MODEL_PREFIXES)

    def submit(self, jobs: list[Job]) -> str:
        models = {j.model for j in jobs}
        if len(models) > 1:
            raise ValueError(
                "a Mistral batch carries one model: the API takes `model` at job "
                "creation and applies it to the whole file, so "
                f"{sorted(models)} cannot share a batch. Submit one batch per "
                "model, or pass a separate MistralBatch per model to run()."
            )
        model = models.pop()

        upload = self.client.files.upload(
            file={"file_name": "offpeak_batch.jsonl", "content": build_jsonl(jobs)},
            purpose="batch",
        )
        job = self.client.batch.jobs.create(
            endpoint=_ENDPOINT,
            input_files=[upload.id],
            model=model,
            timeout_hours=self.timeout_hours,
        )
        return job.id

    def status(self, handle: str) -> BatchState:
        job = self.client.batch.jobs.get(job_id=handle)
        raw = getattr(job, "status", None)
        # ``succeeded_requests`` rather than ``completed_requests``: Mistral
        # counts a failed request as completed, and this field means "landed
        # and worked" everywhere else in the library.
        return BatchState(
            status=_STATUS.get(str(raw), "in_progress"),
            completed=getattr(job, "succeeded_requests", 0) or 0,
            failed=getattr(job, "failed_requests", 0) or 0,
            total=getattr(job, "total_requests", 0) or 0,
            raw_status=str(raw) if raw else None,
            completed_at_utc=iso_utc(getattr(job, "completed_at", None)),
            created_at_utc=iso_utc(getattr(job, "created_at", None)),
        )

    def collect(self, handle: str) -> dict[str, Result]:
        job = self.client.batch.jobs.get(job_id=handle)
        out: dict[str, tuple[str | None, dict, str | None]] = {}
        for file_id in (
            getattr(job, "output_file", None),
            getattr(job, "error_file", None),
        ):
            if not file_id:
                continue
            content = self._download(file_id)
            for line in content.splitlines():
                if not line.strip():
                    continue
                job_id, text, usage, error = parse_output_line(line)
                out[job_id] = (text, usage, error)
        return {
            job_id: Result(job=None, text=text, raw=usage, error=error)
            for job_id, (text, usage, error) in out.items()
        }

    def _download(self, file_id: str) -> str:
        """The file's text, however this SDK version hands it back.

        Read before reaching for ``.text``, not after. ``files.download`` returns
        a *streaming* response on the current SDK, and touching ``.text`` before
        it has been read raises ``httpx.ResponseNotRead`` — which is a
        ``RuntimeError``, not an ``AttributeError``, so ``getattr(response,
        "text", None)`` does not shield it. Asking for the text first therefore
        raised out of :meth:`collect`, ``run()`` booked the completed batch as a
        polling failure, and every job took the sync fallback at list price:
        the batch tier was reached, paid for, and then thrown away.

        See ``receipts/2026-08-26-mistral-2.json`` — that is this bug's bill.
        """
        response = self.client.files.download(file_id=file_id)
        read = getattr(response, "read", None)
        if callable(read):
            try:
                raw = read()
            except Exception:  # noqa: BLE001 — an already-consumed stream
                raw = None
            if raw is not None:
                return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        try:
            text = response.text
        except Exception:  # noqa: BLE001 — unread stream, or no .text at all
            text = None
        if text is not None:
            return text
        return response.decode("utf-8") if isinstance(response, bytes) else str(response)

    def cancel(self, handle: str) -> None:
        try:
            self.client.batch.jobs.cancel(job_id=handle)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def run_sync(self, job: Job) -> Result:
        try:
            response = self.client.chat.complete(
                model=job.model, messages=job.messages, **job.params
            )
        except Exception as exc:  # noqa: BLE001
            return Result(job=job, error=str(exc))
        usage = getattr(response, "usage", None)
        return Result(
            job=job,
            text=response.choices[0].message.content,
            raw={
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
            },
        )
