"""Anthropic Message Batches venue (−50% vs list, 24h completion window).

Uses your own ``ANTHROPIC_API_KEY``. Requires the ``anthropic`` extra:
``pip install "offpeak[anthropic]"``.
"""

from __future__ import annotations

from ..job import Job, Result
from .base import BatchState, Venue

__all__ = ["AnthropicBatch", "build_requests"]

_DEFAULT_MAX_TOKENS = 4096


def build_requests(jobs: list[Job]) -> list[dict]:
    """Render *jobs* as Message Batches request dicts."""
    requests = []
    for j in jobs:
        system = None
        messages = []
        for message in j.messages:
            if message.get("role") == "system":
                system = message.get("content")
            else:
                messages.append(message)
        params = {
            "model": j.model,
            "messages": messages,
            "max_tokens": j.params.get("max_tokens", _DEFAULT_MAX_TOKENS),
            **{k: v for k, v in j.params.items() if k != "max_tokens"},
        }
        if system is not None:
            params["system"] = system
        requests.append({"custom_id": j.id, "params": params})
    return requests


def _text_of(message: object) -> str:
    blocks = getattr(message, "content", None) or []
    parts = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "".join(parts)


class AnthropicBatch(Venue):
    name = "anthropic:batch"

    def __init__(self, client: object | None = None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    'Anthropic venue requires the anthropic SDK: pip install "offpeak[anthropic]"'
                ) from exc
            self._client = Anthropic()
        return self._client

    def supports(self, model: str) -> bool:
        return model.startswith("claude")

    def submit(self, jobs: list[Job]) -> str:
        batch = self.client.messages.batches.create(requests=build_requests(jobs))
        return batch.id

    def status(self, handle: str) -> BatchState:
        batch = self.client.messages.batches.retrieve(handle)
        counts = getattr(batch, "request_counts", None)
        processing = getattr(batch, "processing_status", "in_progress")
        status = "completed" if processing == "ended" else "in_progress"
        if processing in ("canceling", "cancelled"):
            status = "cancelled"
        return BatchState(
            status=status,
            completed=getattr(counts, "succeeded", 0) or 0,
            failed=(getattr(counts, "errored", 0) or 0) + (getattr(counts, "expired", 0) or 0),
            total=sum(
                getattr(counts, k, 0) or 0
                for k in ("processing", "succeeded", "errored", "canceled", "expired")
            ),
        )

    def collect(self, handle: str) -> dict[str, Result]:
        results: dict[str, Result] = {}
        for entry in self.client.messages.batches.results(handle):
            outcome = entry.result
            if getattr(outcome, "type", None) == "succeeded":
                message = outcome.message
                usage = getattr(message, "usage", None)
                results[entry.custom_id] = Result(
                    job=None,
                    text=_text_of(message),
                    raw={
                        "input_tokens": getattr(usage, "input_tokens", 0),
                        "output_tokens": getattr(usage, "output_tokens", 0),
                    },
                )
            else:
                results[entry.custom_id] = Result(
                    job=None, error=f"{getattr(outcome, 'type', 'error')}: {outcome}"
                )
        return results

    def cancel(self, handle: str) -> None:
        try:
            self.client.messages.batches.cancel(handle)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def run_sync(self, job: Job) -> Result:
        requests = build_requests([job])
        params = requests[0]["params"]
        try:
            message = self.client.messages.create(**params)
        except Exception as exc:  # noqa: BLE001
            return Result(job=job, error=str(exc))
        usage = getattr(message, "usage", None)
        return Result(
            job=job,
            text=_text_of(message),
            raw={
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
            },
        )


def usage_tokens(usage: dict) -> tuple[int, int]:
    """(input_tokens, output_tokens) from an Anthropic usage dict."""
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
