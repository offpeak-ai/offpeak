"""The Venue interface — anywhere a deferred job can run.

v0.1 ships provider batch tiers. The same interface is how off-peak windows on
your own GPUs, spot capacity, and cleaner regions plug in later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..job import Job, Result

__all__ = ["Venue", "BatchState"]


@dataclass
class BatchState:
    """A venue batch's progress."""

    status: str  # "in_progress" | "completed" | "failed" | "cancelled"
    completed: int = 0
    failed: int = 0
    total: int = 0

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
