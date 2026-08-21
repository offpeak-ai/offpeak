"""The free quote — what a deadline is worth, before you spend anything.

``quote()`` prices a job list against the bundled price sheet and returns what
each venue's batch tier would save versus running the same tokens synchronously
at list. It makes **no API calls**: no submission, no token-counting round
trip, no key required. It is arithmetic against published numbers, which is the
same thing a receipt is — just before the trade instead of after.

Token counts come from the job where the job knows them and are estimated where
it does not. Every quote says which, per figure, in :attr:`Quote.basis`: a
number you cannot trace back to its source is not a quote.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .client import _pick_venue, default_venues
from .deadline import parse_deadline, seconds_until
from .job import Job
from .prices import BATCH_DISCOUNT, PRICE_SHEET_DATE, format_usd, get_price
from .venues.base import Venue

__all__ = ["quote", "Quote", "VenueQuote", "estimate_tokens", "CHARS_PER_TOKEN"]

# A deliberately crude estimator. Real tokenizers differ per model and per
# language; four characters per token is the industry rule of thumb and is
# labeled as an estimate everywhere it is used, never presented as a count.
CHARS_PER_TOKEN = 4

# Both venues publish a 24h batch completion window. A deadline shorter than
# this does not make batch impossible — batches usually land far sooner — but
# it does mean the SLA rests on the sync fallback rather than on the tier.
BATCH_COMPLETION_WINDOW_S = 24 * 3600


def _text_len(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):  # content blocks
        return sum(
            len(b["text"])
            for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return 0


def estimate_tokens(j: Job) -> tuple[int, int, str, str]:
    """(input, output, input_basis, output_basis) for one job.

    Explicit counts on ``job.metadata`` win; then ``max_tokens`` as an output
    ceiling; then a chars/4 estimate for input. Each figure reports its own
    provenance so a quote never launders an estimate into a fact.
    """
    meta = j.metadata or {}

    if isinstance(meta.get("input_tokens"), int):
        input_tokens, input_basis = int(meta["input_tokens"]), "explicit"
    else:
        chars = sum(_text_len(m.get("content")) for m in j.messages)
        input_tokens = max(1, math.ceil(chars / CHARS_PER_TOKEN))
        input_basis = f"estimated (chars/{CHARS_PER_TOKEN})"

    if isinstance(meta.get("output_tokens"), int):
        output_tokens, output_basis = int(meta["output_tokens"]), "explicit"
    elif isinstance(j.params.get("max_tokens"), int):
        output_tokens, output_basis = int(j.params["max_tokens"]), "ceiling (max_tokens)"
    else:
        output_tokens, output_basis = 0, "unknown (no max_tokens, none given)"

    return input_tokens, output_tokens, input_basis, output_basis


@dataclass
class VenueQuote:
    """What one venue's batch tier is worth for the jobs routed to it."""

    venue: str
    jobs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    list_usd: float = 0.0
    batch_usd: float = 0.0
    unpriced: int = 0
    unknown_output: int = 0

    @property
    def spread_usd(self) -> float:
        return self.list_usd - self.batch_usd

    @property
    def spread_pct(self) -> float:
        return 0.0 if not self.list_usd else 100.0 * self.spread_usd / self.list_usd


@dataclass
class Quote:
    """A pre-trade quote. No API calls were made to produce this."""

    deadline: datetime
    window_seconds: float
    by_venue: dict[str, VenueQuote] = field(default_factory=dict)
    basis: dict[str, str] = field(default_factory=dict)

    @property
    def jobs(self) -> int:
        return sum(v.jobs for v in self.by_venue.values())

    @property
    def input_tokens(self) -> int:
        return sum(v.input_tokens for v in self.by_venue.values())

    @property
    def output_tokens(self) -> int:
        return sum(v.output_tokens for v in self.by_venue.values())

    @property
    def list_usd(self) -> float:
        return sum(v.list_usd for v in self.by_venue.values())

    @property
    def batch_usd(self) -> float:
        return sum(v.batch_usd for v in self.by_venue.values())

    @property
    def spread_usd(self) -> float:
        return self.list_usd - self.batch_usd

    @property
    def spread_pct(self) -> float:
        return 0.0 if not self.list_usd else 100.0 * self.spread_usd / self.list_usd

    @property
    def unpriced(self) -> int:
        return sum(v.unpriced for v in self.by_venue.values())

    @property
    def unknown_output(self) -> int:
        return sum(v.unknown_output for v in self.by_venue.values())

    @property
    def is_floor(self) -> bool:
        """True when some job's output tokens were unknown and priced at zero.

        Output is the expensive side on every model on the sheet, so a quote
        that silently omits it reads far cheaper than the bill. Such a quote is
        a floor, and says so.
        """
        return self.unknown_output > 0

    @property
    def within_batch_window(self) -> bool:
        """Whether the deadline clears the venues' published completion window."""
        return self.window_seconds >= BATCH_COMPLETION_WINDOW_S

    def __str__(self) -> str:
        lines = [
            "OFFPEAK QUOTE " + "─" * 33,
            f"jobs      {self.jobs} across {len(self.by_venue)} venue(s)",
            f"deadline  {self.deadline:%Y-%m-%d %H:%M %Z} ({self.window_seconds / 3600:.1f}h out)",
            f"tokens    {self.input_tokens:,} in · {self.output_tokens:,} out",
            "",
        ]
        for name in sorted(self.by_venue):
            v = self.by_venue[name]
            lines.append(
                f"  {name:<16} {v.jobs:>5} job(s)  list ${format_usd(v.list_usd)}"
                f"  batch ${format_usd(v.batch_usd)}"
                f"  save ${format_usd(v.spread_usd)} ({v.spread_pct:.1f}%)"
            )
        lines += [
            "",
            f"list      ${format_usd(self.list_usd)}   (run now, synchronously)",
            f"batch     ${format_usd(self.batch_usd)}   (run by the deadline)",
            f"save      ${format_usd(self.spread_usd)} ({self.spread_pct:.1f}%)",
        ]
        if not self.within_batch_window:
            lines.append(
                f"risk      deadline is inside the {BATCH_COMPLETION_WINDOW_S // 3600}h batch "
                "window — the SLA rests on the sync fallback, which pays list"
            )
        if self.is_floor:
            lines.append(
                f"FLOOR     {self.unknown_output} job(s) gave no output-token signal; their "
                "output is priced at zero"
            )
            lines.append(
                "          output costs more than input on every model here — "
                "pass max_tokens or metadata to quote it properly"
            )
        if self.unpriced:
            lines.append(f"note      {self.unpriced} job(s) had no price sheet entry")
        lines += [
            f"basis     {'; '.join(f'{k} {v}' for k, v in sorted(self.basis.items()))}",
            f"prices    snapshot {PRICE_SHEET_DATE} — estimate only, not a bill",
            "─" * 47,
        ]
        return "\n".join(lines)


def quote(
    jobs: Job | list[Job],
    deadline: object,
    *,
    venues: list[Venue] | None = None,
) -> Quote:
    """Price *jobs* against *deadline* without calling any provider.

    Routes each job to the venue that would run it, then settles list versus
    batch cost from the bundled price sheet. Raises ``ValueError`` for a
    deadline in the past or a model no venue supports — the same programming
    errors :func:`offpeak.run` reserves exceptions for.
    """
    job_list = [jobs] if isinstance(jobs, Job) else list(jobs)
    resolved = parse_deadline(deadline)
    q = Quote(deadline=resolved, window_seconds=seconds_until(resolved))
    if not job_list:
        return q

    venue_list = venues if venues is not None else default_venues()
    bases: dict[str, set[str]] = {"input": set(), "output": set()}

    for j in job_list:
        venue = _pick_venue(j.model, venue_list)
        vq = q.by_venue.setdefault(venue.name, VenueQuote(venue=venue.name))
        input_tokens, output_tokens, input_basis, output_basis = estimate_tokens(j)
        bases["input"].add(input_basis)
        bases["output"].add(output_basis)

        vq.jobs += 1
        vq.input_tokens += input_tokens
        vq.output_tokens += output_tokens
        if output_basis.startswith("unknown"):
            vq.unknown_output += 1

        price = get_price(j.model)
        if price is None:
            vq.unpriced += 1
            continue
        list_usd = (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
        vq.list_usd += list_usd
        vq.batch_usd += list_usd * BATCH_DISCOUNT

    q.basis = {k: ", ".join(sorted(v)) for k, v in bases.items() if v}
    return q
