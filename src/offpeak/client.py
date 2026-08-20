"""The v0 desk: portfolio-submit jobs to batch venues, watch the deadline,
fall back to sync if the batch won't make it, settle a receipt.

This is deliberately simple — deadline risk is a buffer, not a forecast. The
hosted desk adds queue-latency forecasting, cross-venue portfolio placement,
own-GPU off-peak windows, and carbon-aware scheduling on the same interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from .deadline import parse_deadline, seconds_until
from .job import Job, Receipt, Result, Status
from .prices import PRICE_SHEET_DATE
from .venues.base import Venue

__all__ = ["run", "receipt", "Settlement", "default_venues"]


def default_venues() -> list[Venue]:
    """Provider batch tiers, tried in order. SDKs import lazily on first use."""
    from .venues.anthropic_batch import AnthropicBatch
    from .venues.openai_batch import OpenAIBatch

    return [AnthropicBatch(), OpenAIBatch()]


def _usage_tokens(raw: object) -> tuple[int, int]:
    if not isinstance(raw, dict):
        return 0, 0
    input_tokens = raw.get("input_tokens", raw.get("prompt_tokens", 0)) or 0
    output_tokens = raw.get("output_tokens", raw.get("completion_tokens", 0)) or 0
    return int(input_tokens), int(output_tokens)


def _pick_venue(model: str, venues: list[Venue]) -> Venue:
    for venue in venues:
        if venue.supports(model):
            return venue
    known = ", ".join(v.name for v in venues)
    raise ValueError(f"no venue supports model {model!r} (venues: {known})")


def run(
    jobs: Job | list[Job],
    deadline: object,
    *,
    venues: list[Venue] | None = None,
    fallback: str = "sync",
    poll_interval: float | None = None,
    risk_buffer: float | None = None,
) -> list[Result]:
    """Run *jobs* against *deadline* on the cheapest supporting venue.

    Submits each job to its venue's batch tier, polls until everything lands,
    and — if the batch has not completed by the time the remaining window
    shrinks to ``risk_buffer`` seconds — cancels and re-runs the stragglers
    synchronously at list price so the deadline is met (``fallback="sync"``,
    the default; ``fallback="none"`` reports them failed instead).

    Returns one :class:`Result` per job, in input order, each with a
    :class:`Receipt`.
    """
    job_list = [jobs] if isinstance(jobs, Job) else list(jobs)
    if not job_list:
        return []
    resolved = parse_deadline(deadline)
    window = seconds_until(resolved)
    if risk_buffer is None:
        risk_buffer = max(60.0, min(600.0, 0.15 * window))
    venue_list = venues if venues is not None else default_venues()

    groups: dict[str, tuple[Venue, list[Job]]] = {}
    for j in job_list:
        venue = _pick_venue(j.model, venue_list)
        groups.setdefault(venue.name, (venue, []))[1].append(j)

    submitted_at = datetime.now().astimezone()
    pending: dict[str, str] = {}  # venue name -> batch handle
    for name, (venue, group_jobs) in groups.items():
        pending[name] = venue.submit(group_jobs)
        for j in group_jobs:
            j.status = Status.SUBMITTED

    collected: dict[str, Result] = {}
    fell_back: set[str] = set()

    while pending:
        for name in list(pending):
            venue, group_jobs = groups[name]
            state = venue.status(pending[name])
            if state.status == "completed":
                collected.update(venue.collect(pending[name]))
                del pending[name]
            elif state.status in ("failed", "cancelled"):
                del pending[name]  # jobs surface below as fallback or errors

        remaining = seconds_until(resolved)
        if not pending and not _missing(groups, collected):
            break
        if remaining <= risk_buffer or not pending:
            for name in list(pending):
                groups[name][0].cancel(pending.pop(name))
            if fallback == "sync":
                for j in _missing(groups, collected):
                    result = groups[_venue_of(j, groups)][0].run_sync(j)
                    collected[j.id] = result
                    fell_back.add(j.id)
            break
        time.sleep(
            poll_interval
            if poll_interval is not None
            else min(30.0, max(2.0, remaining / 50.0))
        )

    completed_at = datetime.now().astimezone()
    results: list[Result] = []
    for j in job_list:
        result = collected.get(j.id)
        if result is None:
            result = Result(job=j, error="not returned by venue before the deadline")
            j.status = Status.FAILED
        else:
            result.job = j
            j.status = (
                Status.FELL_BACK
                if j.id in fell_back
                else (Status.SUCCEEDED if result.ok else Status.FAILED)
            )
        input_tokens, output_tokens = _usage_tokens(result.raw)
        result.receipt = Receipt(
            venue=groups[_venue_of(j, groups)][0].name,
            model=j.model,
            deadline=resolved,
            submitted_at=submitted_at,
            completed_at=completed_at if result.error is None else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            fell_back=j.id in fell_back,
        )
        results.append(result)
    return results


def _venue_of(j: Job, groups: dict[str, tuple[Venue, list[Job]]]) -> str:
    for name, (_, group_jobs) in groups.items():
        if j in group_jobs:
            return name
    raise KeyError(j.id)


def _missing(
    groups: dict[str, tuple[Venue, list[Job]]], collected: dict[str, Result]
) -> list[Job]:
    return [j for _, (_, js) in groups.items() for j in js if j.id not in collected]


@dataclass
class Settlement:
    """Aggregate receipt across a run."""

    total: int = 0
    ok: int = 0
    sla_met: int = 0
    fell_back: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    list_usd: float = 0.0
    paid_usd: float = 0.0
    unpriced: int = 0
    by_venue: dict = field(default_factory=dict)

    @property
    def captured_usd(self) -> float:
        return self.list_usd - self.paid_usd

    @property
    def captured_pct(self) -> float:
        return 0.0 if not self.list_usd else 100.0 * self.captured_usd / self.list_usd

    def __str__(self) -> str:
        venues = " · ".join(f"{k} {v}" for k, v in sorted(self.by_venue.items()))
        lines = [
            "OFFPEAK SETTLEMENT " + "─" * 28,
            f"jobs      {self.total} ({self.ok} ok, {self.fell_back} sync fallback)",
            f"sla       {self.sla_met}/{self.total} met",
            f"venues    {venues or '—'}",
            f"tokens    {self.input_tokens:,} in · {self.output_tokens:,} out",
            f"list      ${self.list_usd:,.2f}",
            f"paid      ${self.paid_usd:,.2f}",
            f"captured  ${self.captured_usd:,.2f} ({self.captured_pct:.1f}%)",
            f"prices    snapshot {PRICE_SHEET_DATE} — override via offpeak.prices",
        ]
        if self.unpriced:
            lines.append(f"note      {self.unpriced} job(s) had no price sheet entry")
        lines.append("─" * 47)
        return "\n".join(lines)


def receipt(results: list[Result]) -> Settlement:
    """Settle a run: aggregate per-job receipts into one :class:`Settlement`."""
    settlement = Settlement()
    for result in results:
        settlement.total += 1
        if result.ok:
            settlement.ok += 1
        r = result.receipt
        if r is None:
            continue
        settlement.sla_met += int(r.sla_met)
        settlement.fell_back += int(r.fell_back)
        settlement.input_tokens += r.input_tokens
        settlement.output_tokens += r.output_tokens
        settlement.by_venue[r.venue] = settlement.by_venue.get(r.venue, 0) + 1
        if r.list_usd is None or r.paid_usd is None:
            settlement.unpriced += 1
        else:
            settlement.list_usd += r.list_usd
            settlement.paid_usd += r.paid_usd
    return settlement
