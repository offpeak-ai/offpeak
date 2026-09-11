"""Submit now, collect later — the deadline survives your process.

``run()`` submits a batch and blocks until it lands. That is the right shape
inside a Temporal activity or an Airflow task, where a worker is paid to wait.
It is the wrong shape on a laptop that sleeps, in a CI step with a timeout, or
in a serverless function: the process dies, the batch is orphaned at the
provider, you are billed for results nobody collects.

The fix is to make the in-flight state a value you can keep::

    ticket = offpeak.submit(jobs, deadline="06:00")
    ticket.save("tonight.json")            # anywhere: disk, a DB row, S3

    # …later, in a different process — a cron at 06:00, a second CI job…
    ticket = offpeak.Ticket.load("tonight.json")
    results = offpeak.collect(ticket)      # same deadline, same fallback
    print(offpeak.receipt(results))

``collect(ticket, wait=False)`` is a single non-blocking sweep: it returns the
results if everything has landed (or the deadline forced a settlement), and
``None`` if the batch is still open — call again later. ``status(ticket)``
peeks without touching anything.

A ticket carries the jobs, the resolved deadline, the risk buffer, and one
provider batch handle per venue. It does not carry clients or keys: pass the
same ``venues=`` you submitted with (or rely on ``default_venues()``) when you
collect. Venues are matched by ``name``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .deadline import parse_deadline, seconds_until
from .job import Job, Receipt, Result, Status
from .venues.base import BatchState, Venue

if TYPE_CHECKING:
    from .desk import DeskPlan

__all__ = ["Ticket", "submit", "status", "collect"]

TICKET_VERSION = 1


def _default_venues() -> list[Venue]:
    from .client import default_venues

    return default_venues()


def _pick_venue(model: str, venues: list[Venue]) -> Venue:
    for venue in venues:
        if venue.supports(model):
            return venue
    known = ", ".join(v.name for v in venues)
    raise ValueError(f"no venue supports model {model!r} (venues: {known})")


def _usage_tokens(raw: object) -> tuple[int, int]:
    if not isinstance(raw, dict):
        return 0, 0
    input_tokens = raw.get("input_tokens", raw.get("prompt_tokens", 0)) or 0
    output_tokens = raw.get("output_tokens", raw.get("completion_tokens", 0)) or 0
    return int(input_tokens), int(output_tokens)


def _paid_fraction(raw: object) -> float | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("paid_fraction")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass
class Ticket:
    """Everything needed to finish a run that was started somewhere else.

    Produced by :func:`submit`, consumed by :func:`collect`. Serialisable with
    :meth:`to_dict` / :meth:`from_dict` (plain JSON types only), and
    :meth:`save` / :meth:`load` for the one-file case.
    """

    jobs: list[Job]
    deadline: datetime
    submitted_at: datetime
    risk_buffer: float
    #: job id -> venue name (every job has one, even if its submit failed)
    assignment: dict[str, str] = field(default_factory=dict)
    #: venue name -> provider batch handle, for batches still open
    batches: dict[str, str] = field(default_factory=dict)
    #: venue name -> why its batch path died, if it did
    venue_errors: dict[str, str] = field(default_factory=dict)
    #: job id -> result already fetched (a partial collect survives a restart)
    collected: dict[str, Result] = field(default_factory=dict)
    #: job ids rescued by the sync fallback
    fell_back: set[str] = field(default_factory=set)
    #: Set only when ``submit()`` was given ``desk=``. The key itself is never
    #: kept here -- it is re-read from ``OFFPEAK_KEY`` wherever the ticket is
    #: collected, so a saved ticket file never carries it.
    desk_url: str | None = None
    #: Set once the desk answered ``/v1/plan``; stays ``None`` if it did not.
    desk_plan_id: str | None = None
    version: int = TICKET_VERSION

    # -- state -------------------------------------------------------------

    @property
    def pending(self) -> bool:
        """True while any batch is still open at a venue."""
        return bool(self.batches)

    @property
    def remaining(self) -> float:
        """Seconds until the deadline (negative once it has passed)."""
        return seconds_until(self.deadline)

    def _missing(self) -> list[Job]:
        return [j for j in self.jobs if j.id not in self.collected]

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "deadline": self.deadline.isoformat(),
            "submitted_at": self.submitted_at.isoformat(),
            "risk_buffer": self.risk_buffer,
            "jobs": [
                {
                    "id": j.id,
                    "model": j.model,
                    "messages": j.messages,
                    "params": j.params,
                    "metadata": j.metadata,
                    "status": j.status.value,
                }
                for j in self.jobs
            ],
            "assignment": dict(self.assignment),
            "batches": dict(self.batches),
            "venue_errors": dict(self.venue_errors),
            "collected": {
                jid: {"text": r.text, "raw": r.raw, "error": r.error}
                for jid, r in self.collected.items()
            },
            "fell_back": sorted(self.fell_back),
            "desk_url": self.desk_url,
            "desk_plan_id": self.desk_plan_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Ticket:
        version = int(data.get("version", 0))
        if version != TICKET_VERSION:
            raise ValueError(f"unsupported ticket version {version} (want {TICKET_VERSION})")
        jobs = [
            Job(
                model=d["model"],
                messages=list(d["messages"]),
                params=dict(d.get("params") or {}),
                id=d["id"],
                metadata=dict(d.get("metadata") or {}),
                status=Status(d.get("status", Status.SUBMITTED.value)),
            )
            for d in data["jobs"]
        ]
        by_id = {j.id: j for j in jobs}
        collected = {
            jid: Result(job=by_id[jid], text=r.get("text"), raw=r.get("raw"), error=r.get("error"))
            for jid, r in (data.get("collected") or {}).items()
            if jid in by_id
        }
        return cls(
            jobs=jobs,
            deadline=datetime.fromisoformat(data["deadline"]),
            submitted_at=datetime.fromisoformat(data["submitted_at"]),
            risk_buffer=float(data["risk_buffer"]),
            assignment=dict(data.get("assignment") or {}),
            batches=dict(data.get("batches") or {}),
            venue_errors=dict(data.get("venue_errors") or {}),
            collected=collected,
            fell_back=set(data.get("fell_back") or []),
            desk_url=data.get("desk_url"),
            desk_plan_id=data.get("desk_plan_id"),
            version=version,
        )

    def to_json(self, **kwargs: object) -> str:
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_json(cls, text: str) -> Ticket:
        return cls.from_dict(json.loads(text))

    def save(self, path: str | Path) -> Path:
        """Write the ticket as JSON. Overwrites."""
        p = Path(path)
        p.write_text(self.to_json())
        return p

    @classmethod
    def load(cls, path: str | Path) -> Ticket:
        return cls.from_json(Path(path).read_text())

    def __str__(self) -> str:
        state = "pending" if self.pending else "settled" if not self._missing() else "open"
        left = self.remaining
        when = f"{left / 3600:.1f}h left" if left > 0 else f"{-left / 60:.0f}m past"
        venues = " · ".join(f"{k} {v}" for k, v in sorted(self.batches.items())) or "—"
        return (
            f"OFFPEAK TICKET {state} · {len(self.jobs)} job(s) · "
            f"{len(self.collected)} collected · deadline {self.deadline:%Y-%m-%d %H:%M %Z} "
            f"({when}) · batches {venues}"
        )


# -- the three verbs --------------------------------------------------------


def _plan_with_desk(
    desk: object, job_list: list[Job], deadline: datetime, venue_list: list[Venue]
) -> tuple[str | None, DeskPlan | None]:
    """Ask the desk for a plan, if *desk* names one. Never raises.

    Returns ``(desk_url, plan)`` -- *desk_url* is set whenever a desk was
    named, even if the plan call itself failed, so the caller can still stamp
    "desk unreachable" on the receipts.
    """
    from . import desk as _desk

    desk_url, key = _desk.resolve_desk(desk)
    if desk_url is None:
        return None, None

    from .quote import estimate_tokens

    input_tokens = output_tokens = 0
    for j in job_list:
        i, o, _, _ = estimate_tokens(j)
        input_tokens += i
        output_tokens += o

    plan = _desk.request_plan(
        desk_url,
        key,
        deadline=deadline,
        models=sorted({j.model for j in job_list}),
        job_count=len(job_list),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        venue_keys=_desk.venue_key_signals([v.name for v in venue_list]),
    )
    return desk_url, plan


def _apply_plan(
    plan: DeskPlan, deadline: datetime, risk_buffer: float, venue_list: list[Venue]
) -> tuple[float, list[Venue]]:
    """Fold a desk's plan into the local risk buffer and venue order.

    ``rescue_at`` overrides the risk buffer directly -- it is the desk's own
    forecast of when a straggler must be rescued, not a fixed fraction of the
    window. A recommended ``venue`` that matches one already on *venue_list*
    is tried first; one that does not match anything the caller configured is
    ignored; the caller's own venues remain the only thing that ever runs.
    """
    if plan.rescue_at:
        try:
            rescue_dt = datetime.fromisoformat(plan.rescue_at)
        except ValueError:
            rescue_dt = None
        if rescue_dt is not None:
            risk_buffer = max(0.0, (deadline - rescue_dt).total_seconds())
    if plan.venue:
        preferred = [v for v in venue_list if v.name == plan.venue]
        if preferred:
            rest = [v for v in venue_list if v.name != plan.venue]
            venue_list = preferred + rest
    return risk_buffer, venue_list


def submit(
    jobs: Job | list[Job],
    deadline: object,
    *,
    venues: list[Venue] | None = None,
    risk_buffer: float | None = None,
    desk: object = None,
) -> Ticket:
    """Submit *jobs* to their venues' batch tiers and return immediately.

    The returned :class:`Ticket` is the run's whole state; keep it (``save()``)
    and finish with :func:`collect` — in this process or another. Raises only
    for programming errors (a bad or past deadline, a model no venue supports);
    a venue that fails at submit is recorded on the ticket and its jobs are
    rescued by the fallback at collect time.

    *desk* opts into the hosted desk: a URL string, or ``True`` to read
    ``OFFPEAK_DESK`` (the key always comes from ``OFFPEAK_KEY``). When set,
    the desk is asked for a plan before anything is submitted -- metadata
    only, never prompts or provider keys -- and its ``rescue_at`` and
    ``venue`` are folded into the local risk buffer and venue order. The desk
    is optional by construction: unreachable, slow, or wrong shape, and this
    behaves exactly as it would with no ``desk=`` at all.
    """
    job_list = [jobs] if isinstance(jobs, Job) else list(jobs)
    resolved = parse_deadline(deadline)
    window = seconds_until(resolved)
    if risk_buffer is None:
        risk_buffer = max(60.0, min(600.0, 0.15 * window))
    venue_list = venues if venues is not None else _default_venues()

    ticket = Ticket(
        jobs=job_list,
        deadline=resolved,
        submitted_at=datetime.now().astimezone(),
        risk_buffer=risk_buffer,
    )
    if not job_list:
        return ticket

    desk_url, plan = _plan_with_desk(desk, job_list, resolved, venue_list)
    if desk_url is not None:
        ticket.desk_url = desk_url
        if plan is not None:
            ticket.desk_plan_id = plan.plan_id
            ticket.risk_buffer, venue_list = _apply_plan(
                plan, resolved, ticket.risk_buffer, venue_list
            )

    groups: dict[str, tuple[Venue, list[Job]]] = {}
    for j in job_list:
        venue = _pick_venue(j.model, venue_list)
        groups.setdefault(venue.name, (venue, []))[1].append(j)
        ticket.assignment[j.id] = venue.name

    for name, (venue, group_jobs) in groups.items():
        try:
            ticket.batches[name] = venue.submit(group_jobs)
        except Exception as exc:  # noqa: BLE001 — the provider failed, not us
            ticket.venue_errors[name] = f"submit failed: {exc}"
            continue
        for j in group_jobs:
            j.status = Status.SUBMITTED
    return ticket


def _venues_by_name(ticket: Ticket, venues: list[Venue] | None) -> dict[str, Venue]:
    venue_list = venues if venues is not None else _default_venues()
    by_name = {v.name: v for v in venue_list}
    for name in set(ticket.assignment.values()):
        if name not in by_name:
            ticket.venue_errors.setdefault(
                name, f"venue {name!r} is not configured in this process"
            )
    return by_name


def status(ticket: Ticket, *, venues: list[Venue] | None = None) -> dict[str, BatchState]:
    """One look at every open batch on *ticket*, keyed by venue name.

    Read-only: nothing is collected, cancelled or rescued. A venue that
    errors while being polled reports a ``failed`` state carrying the message.
    """
    by_name = _venues_by_name(ticket, venues)
    out: dict[str, BatchState] = {}
    for name, handle in ticket.batches.items():
        venue = by_name.get(name)
        if venue is None:
            out[name] = BatchState(status="failed", raw_status="venue not configured")
            continue
        try:
            out[name] = venue.status(handle)
        except Exception as exc:  # noqa: BLE001 — the provider failed, not us
            out[name] = BatchState(status="failed", raw_status=f"status failed: {exc}")
    return out


def _sweep(ticket: Ticket, by_name: dict[str, Venue]) -> None:
    """Poll each open batch once; collect what finished, drop what died."""
    for name in list(ticket.batches):
        venue = by_name.get(name)
        handle = ticket.batches[name]
        if venue is None:
            del ticket.batches[name]
            continue
        try:
            state = venue.status(handle)
            if state.status == "completed":
                for jid, result in venue.collect(handle).items():
                    ticket.collected[jid] = result
                del ticket.batches[name]
            elif state.status in ("failed", "cancelled"):
                ticket.venue_errors[name] = f"batch {state.status}"
                del ticket.batches[name]
        except Exception as exc:  # noqa: BLE001 — the provider failed, not us
            ticket.venue_errors[name] = f"batch polling failed: {exc}"
            del ticket.batches[name]


def _cancel(venue: Venue | None, handle: str) -> None:
    if venue is None:
        return
    try:
        venue.cancel(handle)
    except Exception:  # noqa: BLE001 — the provider failed, not us
        pass


def _run_sync(venue: Venue | None, j: Job, why: str | None) -> Result:
    if venue is None:
        return Result(job=j, error=why or "venue not configured in this process")
    try:
        return venue.run_sync(j)
    except Exception as exc:  # noqa: BLE001 — the provider failed, not us
        return Result(job=j, error=str(exc))


def _settle(ticket: Ticket, by_name: dict[str, Venue], fallback: str) -> list[Result]:
    """Cancel what is still open, rescue stragglers, stamp receipts."""
    for name in list(ticket.batches):
        _cancel(by_name.get(name), ticket.batches.pop(name))

    stragglers = ticket._missing()
    failed_returns = [
        j
        for j in ticket.jobs
        if j.id in ticket.collected and not ticket.collected[j.id].ok and j not in stragglers
    ]
    if (stragglers or failed_returns) and fallback == "sync" and ticket.remaining > 0:
        for j in stragglers:
            name = ticket.assignment[j.id]
            why = ticket.venue_errors.get(name)
            result = _run_sync(by_name.get(name), j, why)
            if result.ok:
                ticket.fell_back.add(j.id)
            elif why:
                result.error = f"{why}; sync fallback failed: {result.error}"
            ticket.collected[j.id] = result
        for j in failed_returns:
            name = ticket.assignment[j.id]
            batch_error = ticket.collected[j.id].error
            result = _run_sync(by_name.get(name), j, None)
            if result.ok:
                ticket.fell_back.add(j.id)
            else:
                result.error = (
                    f"batch returned an error ({batch_error}); sync fallback failed: {result.error}"
                )
            ticket.collected[j.id] = result

    completed_at = datetime.now().astimezone()
    results: list[Result] = []
    for j in ticket.jobs:
        result = ticket.collected.get(j.id)
        if result is None:
            reason = ticket.venue_errors.get(
                ticket.assignment.get(j.id, ""), "not returned by venue before the deadline"
            )
            result = Result(job=j, error=reason)
            j.status = Status.FAILED
        else:
            result.job = j
            j.status = (
                Status.FELL_BACK
                if j.id in ticket.fell_back
                else (Status.SUCCEEDED if result.ok else Status.FAILED)
            )
        input_tokens, output_tokens = _usage_tokens(result.raw)
        result.receipt = Receipt(
            venue=ticket.assignment.get(j.id, "?"),
            model=j.model,
            deadline=ticket.deadline,
            submitted_at=ticket.submitted_at,
            completed_at=completed_at if result.error is None else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            fell_back=j.id in ticket.fell_back,
            paid_fraction=_paid_fraction(result.raw),
        )
        _report_to_desk(ticket, result.receipt)
        results.append(result)
    return results


def _report_to_desk(ticket: Ticket, receipt: Receipt) -> None:
    """Best-effort ``POST .../v1/receipts``; stamps the receipt either way.

    A ticket carries only ``desk_url`` and ``desk_plan_id`` -- never a key, so
    it is re-read from ``OFFPEAK_KEY`` here, which also makes this work when
    a ticket is collected in a different process than the one that submitted
    it.
    """
    if ticket.desk_url is None:
        return
    receipt.desk_host = None
    ok = False
    if ticket.desk_plan_id is not None:
        from . import desk as _desk

        ok = _desk.send_receipt(
            ticket.desk_url, _desk.desk_key(), ticket.desk_plan_id, receipt
        )
        if ok:
            receipt.desk_host = _desk.desk_host(ticket.desk_url)
            receipt.desk_plan_id = ticket.desk_plan_id
    receipt.desk_reachable = ok


def collect(
    ticket: Ticket,
    *,
    venues: list[Venue] | None = None,
    fallback: str = "sync",
    wait: bool = True,
    poll_interval: float | None = None,
) -> list[Result] | None:
    """Finish a submitted run.

    With ``wait=True`` (default) this is the back half of :func:`run`: poll
    until every batch lands or the remaining window shrinks to the ticket's
    risk buffer, then cancel stragglers and rescue them synchronously
    (``fallback="sync"``) or report them failed (``fallback="none"``). Returns
    one :class:`Result` per job, in input order.

    With ``wait=False`` it does one sweep and returns the results only if the
    run can be settled now — everything landed, or the deadline is close
    enough that the buffer rule fires. Otherwise it returns ``None`` with the
    ticket updated (anything that landed is kept on it); call again later.

    Pass the same ``venues`` you submitted with. Venues are matched by name;
    a venue missing from this process fails its jobs with a clear message.
    """
    if not ticket.jobs:
        return []
    by_name = _venues_by_name(ticket, venues)

    while True:
        _sweep(ticket, by_name)
        remaining = ticket.remaining
        if not ticket.pending and not ticket._missing():
            break
        if remaining <= ticket.risk_buffer or not ticket.pending:
            break
        if not wait:
            return None
        time.sleep(
            poll_interval if poll_interval is not None else min(30.0, max(2.0, remaining / 50.0))
        )
    return _settle(ticket, by_name, fallback)
