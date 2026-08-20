"""OpenAI Batch API venue (−50% vs list, 24h completion window).

Uses your own ``OPENAI_API_KEY``. Requires the ``openai`` extra:
``pip install "offpeak[openai]"``.
"""

from __future__ import annotations

import json

from ..job import Job, Result
from .base import BatchState, Venue

__all__ = ["OpenAIBatch", "build_jsonl", "parse_output_line"]

_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
_ENDPOINT = "/v1/chat/completions"


def build_jsonl(jobs: list[Job]) -> bytes:
    """Render *jobs* as OpenAI Batch API JSONL (one request per line)."""
    lines = []
    for j in jobs:
        body = {"model": j.model, "messages": j.messages, **j.params}
        lines.append(
            json.dumps(
                {"custom_id": j.id, "method": "POST", "url": _ENDPOINT, "body": body},
                ensure_ascii=False,
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def parse_output_line(line: str) -> tuple[str, str | None, dict, str | None]:
    """Parse one output-file line -> (job_id, text, usage, error)."""
    record = json.loads(line)
    job_id = record.get("custom_id", "")
    if record.get("error"):
        return job_id, None, {}, str(record["error"])
    response = record.get("response") or {}
    body = response.get("body") or {}
    if response.get("status_code") not in (200, None):
        return job_id, None, {}, f"HTTP {response.get('status_code')}: {body}"
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return job_id, None, {}, f"unexpected response body: {body!r}"
    return job_id, text, body.get("usage") or {}, None


class OpenAIBatch(Venue):
    name = "openai:batch"

    def __init__(self, client: object | None = None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    'OpenAI venue requires the openai SDK: pip install "offpeak[openai]"'
                ) from exc
            self._client = OpenAI()
        return self._client

    def supports(self, model: str) -> bool:
        return model.startswith(_MODEL_PREFIXES)

    def submit(self, jobs: list[Job]) -> str:
        upload = self.client.files.create(
            file=("offpeak_batch.jsonl", build_jsonl(jobs)), purpose="batch"
        )
        batch = self.client.batches.create(
            input_file_id=upload.id, endpoint=_ENDPOINT, completion_window="24h"
        )
        return batch.id

    def status(self, handle: str) -> BatchState:
        batch = self.client.batches.retrieve(handle)
        mapping = {
            "validating": "in_progress",
            "in_progress": "in_progress",
            "finalizing": "in_progress",
            "completed": "completed",
            "failed": "failed",
            "expired": "failed",
            "cancelling": "cancelled",
            "cancelled": "cancelled",
        }
        counts = getattr(batch, "request_counts", None)
        return BatchState(
            status=mapping.get(batch.status, "in_progress"),
            completed=getattr(counts, "completed", 0) or 0,
            failed=getattr(counts, "failed", 0) or 0,
            total=getattr(counts, "total", 0) or 0,
        )

    def collect(self, handle: str) -> dict[str, Result]:
        batch = self.client.batches.retrieve(handle)
        out: dict[str, tuple[str | None, dict, str | None]] = {}
        for file_id in (batch.output_file_id, batch.error_file_id):
            if not file_id:
                continue
            content = self.client.files.content(file_id).text
            for line in content.splitlines():
                if not line.strip():
                    continue
                job_id, text, usage, error = parse_output_line(line)
                out[job_id] = (text, usage, error)
        results: dict[str, Result] = {}
        for job_id, (text, usage, error) in out.items():
            results[job_id] = Result(
                job=None,  # attached by the scheduler
                text=text,
                raw=usage,
                error=error,
            )
        return results

    def cancel(self, handle: str) -> None:
        try:
            self.client.batches.cancel(handle)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def run_sync(self, job: Job) -> Result:
        try:
            response = self.client.chat.completions.create(
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


def usage_tokens(usage: dict) -> tuple[int, int]:
    """(input_tokens, output_tokens) from an OpenAI usage dict."""
    return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
