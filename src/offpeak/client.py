"""The v0 desk: portfolio-submit jobs to batch venues, watch the deadline,
fall back to sync if the batch won't make it, settle a receipt.

This is deliberately simple — deadline risk is a buffer, not a forecast. The
hosted desk adds queue-latency forecasting, cross-venue portfolio placement,
own-GPU off-peak windows, and carbon-aware scheduling on the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import prices as _prices
from .job import Job, Result
from .prices import BATCH_DISCOUNT, format_usd
from .ticket import Ticket, collect, status, submit
from .venues.base import Venue

__all__ = [
    "run",
    "receipt",
    "Settlement",
    "default_venues",
    "submit",
    "collect",
    "status",
    "Ticket",
]


def default_venues() -> list[Venue]:
    """Provider batch tiers, tried in order. SDKs import lazily on first use.

    Anthropic and OpenAI only. Every other venue in the tree — Groq, Mistral,
    Gemini, DeepSeek, Qwen — is opt-in: it wants its own key and its own
    extra, and a model name should not start costing money at a venue nobody
    asked for. Pass them explicitly::

        from offpeak.venues import DeepSeekClock, QwenBatch
        offpeak.run(jobs, "06:00", venues=[DeepSeekClock(), QwenBatch()])
    """
    from .venues.anthropic_batch import AnthropicBatch
    from .venues.openai_batch import OpenAIBatch

    return [AnthropicBatch(), OpenAIBatch()]


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

    Provider failures never escape: if a venue raises while submitting, polling
    or running the sync fallback, the affected jobs are rescued through the
    fallback where the deadline still allows it and otherwise come back as
    failed :class:`Result` objects carrying the provider's message. Exceptions
    out of ``run()`` are reserved for programming errors — a bad deadline, or a
    model no configured venue supports.

    ``run()`` blocks for as long as the batch takes. When the calling process
    cannot stay alive that long — a laptop, a CI step, a serverless function —
    use :func:`submit` and :func:`collect` and keep the :class:`Ticket` between
    them; ``run()`` is exactly ``collect(submit(...))``.
    """
    job_list = [jobs] if isinstance(jobs, Job) else list(jobs)
    if not job_list:
        return []
    venue_list = venues if venues is not None else default_venues()
    ticket = submit(job_list, deadline, venues=venue_list, risk_buffer=risk_buffer)
    results = collect(
        ticket, venues=venue_list, fallback=fallback, wait=True, poll_interval=poll_interval
    )
    assert results is not None  # wait=True always settles
    return results


_usd = format_usd  # kept as a private alias; the canonical home is prices


@dataclass
class Settlement:
    """Aggregate receipt across a run."""

    total: int = 0
    ok: int = 0
    sla_met: int = 0
    fell_back: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    list_usd: float = 0.0
    paid_usd: float = 0.0
    left_on_table_usd: float = 0.0
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
            f"jobs      {self.total} ({self.ok} ok, {self.fell_back} sync fallback, "
            f"{self.failed} failed)",
            f"sla       {self.sla_met}/{self.total} met",
            f"venues    {venues or '—'}",
            f"tokens    {self.input_tokens:,} in · {self.output_tokens:,} out",
            f"list      ${_usd(self.list_usd)}",
            f"paid      ${_usd(self.paid_usd)}",
            f"captured  ${_usd(self.captured_usd)} ({self.captured_pct:.1f}%)",
            f"prices    snapshot {_prices.sheet_date()} — override via offpeak.prices",
        ]
        if self.fell_back:
            lines.append(
                f"left      ${_usd(self.left_on_table_usd)} on the table "
                f"({self.fell_back} job(s) missed the batch tier)"
            )
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
        else:
            settlement.failed += 1
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
            if r.fell_back:
                # The spread this job would have captured had the batch held,
                # less whatever it captured anyway — a clock-priced fallback
                # that ran off-peak paid half and left nothing on the table.
                spread = r.spread_usd or 0.0
                settlement.left_on_table_usd += max(0.0, r.list_usd * (1 - BATCH_DISCOUNT) - spread)
    return settlement
