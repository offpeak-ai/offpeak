"""Job, Result, and Receipt — the unit of deferred work and its settlement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .prices import batch_cost_usd, list_cost_usd

__all__ = ["Job", "Result", "Receipt", "Status", "job"]


class Status(str, Enum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    FELL_BACK = "fell_back"  # completed, but via the sync fallback (list price)


@dataclass
class Job:
    """A venue-agnostic chat-completion job."""

    model: str
    messages: list[dict]
    params: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    metadata: dict = field(default_factory=dict)
    status: Status = Status.QUEUED


def job(
    model: str,
    input: str | list[dict] | None = None,
    *,
    system: str | None = None,
    metadata: dict | None = None,
    **params: object,
) -> Job:
    """Build a :class:`Job`.

    ``input`` may be a plain prompt string or a full ``messages`` list.
    Extra keyword arguments (``temperature``, ``max_tokens``, ...) are passed
    through to the venue.
    """
    if input is None:
        raise ValueError("job() requires an input (a prompt string or a messages list)")
    if isinstance(input, str):
        messages = [{"role": "user", "content": input}]
    else:
        messages = list(input)
    if system is not None:
        messages = [{"role": "system", "content": system}, *messages]
    return Job(model=model, messages=messages, params=dict(params), metadata=metadata or {})


@dataclass
class Receipt:
    """Per-job settlement: what ran where, when, and what the hour was worth."""

    venue: str
    model: str
    deadline: datetime
    submitted_at: datetime
    completed_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    fell_back: bool = False

    @property
    def sla_met(self) -> bool:
        return self.completed_at is not None and self.completed_at <= self.deadline

    @property
    def list_usd(self) -> float | None:
        """What the job would have cost run synchronously at list price."""
        return list_cost_usd(self.model, self.input_tokens, self.output_tokens)

    @property
    def paid_usd(self) -> float | None:
        """What the job cost on the venue it actually ran on."""
        if self.fell_back:
            return self.list_usd
        return batch_cost_usd(self.model, self.input_tokens, self.output_tokens)

    @property
    def spread_usd(self) -> float | None:
        """Captured spread: list minus paid."""
        if self.list_usd is None or self.paid_usd is None:
            return None
        return self.list_usd - self.paid_usd


@dataclass
class Result:
    """The outcome of one job."""

    job: Job
    text: str | None = None
    raw: object = None
    error: str | None = None
    receipt: Receipt | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.text is not None
