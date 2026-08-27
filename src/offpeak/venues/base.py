"""The Venue interface — anywhere a deferred job can run.

v0.1 ships provider batch tiers. The same interface is how off-peak windows on
your own GPUs, spot capacity, and cleaner regions plug in later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from ..job import Job, Result

__all__ = ["Venue", "BatchState", "iso_utc"]


def iso_utc(value) -> str | None:
    """Normalise a provider timestamp — unix seconds, datetime, or ISO string —
    to an ISO 8601 UTC string. Anything unrecognisable becomes None rather than
    a guess: an absent completion time is honest, a wrong one poisons a record.
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            if value <= 0:
                return None
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat(timespec="seconds")
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None
    return None


@dataclass
class BatchState:
    """A venue batch's progress."""

    status: str  # "in_progress" | "completed" | "failed" | "cancelled"
    completed: int = 0
    failed: int = 0
    total: int = 0
    #: The provider's own status word, unmapped ("expired" survives here even
    #: though it maps to "failed" for run()'s purposes). None when the driver
    #: has nothing beyond the mapped status.
    raw_status: str | None = None
    #: When the provider says the batch finished (ISO 8601, UTC), if it says.
    #: A poller that checks once a day still learns the true completion time
    #: from this field; without it, resolution is bounded by the check times.
    completed_at_utc: str | None = None

    @property
    def done(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")


class Venue(ABC):
    """A place deferred work can execute, plus a synchronous escape hatch."""

    name: str = "venue"

    @abstractmethod
    def supports(self, model: str) -> bool:
        """Whether this venue can run *model*."""

    @abstractmethod
    def submit(self, jobs: list[Job]) -> str:
        """Submit *jobs* as one batch; return an opaque batch handle."""

    @abstractmethod
    def status(self, handle: str) -> BatchState:
        """Poll a batch's progress."""

    @abstractmethod
    def collect(self, handle: str) -> dict[str, Result]:
        """Fetch results for a finished batch, keyed by job id."""

    @abstractmethod
    def cancel(self, handle: str) -> None:
        """Best-effort cancel of an in-flight batch."""

    @abstractmethod
    def run_sync(self, job: Job) -> Result:
        """Run one job synchronously at list price (the SLA fallback path)."""
