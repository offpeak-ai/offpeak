"""DeepSeek — a clock-priced venue, not a batch one.

DeepSeek publishes **no batch API**. What it publishes instead is a clock:
peak hours are 01:00–04:00 and 06:00–10:00 UTC, Monday through Friday, and
every other hour — evenings, weekends, the gap between the two blocks — is
off-peak, at **half the peak rate** on input, output and cache-hit alike.
Read off the rendered page at api-docs.deepseek.com/quick_start/pricing on
2026-08-28, and re-read on 2026-08-30 to confirm the schedule wording and the
per-model columns; DeepSeek's own announcement puts the current schedule in
effect from 2026-08-23.

That is the same 2.0x spread the batch venues sell, priced on a different
axis. A batch tier discounts *how long you are willing to wait*; DeepSeek
discounts *when the request lands*. There is nothing to upload and nothing to
poll — the discount is a property of the wall clock at the moment
``chat.completions`` is called. So this driver does not batch. It **holds**:

* :meth:`DeepSeekClock.submit` sends nothing. It records the jobs with a
  ``release_at`` — now, if now is off-peak, else the end of the current peak
  block — and hands back an in-process handle. Holding is the whole mechanism:
  a job released at 04:00 UTC costs half what the same job cost at 03:59, and
  the only way to buy that is to not send it yet.
* :meth:`DeepSeekClock.status` is where the work happens. Before ``release_at``
  it reports ``in_progress``. At or after it, it runs every held job through
  ``chat.completions`` — one request per job, a small thread pool, stdlib
  only — stores the results, and reports ``completed`` with the submit stamp
  as ``created_at_utc`` and the last return as ``completed_at_utc``.
* :meth:`DeepSeekClock.collect` returns what ``status`` stored.
* :meth:`DeepSeekClock.cancel` drops a hold. Once executed there is nothing
  remote to cancel: every request has already returned.
* :meth:`DeepSeekClock.run_sync` is the deadline fallback and runs **now**, at
  whatever rate the clock says now. That may be peak. It is the honest outcome
  of a deadline that could not wait for the boundary, and the result says so.

``run()`` therefore works unchanged: it submits, polls ``status``, collects,
and rescues stragglers through ``run_sync`` under the risk buffer, exactly as
it does for a batch venue. The longest a hold can last is four hours — the
06:00–10:00 block — so a deadline that clears the next boundary captures the
spread and a deadline that does not takes the fallback, which is the same
contract the batch venues keep with their 24h windows.

Settlement
----------

The bundled sheet stores DeepSeek's **peak** rate as the standard row, and the
library's ``BATCH_DISCOUNT`` rule reproduces the off-peak rate exactly — half
of $0.44 / $1.32 is $0.22 / $0.66, which is what the page prints. So a job
executed off-peak settles through the same arithmetic as a batched job, and a
job that fell back at peak settles at list, and neither needs a special case.

The case that does is a fallback that happened to land off-peak: a job the
hold failed to return (a 5xx, a timeout) is rescued through ``run_sync`` while
the clock is still cheap, and it pays half of list even though it ``fell_back``.
``Receipt.paid_usd`` would otherwise book that as list. So every ``Result``
this driver produces carries the regime it ran under in ``raw`` —
``"regime"`` (``"off_peak"`` or ``"peak"``), ``"rate_multiplier"`` (1.0 or
2.0), ``"paid_fraction"`` (0.5 or 1.0), and ``"executed_at_utc"`` — stamped
**per request, at the moment it was made**, not per hold. ``run()`` copies
``paid_fraction`` onto the receipt, where it outranks the batch/fallback rule.
A hold that releases at 00:55 UTC and takes six minutes to drain has its last
jobs priced at peak, and the receipts say so.

Why the regime is stamped per request rather than derived later from the
timestamp: the price is whatever the clock said when the request was made,
and re-deriving it from a stamp would silently move the moment the schedule
moves. The stamp is the observation; the regime is the fact it recorded.

What is not modelled: **cache hits**. DeepSeek prices a cache-hit input token
at $0.007 / $0.022 off-peak — an order of magnitude under the miss rate — and
reports hits in ``usage.prompt_cache_hit_tokens``. The sheet has no cache
dimension, so every input token here is priced at the miss rate and a receipt
for a cache-heavy run **overstates** what was paid. That is the conservative
direction, and it is stated rather than hidden.

The API
-------

OpenAI-compatible at ``https://api.deepseek.com``, so the ``openai`` SDK is
the client (``pip install "offpeak[deepseek]"``) and the key is read from
``DEEPSEEK_API_KEY``. Two things about the V4 family are worth knowing before
setting a ceiling:

* **Thinking is on by default.** The model reasons before it answers and the
  reasoning is billed as output. This driver leaves that at the venue's
  default rather than fighting it — it is the model's behaviour, and a job
  that wants it off can say so through its own params — but it means a
  small ``max_tokens`` buys the reasoning and an empty answer. Size the
  ceiling for a model that thinks. The context window is 1M tokens and the
  output ceiling 384K.
* ``max_tokens`` is DeepSeek's own spelling and is passed through untouched.
  The ``max_completion_tokens`` rewrite the OpenAI driver applies to newer
  OpenAI families is not applied here.

.. warning::
   **Unverified live.** No request has yet been made through this driver to
   ``api.deepseek.com``: the clock helpers, the hold, the settlement and the
   request shape are exercised network-free, and every price is a
   transcription of the page. The plan-gating lesson from Groq (403), Mistral
   (402) and Gemini (billing) is that the first sub-cent live run is what
   verifies a driver, and this one has not had it. When it does, the receipt
   goes in ``receipts/`` and this warning comes out.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from ..job import Job, Result
from ..prices import BATCH_DISCOUNT
from .base import BatchState, Venue, iso_utc

__all__ = [
    "DeepSeekClock",
    "BASE_URL",
    "PEAK_BLOCKS_UTC",
    "is_peak",
    "next_offpeak_start",
    "offpeak_until",
    "rate_multiplier",
    "paid_fraction",
]

BASE_URL = "https://api.deepseek.com"

#: Peak hours, UTC, Monday through Friday, as half-open ``[start, end)``
#: blocks. Everything outside them — including the whole weekend — is off-peak.
#: DeepSeek states the schedule in both UTC and Beijing time; the blocks are
#: the same instants either way, and UTC is the frame every other stamp in
#: this library uses.
PEAK_BLOCKS_UTC: tuple[tuple[time, time], ...] = (
    (time(1, 0), time(4, 0)),
    (time(6, 0), time(10, 0)),
)

#: What an off-peak request pays as a fraction of the peak (standard) rate.
#: It is the same number as the batch rule on purpose: DeepSeek's "half of the
#: peak rates" and the batch venues' "50% of list" are the same spread, and
#: sharing the constant is what lets ``batch_cost_usd`` settle an off-peak job
#: with no special case.
OFFPEAK_FRACTION = BATCH_DISCOUNT

_MODEL_PREFIXES = ("deepseek-",)

Clock = Callable[[], datetime]


def _utc(dt: datetime) -> datetime:
    """*dt* as an aware UTC datetime. A naive value is taken as already UTC —
    the clock helpers are documented in UTC and a naive local time would
    otherwise shift the schedule by the caller's offset."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_peak(dt_utc: datetime) -> bool:
    """Whether *dt_utc* falls inside a peak block.

    Weekday and time are read in UTC. Block ends are exclusive — 04:00:00 is
    off-peak, 03:59:59 is peak — and Saturday and Sunday are off-peak at every
    hour.
    """
    dt = _utc(dt_utc)
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return any(start <= t < end for start, end in PEAK_BLOCKS_UTC)


def next_offpeak_start(dt_utc: datetime) -> datetime:
    """The instant a job submitted at *dt_utc* can run off-peak.

    *dt_utc* itself when it is already off-peak — a hold placed at an off-peak
    moment releases immediately. Otherwise the end of the peak block *dt_utc*
    is inside, which is always later the same UTC day: the blocks never cross
    midnight, so the wait is at most four hours.
    """
    dt = _utc(dt_utc)
    if not is_peak(dt):
        return dt
    t = dt.time()
    for start, end in PEAK_BLOCKS_UTC:
        if start <= t < end:
            return dt.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    raise AssertionError("is_peak and PEAK_BLOCKS_UTC disagree")  # pragma: no cover


def offpeak_until(dt_utc: datetime) -> datetime | None:
    """When the off-peak stretch containing *dt_utc* ends — the next peak
    block's start — or ``None`` when *dt_utc* is at peak and so is not inside
    an off-peak stretch at all.

    The weekend is one stretch: Friday 10:00 UTC through Monday 01:00 UTC, and
    a Saturday timestamp answers Monday 01:00. The gap between the two weekday
    blocks is a stretch too — 04:00 answers 06:00 the same day.
    """
    dt = _utc(dt_utc)
    if is_peak(dt):
        return None
    # Scan forward day by day for the first block start strictly after dt on a
    # weekday. Eight days is the longest gap that can ever exist (Friday 10:00
    # to Monday 01:00 is under three), so the bound is never reached.
    for offset in range(8):
        day = dt + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for start, _ in PEAK_BLOCKS_UTC:
            candidate = day.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            if candidate > dt:
                return candidate
    raise AssertionError("no peak block found within eight days")  # pragma: no cover


def rate_multiplier(dt_utc: datetime) -> float:
    """What a request at *dt_utc* pays relative to the off-peak rate: 2.0 at
    peak, 1.0 off-peak. The off-peak rate is the floor of the schedule, so the
    multiplier is the price of the hour in the same sense
    :func:`~offpeak.prices.urgency_spread` is."""
    return 2.0 if is_peak(dt_utc) else 1.0


def paid_fraction(dt_utc: datetime) -> float:
    """What a request at *dt_utc* pays as a fraction of the sheet's standard
    (peak) rate: 1.0 at peak, :data:`OFFPEAK_FRACTION` off-peak. This is the
    figure a receipt settles on."""
    return 1.0 if is_peak(dt_utc) else OFFPEAK_FRACTION


@dataclass
class _Hold:
    """One submitted book, waiting for the clock."""

    jobs: list[Job]
    submitted_at: datetime
    release_at: datetime
    results: dict[str, Result] | None = None
    completed_at: datetime | None = None
    #: Set by cancel(). A cancelled hold that is later polled reports
    #: cancelled rather than quietly executing.
    cancelled: bool = False


class DeepSeekClock(Venue):
    """DeepSeek's clock-priced tier: hold until off-peak, then run.

    *clock* is a callable returning an aware UTC ``datetime``; it exists so
    the hold can be tested against fixed instants. *max_workers* bounds the
    thread pool that drains a released hold.
    """

    name = "deepseek:clock"

    def __init__(
        self,
        client: object | None = None,
        *,
        clock: Clock | None = None,
        max_workers: int = 8,
    ):
        if max_workers < 1:
            raise ValueError(f"max_workers must be at least 1, got {max_workers!r}")
        self._client = client
        self._clock: Clock = clock or _now_utc
        self._max_workers = max_workers
        self._holds: dict[str, _Hold] = {}

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    'DeepSeek venue requires the openai SDK: pip install "offpeak[deepseek]"'
                ) from exc
            key = os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                # The OpenAI client falls back to OPENAI_API_KEY when handed
                # None, which would send an OpenAI key to DeepSeek. Refuse
                # early and name the variable instead.
                raise RuntimeError("DeepSeek venue needs DEEPSEEK_API_KEY in the environment")
            self._client = OpenAI(api_key=key, base_url=BASE_URL)
        return self._client

    def now(self) -> datetime:
        """The venue's idea of now, in UTC."""
        return _utc(self._clock())

    def supports(self, model: str) -> bool:
        return model.startswith(_MODEL_PREFIXES)

    # -- the hold ---------------------------------------------------------- #

    def submit(self, jobs: list[Job]) -> str:
        """Hold *jobs* until the clock is off-peak. Sends nothing.

        The discount is for *when* a request runs, not for how long the caller
        waited, so there is nothing to give the venue yet: sending now at peak
        would pay peak. The handle is an in-process id and is meaningless to
        DeepSeek — a hold does not survive the process, which is the honest
        limit of a venue with no server-side queue.
        """
        now = self.now()
        handle = f"hold_{uuid.uuid4().hex[:12]}"
        self._holds[handle] = _Hold(
            jobs=list(jobs), submitted_at=now, release_at=next_offpeak_start(now)
        )
        return handle

    def status(self, handle: str) -> BatchState:
        hold = self._hold(handle)
        created = iso_utc(hold.submitted_at)
        if hold.cancelled:
            return BatchState(
                status="cancelled",
                total=len(hold.jobs),
                raw_status="hold_dropped",
                created_at_utc=created,
            )
        if hold.results is None:
            if self.now() < hold.release_at:
                return BatchState(
                    status="in_progress",
                    total=len(hold.jobs),
                    raw_status="held_for_off_peak",
                    created_at_utc=created,
                )
            self._execute(hold)
        assert hold.results is not None
        failed = sum(1 for r in hold.results.values() if r.error is not None)
        return BatchState(
            status="completed",
            completed=len(hold.results) - failed,
            failed=failed,
            total=len(hold.jobs),
            raw_status="off_peak_executed",
            completed_at_utc=iso_utc(hold.completed_at),
            created_at_utc=created,
        )

    def collect(self, handle: str) -> dict[str, Result]:
        hold = self._hold(handle)
        if hold.results is None:
            raise RuntimeError(f"hold {handle} has not been released yet")
        # Detached from the job, as the other drivers hand them back: run()
        # attaches its own Job object and settles the receipt from raw.
        return {
            job_id: Result(job=None, text=r.text, raw=r.raw, error=r.error)
            for job_id, r in hold.results.items()
        }

    def cancel(self, handle: str) -> None:
        hold = self._holds.get(handle)
        if hold is None:
            return
        hold.cancelled = True
        if hold.results is None:
            # Never sent, so nothing to unsend; dropping it is the cancel.
            self._holds.pop(handle, None)

    def _hold(self, handle: str) -> _Hold:
        try:
            return self._holds[handle]
        except KeyError:
            raise KeyError(
                f"unknown hold {handle!r}: DeepSeek holds live in this process only"
            ) from None

    def _execute(self, hold: _Hold) -> None:
        """Drain a released hold: one request per job, stamped as it goes."""
        jobs = hold.jobs
        if len(jobs) == 1 or self._max_workers == 1:
            results = [self._call(j) for j in jobs]
        else:
            with ThreadPoolExecutor(max_workers=min(self._max_workers, len(jobs))) as pool:
                results = list(pool.map(self._call, jobs))
        hold.results = {r.job.id: r for r in results}
        hold.completed_at = self.now()

    # -- the request ------------------------------------------------------- #

    def run_sync(self, job: Job) -> Result:
        """Run *job* now, at the rate the clock says now.

        This is the deadline fallback. It does not wait for the boundary — a
        deadline that could wait would still be on hold — so it may run at
        peak and pay list. The result's ``raw`` records which.
        """
        return self._call(job)

    def _call(self, job: Job) -> Result:
        # The regime is read once, immediately before the request, and
        # travels with the result. See the module docstring on why it is a
        # stamp and not a later derivation.
        at = self.now()
        regime = "peak" if is_peak(at) else "off_peak"
        stamp = {
            "regime": regime,
            "rate_multiplier": rate_multiplier(at),
            "paid_fraction": paid_fraction(at),
            "executed_at_utc": iso_utc(at),
        }
        try:
            response = self.client.chat.completions.create(
                model=job.model, messages=job.messages, **job.params
            )
        except Exception as exc:  # noqa: BLE001 — the provider failed, not us
            return Result(job=job, raw=stamp, error=str(exc))
        try:
            text = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            return Result(job=job, raw=stamp, error=f"unexpected response: {response!r}")
        usage = getattr(response, "usage", None)
        return Result(
            job=job,
            text=text,
            raw={
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                # Reported, not priced: the sheet has no cache dimension.
                "prompt_cache_hit_tokens": getattr(usage, "prompt_cache_hit_tokens", 0) or 0,
                **stamp,
            },
        )
