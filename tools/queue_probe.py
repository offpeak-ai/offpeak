#!/usr/bin/env python3
"""Queue latency, measured — how long a batch tier actually takes to land.

`offpeak` decides when to abandon a batch on a **fixed** risk buffer: 15% of the
window, clamped to 1–10 minutes. That is a placeholder standing in for a number
nobody has. This tool collects the number.

Two modes, and they answer different questions.

    queue_probe.py --mode windows     # which completion_window strings exist?
    queue_probe.py --mode series      # how long does a batch actually take?

**windows** attempts a batch create at each candidate window and records which
ones the venue accepts. Groq documents "durations from 24h to 7d" and never
enumerates the strings in between, so the seven values in
:data:`~offpeak.venues.groq_batch.COMPLETION_WINDOWS` were guesses. An invalid
window is rejected at create, before any work is queued, so the probe is free —
and any batch that *is* accepted is cancelled on the spot, because a created
batch runs and a running batch bills.

**series** submits a few tiny jobs at each accepted window on each available
venue, polls until they land, and records what it saw: submit time, the window
that was declared, completion time, and the fraction of the declared window
actually used. It spends real money, so it is capped — see below.

Everything here is an **observation**, and the record is deliberately a list of
them rather than a curve. Three data points do not have a distribution, and a
fitted percentile over them would look like knowledge instead of the three
numbers it came from. When there are enough observations to model, the model can
be built from the records; the records do not get to pre-empt it.

Output is per-session JSON plus a table of its own on ``board-data``. It does
**not** touch the quote/mark tables: those are open-data observation that spends
nothing at any venue, and this spends money. Same separation, and same reason,
as ``SETTLED.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import offpeak  # noqa: E402
from offpeak import prices  # noqa: E402
from offpeak.venues.anthropic_batch import AnthropicBatch  # noqa: E402
from offpeak.venues.gemini_batch import GeminiBatch  # noqa: E402
from offpeak.venues.groq_batch import COMPLETION_WINDOWS, GroqBatch  # noqa: E402
from offpeak.venues.mistral_batch import MistralBatch  # noqa: E402
from offpeak.venues.openai_batch import OpenAIBatch  # noqa: E402

# The seven strings that have only ever been guesses. Probed, not assumed.
CANDIDATE_WINDOWS: tuple[str, ...] = ("24h", "48h", "72h", "96h", "120h", "144h", "7d")

WINDOW_SECONDS: dict[str, int] = {
    "24h": 24 * 3600,
    "48h": 48 * 3600,
    "72h": 72 * 3600,
    "96h": 96 * 3600,
    "120h": 120 * 3600,
    "144h": 144 * 3600,
    "7d": 7 * 24 * 3600,
}

# One cent a day, and the tool is what enforces it — not a note in a runbook.
# A leg that would carry the day's total past this does not run, and the record
# says which leg was skipped and by how much it would have overshot.
DEFAULT_CAP_USD = 0.01

# Deliberately tiny and deliberately boring. This measures the queue, not the
# model: the shortest real prompt that still produces a real completion.
PROBE_PROMPT = "Reply with exactly one word: a colour."
PROBE_JOBS_PER_LEG = 2

# gpt-oss reasons a long way past a one-word answer — 10 of 24 came back empty
# at a 256-token ceiling, 4 of 24 at 512 (receipts/2026-08-23-groq-1.json). The
# ceiling here is sized for the model that needs the most room, because an empty
# completion still bills and still takes queue time to produce.
CEILINGS: dict[str, int] = {
    "openai/gpt-oss-20b": 768,
    "mistral-small-latest": 32,
    # Reasoning model: the first live batch spent 13 of a 16-token ceiling
    # thinking and returned nothing. Give it room.
    "gemini-3.7-flash": 512,
    "gpt-5.6-luna": 256,
    "claude-haiku-4-5": 32,
}

# Venue -> (factory, model, the windows it publishes). OpenAI and Anthropic
# publish exactly one window each and take no argument for it; Groq publishes a
# range, which is the whole reason this tool exists.
VENUE_SPECS: dict[str, dict] = {
    "anthropic": {"model": "claude-haiku-4-5", "key": "ANTHROPIC_API_KEY", "windows": ["24h"]},
    "openai": {"model": "gpt-5.6-luna", "key": "OPENAI_API_KEY", "windows": ["24h"]},
    "groq": {"model": "openai/gpt-oss-20b", "key": "GROQ_API_KEY", "windows": None},
    "mistral": {"model": "mistral-small-latest", "key": "MISTRAL_API_KEY", "windows": ["24h"]},
    "gemini": {"model": "gemini-3.7-flash", "key": "GEMINI_API_KEY", "windows": ["24h"]},
}


def _venue(name: str, window: str):
    if name == "anthropic":
        return AnthropicBatch()
    if name == "openai":
        return OpenAIBatch()
    if name == "groq":
        return GroqBatch(completion_window=window)
    if name == "gemini":
        return GeminiBatch()
    if name == "mistral":
        # Mistral's window is a number of hours rather than a string, so the
        # shared "24h" spelling is translated at the boundary.
        return MistralBatch(timeout_hours=int(window.rstrip("h")))
    raise ValueError(f"unknown venue {name!r}")


@dataclass
class WindowProbe:
    """One completion_window string, and what the venue said about it."""

    window: str
    #: True accepted, False rejected as invalid, None the venue would not say.
    accepted: bool | None
    detail: str


@dataclass
class Leg:
    """One measured submission: what was declared, and what actually happened."""

    venue: str
    model: str
    declared_window: str
    declared_window_seconds: int
    jobs: int
    submitted_utc: str | None = None
    completed_utc: str | None = None
    elapsed_seconds: float | None = None
    #: elapsed / declared_window_seconds. The number the risk buffer wants.
    fraction_of_window_used: float | None = None
    status: str = "pending"
    input_tokens: int = 0
    output_tokens: int = 0
    paid_usd: float | None = None
    quoted_list_usd: float = 0.0
    skipped_reason: str | None = None
    note: str | None = None
    #: The venue's batch handle, kept so a leg still open when this run stops
    #: watching can be resolved by a later run instead of cancelled. A batch
    #: that outlives the probe is the observation, not a nuisance.
    handle: str | None = None


def build_book(model: str, jobs: int = PROBE_JOBS_PER_LEG) -> list[offpeak.Job]:
    ceiling = CEILINGS.get(model, 256)
    return [offpeak.job(model, PROBE_PROMPT, max_tokens=ceiling) for _ in range(jobs)]


def probe_completion_windows(
    windows=CANDIDATE_WINDOWS, *, model: str = "openai/gpt-oss-20b", cancel=True
) -> list[WindowProbe]:
    """Ask the venue which window strings it takes, by trying to use them.

    Free by construction: an invalid ``completion_window`` is refused at
    ``batches.create`` before anything is queued. A window that *is* accepted
    has created a live batch, and a live batch bills — so it is cancelled
    immediately, and the probe says so if the cancel itself failed.

    Three outcomes, and the third is not a failure of the probe:

    ``accepted=True``   the venue created a batch at that window
    ``accepted=False``  the venue rejected the string itself
    ``accepted=None``   the venue refused to answer — no entitlement, no key,
                        no network. Recorded as unknown rather than guessed
                        either way, because a 403 says nothing about whether
                        the string was valid.
    """
    out: list[WindowProbe] = []
    for window in windows:
        try:
            venue = _venue("groq", window)
        except ValueError as exc:
            # The driver's own allow-list refused it before the venue saw it.
            out.append(WindowProbe(window, None, f"not offered by the driver: {exc}"))
            continue
        try:
            handle = venue.submit(build_book(model, jobs=1))
        except Exception as exc:  # noqa: BLE001 — every failure is a datum here
            out.append(WindowProbe(window, *_classify(exc)))
            continue
        detail = f"accepted, batch {handle}"
        if cancel:
            try:
                venue.cancel(handle)
                detail += " (cancelled immediately)"
            except Exception as exc:  # noqa: BLE001
                detail += f" (CANCEL FAILED — {exc}; it may bill)"
        out.append(WindowProbe(window, True, detail))
    return out


def _classify(exc: Exception) -> tuple[bool | None, str]:
    """Did the venue reject *the window*, or reject *us*?

    The distinction is the whole probe. A 400 naming the parameter is an answer
    about the string; a 403 is an answer about the account and says nothing
    about whether the string was ever valid.
    """
    text = str(exc)
    status = getattr(exc, "status_code", None)
    if status == 403 or "not_available_for_plan" in text:
        return None, f"unknown — venue refused the caller, not the window: {text[:200]}"
    if status in (400, 422) and re.search(r"completion.?window", text, re.I):
        return False, f"rejected: {text[:200]}"
    if status in (400, 422):
        return None, f"unknown — rejected, but not for the window: {text[:200]}"
    return None, f"unknown — {type(exc).__name__}: {text[:200]}"


def measure_leg(
    venue_name: str,
    window: str,
    *,
    max_wait: float,
    poll: float,
    now=None,
    sleep=time.sleep,
) -> Leg:
    """Submit a tiny book, wait for it to land, and record what was observed.

    The venue is driven directly rather than through :func:`offpeak.run`, on
    purpose. ``run()`` protects a deadline — it cancels a slow batch and pays
    list to keep the SLA — and a instrument that rescues the thing it is timing
    measures the rescue instead.

    A batch still open at ``max_wait`` is recorded as **open** and left running,
    with its handle kept so a later run can resolve it to a real completion
    time. The earlier design cancelled here and wrote "censored" — a lower
    bound — which systematically destroyed exactly the observations the map
    exists for: the slow ones. The tail is the product; cancelling the tail
    measured everything except it.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    spec = VENUE_SPECS[venue_name]
    model = spec["model"]
    jobs = build_book(model)
    leg = Leg(
        venue=venue_name,
        model=model,
        declared_window=window,
        declared_window_seconds=WINDOW_SECONDS[window],
        jobs=len(jobs),
    )

    venue = _venue(venue_name, window)
    started = now()
    leg.submitted_utc = started.isoformat(timespec="seconds")
    try:
        handle = venue.submit(jobs)
    except Exception as exc:  # noqa: BLE001
        leg.status = "submit_failed"
        leg.note = f"{type(exc).__name__}: {str(exc)[:300]}"
        return leg
    leg.handle = handle

    deadline = time.monotonic() + max_wait
    state = None
    while True:
        try:
            state = venue.status(handle)
        except Exception as exc:  # noqa: BLE001
            # A poll failure says nothing about the batch — it is still out
            # there running. Cancelling here would have converted our network
            # blip into the venue's failure. Left open for a later run.
            leg.status = "open"
            leg.note = (
                f"poll failed ({type(exc).__name__}: {str(exc)[:200]}); batch "
                f"left running — a later run resolves it from the handle"
            )
            return leg
        if state.done:
            break
        if time.monotonic() >= deadline:
            leg.status = "open"
            leg.note = (
                f"still open after {max_wait:.0f}s; left running — this run "
                f"stopped watching, the batch did not stop cooking. A later "
                f"run resolves it to a real completion time from the handle."
            )
            return leg
        sleep(poll)

    finished = now()
    leg.completed_utc = finished.isoformat(timespec="seconds")
    leg.elapsed_seconds = round((finished - started).total_seconds(), 1)
    leg.fraction_of_window_used = round(
        leg.elapsed_seconds / leg.declared_window_seconds, 6
    )
    leg.status = state.status

    if state.status != "completed":
        leg.note = f"venue reported {state.status} ({state.failed} failed)"
        return leg

    try:
        results = venue.collect(handle)
    except Exception as exc:  # noqa: BLE001
        leg.note = f"collect failed: {type(exc).__name__}: {str(exc)[:200]}"
        return leg

    for res in results.values():
        usage = res.raw or {}
        leg.input_tokens += int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        leg.output_tokens += int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
    leg.paid_usd = prices.batch_cost_usd(model, leg.input_tokens, leg.output_tokens)
    return leg


@dataclass
class Session:
    """One run of the series, and everything it did or declined to do."""

    date: str
    started_utc: str
    cap_usd: float
    legs: list[Leg] = field(default_factory=list)
    window_probe: list[WindowProbe] = field(default_factory=list)
    spent_usd: float = 0.0
    offpeak_version: str = offpeak.__version__
    price_sheet: str = prices.PRICE_SHEET_DATE


def plan_legs(venues: list[str], accepted_windows: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(venue, window) pairs to measure, in a stable order."""
    plan = []
    for name in venues:
        for window in accepted_windows.get(name) or []:
            plan.append((name, window))
    return plan


def run_series(
    venues: list[str],
    accepted_windows: dict[str, list[str]],
    *,
    cap_usd: float,
    max_wait: float,
    poll: float,
    now=None,
    sleep=time.sleep,
    measure=measure_leg,
) -> Session:
    """Measure every planned leg that fits under the cap; record the rest as skipped.

    The cap is checked against the **quote** — the worst case, priced at the
    ceiling — before each leg, and it is checked against the running total
    rather than per leg. A leg that would carry the day past the cap does not
    run, and says by how much it would have overshot.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    start = now()
    session = Session(
        date=start.date().isoformat(),
        started_utc=start.isoformat(timespec="seconds"),
        cap_usd=cap_usd,
    )
    committed = 0.0
    for venue_name, window in plan_legs(venues, accepted_windows):
        spec = VENUE_SPECS[venue_name]
        model = spec["model"]
        book = build_book(model)
        quoted = _quote_list_usd(book, venue_name, window)
        leg = Leg(
            venue=venue_name,
            model=model,
            declared_window=window,
            declared_window_seconds=WINDOW_SECONDS[window],
            jobs=len(book),
            quoted_list_usd=quoted,
        )
        if not os.environ.get(spec["key"]):
            leg.status = "skipped"
            leg.skipped_reason = f"no {spec['key']} in the environment"
            session.legs.append(leg)
            continue
        if committed + quoted > cap_usd:
            leg.status = "skipped"
            leg.skipped_reason = (
                f"would carry the run to ${committed + quoted:.6f}, over the "
                f"${cap_usd:.4f} cap by ${committed + quoted - cap_usd:.6f}"
            )
            session.legs.append(leg)
            continue
        committed += quoted
        measured = measure(venue_name, window, max_wait=max_wait, poll=poll, now=now, sleep=sleep)
        measured.quoted_list_usd = quoted
        session.legs.append(measured)
        session.spent_usd += measured.paid_usd or 0.0
    return session


def _quote_list_usd(book, venue_name: str, window: str) -> float:
    """Worst-case list price for *book*. No API calls, no key."""
    try:
        q = offpeak.quote(book, "24h", venues=[_venue(venue_name, window)])
    except Exception:  # noqa: BLE001 — an unpriced book quotes as zero, not as a crash
        return 0.0
    return q.list_usd


# ---------------------------------------------------------------------------
# Open-leg resolution: the piece that lets the probe observe the tail.
#
# A leg the run stopped watching is parked here — venue, handle, where its row
# lives — and every subsequent run's first act is to try to resolve the parked
# legs to real outcomes. The original day's record is updated in place and the
# table rebuilt, so the row that said "open" comes to say what actually
# happened, on the day it was submitted. Records stay canonical; this file is
# just the worklist.
# ---------------------------------------------------------------------------

OPEN_FILE = "queue-open.json"

#: How long past the declared window a batch may run before the probe gives up
#: on it: cancelled best-effort, recorded as overran_window. Two days is long
#: enough that the record says "the venue blew its own window by 2x+", which is
#: itself the observation.
ABANDON_GRACE_SECONDS = 48 * 3600


def load_open(outdir: Path) -> list[dict]:
    path = outdir / OPEN_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("open", [])
    except (OSError, ValueError):
        return []


def save_open(outdir: Path, entries: list[dict]) -> None:
    (outdir / OPEN_FILE).write_text(
        json.dumps({"open": entries}, indent=2) + "\n"
    )


def register_open_legs(outdir: Path, record_name: str, session: Session) -> int:
    """Park every open leg of *session* on the worklist. Returns how many."""
    entries = load_open(outdir)
    added = 0
    for index, leg in enumerate(session.legs):
        if leg.status == "open" and leg.handle:
            entries.append(
                {
                    "record": record_name,
                    "leg_index": index,
                    "venue": leg.venue,
                    "model": leg.model,
                    "declared_window": leg.declared_window,
                    "declared_window_seconds": leg.declared_window_seconds,
                    "handle": leg.handle,
                    "submitted_utc": leg.submitted_utc,
                    "last_checked_utc": leg.submitted_utc,
                }
            )
            added += 1
    if added:
        save_open(outdir, entries)
    return added


def _parse_utc(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _update_record_leg(outdir: Path, entry: dict, **fields) -> None:
    """Rewrite one leg of one stored record, and its session spend, in place."""
    path = outdir / entry["record"]
    record = json.loads(path.read_text())
    leg = record["legs"][entry["leg_index"]]
    paid_before = leg.get("paid_usd") or 0.0
    leg.update(fields)
    paid_after = leg.get("paid_usd") or 0.0
    record["spent_usd"] = round(
        (record.get("spent_usd") or 0.0) - paid_before + paid_after, 10
    )
    path.write_text(json.dumps(record, indent=2) + "\n")


def resolve_open(outdir: Path, *, now=None) -> list[str]:
    """Try to settle every parked leg. Returns log lines for the run output.

    Each entry meets one of five fates: **resolved** (the venue reports a
    terminal state — the original row gets its real completion time, from the
    provider's own timestamp when it publishes one, otherwise bounded by our
    check times and labelled as such); **still open** (checked, running,
    within its window — stays parked); **overran** (past declared window plus
    grace — cancelled best-effort and recorded as overran_window, which is the
    finding, not a cleanup); **no key** (can't ask today — stays parked); or
    **check failed** (venue error — stays parked, noted).
    """
    now = now or (lambda: datetime.now(timezone.utc))
    entries = load_open(outdir)
    if not entries:
        return []
    lines: list[str] = []
    still_open: list[dict] = []
    touched_records = False

    for entry in entries:
        label = f"{entry['venue']} @ {entry['declared_window']} ({entry['record']})"
        spec = VENUE_SPECS.get(entry["venue"])
        if spec is None or not os.environ.get(spec["key"]):
            entry["note"] = f"no {spec['key'] if spec else 'known key'} at last check"
            still_open.append(entry)
            lines.append(f"  {label}: key unavailable — still parked")
            continue

        checked = now()
        try:
            venue = _venue(entry["venue"], entry["declared_window"])
            state = venue.status(entry["handle"])
        except Exception as exc:  # noqa: BLE001
            entry["last_checked_utc"] = checked.isoformat(timespec="seconds")
            entry["note"] = f"check failed: {type(exc).__name__}: {str(exc)[:200]}"
            still_open.append(entry)
            lines.append(f"  {label}: check failed ({type(exc).__name__}) — still parked")
            continue

        submitted = _parse_utc(entry.get("submitted_utc"))

        if not state.done:
            deadline_passed = (
                submitted is not None
                and (checked - submitted).total_seconds()
                > entry["declared_window_seconds"] + ABANDON_GRACE_SECONDS
            )
            if deadline_passed:
                try:
                    venue.cancel(entry["handle"])
                except Exception:  # noqa: BLE001 — best-effort
                    pass
                overshoot = (checked - submitted).total_seconds()
                _update_record_leg(
                    outdir,
                    entry,
                    status="overran_window",
                    note=(
                        f"still running {overshoot / 3600:.1f}h after submission "
                        f"against a declared {entry['declared_window']} window; "
                        f"cancelled after window + {ABANDON_GRACE_SECONDS // 3600}h "
                        f"grace. The overrun is the observation."
                    ),
                )
                touched_records = True
                lines.append(f"  {label}: OVERRAN its window — recorded and cancelled")
            else:
                entry["last_checked_utc"] = checked.isoformat(timespec="seconds")
                entry.pop("note", None)
                still_open.append(entry)
                lines.append(f"  {label}: still running — parked again")
            continue

        # Terminal. Pin the completion time: the provider's own stamp when it
        # gives one, else this check time with the bound stated out loud.
        provider_ts = _parse_utc(state.completed_at_utc)
        completed = provider_ts or checked
        note = None
        if provider_ts is None:
            note = (
                f"venue reports no completion timestamp; finished sometime "
                f"between the previous check ({entry.get('last_checked_utc')}) "
                f"and this one — elapsed is an upper bound, not a measurement"
            )
        status = state.status
        if state.raw_status == "expired":
            status = "expired"
            note = (
                (note + "; " if note else "")
                + f"the venue expired the batch at its window with "
                f"{state.completed}/{state.total} done — the partial-completion "
                f"failure mode, observed"
            )

        fields: dict = {
            "status": status,
            "completed_utc": completed.isoformat(timespec="seconds"),
        }
        if submitted is not None and completed >= submitted:
            elapsed = round((completed - submitted).total_seconds(), 1)
            fields["elapsed_seconds"] = elapsed
            fields["fraction_of_window_used"] = round(
                elapsed / entry["declared_window_seconds"], 6
            )

        if state.status == "completed":
            try:
                results = venue.collect(entry["handle"])
                input_tokens = output_tokens = 0
                for res in results.values():
                    usage = res.raw or {}
                    input_tokens += int(
                        usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                    )
                    output_tokens += int(
                        usage.get("completion_tokens") or usage.get("output_tokens") or 0
                    )
                fields["input_tokens"] = input_tokens
                fields["output_tokens"] = output_tokens
                fields["paid_usd"] = prices.batch_cost_usd(
                    entry["model"], input_tokens, output_tokens
                )
            except Exception as exc:  # noqa: BLE001
                note = (note + "; " if note else "") + (
                    f"collect failed: {type(exc).__name__}: {str(exc)[:200]}"
                )
        if note:
            fields["note"] = note

        _update_record_leg(outdir, entry, **fields)
        touched_records = True
        lines.append(
            f"  {label}: resolved {status}"
            + (
                f" in {_fmt_elapsed(fields.get('elapsed_seconds'))}"
                if fields.get("elapsed_seconds") is not None
                else ""
            )
        )

    save_open(outdir, still_open)
    if touched_records:
        rebuild_table(outdir / "QUEUE.md", outdir)
    return lines


QUEUE_HEADER = (
    "# Offpeak queue latency — observed, not modelled\n\n"
    "How long a batch tier actually takes to land, measured by submitting a "
    "couple of tiny jobs and watching the clock. This table spends real money "
    "at real venues and is therefore **not** the Spread Board: that one marks "
    "open grid data and spends nothing. Same separation, and the same reason, "
    "as `SETTLED.md`.\n\n"
    "`offpeak` currently abandons a slow batch on a fixed risk buffer — 15% of "
    "the window, clamped to 1–10 minutes — because nobody had the number. "
    "These are the observations that would replace it.\n\n"
    "**Every row is one submission.** There is no percentile here and no fitted "
    "curve: a handful of observations does not have a distribution, and a model "
    "over them would read as knowledge rather than as the few numbers it came "
    "from. A row marked *open* is a batch still running when the probe stopped "
    "watching — it stays on the venue's queue and the row is rewritten with the "
    "real outcome once a later run resolves it from the stored handle. A row "
    "marked *expired* or *overran_window* is the venue missing its own declared "
    "window — the failure mode this table exists to catch. Early rows marked "
    "*censored* predate resolution: those batches were cancelled at 30 minutes "
    "and are lower bounds, never completion times.\n\n"
    "Written by `tools/queue_probe.py`, never by hand.\n\n"
    "| session | venue | model | declared | jobs | elapsed | % of window | status | paid |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
)

_DATA_ROW = re.compile(r"^\| \d{4}-\d{2}-\d{2} \|")


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def render_rows(record: dict) -> str:
    """One session's legs as table rows. Pure."""
    rows = ""
    for leg in record.get("legs", []):
        pct = leg.get("fraction_of_window_used")
        status = leg.get("status", "?")
        if status == "skipped":
            status = f"skipped — {leg.get('skipped_reason', 'no reason recorded')}"
        paid = leg.get("paid_usd")
        rows += (
            f"| {record['date']} "
            f"| {leg['venue']} "
            f"| {leg['model']} "
            f"| {leg['declared_window']} "
            f"| {leg['jobs']} "
            f"| {_fmt_elapsed(leg.get('elapsed_seconds'))} "
            f"| {'—' if pct is None else f'{pct * 100:.3f}%'} "
            f"| {status} "
            f"| {'—' if paid is None else '$' + offpeak.format_usd(paid)} |\n"
        )
    return rows


def rebuild_table(table: Path, records: Path) -> int:
    """Rewrite the whole table from the stored records. Returns rows written.

    A projection, for the reason #21 established on the Spread Board: a heal
    that fixes the header but not the rows written under the old one is not a
    heal, it is a table whose columns have started lying. A session whose record
    has gone missing loses its rows rather than keeping numbers nobody can
    re-derive.
    """
    rows = []
    for path in sorted(records.glob("*-queue.json")):
        try:
            rows.append(render_rows(json.loads(path.read_text())))
        except (OSError, ValueError) as exc:
            print(f"skipping unreadable record {path.name}: {exc}")
    table.write_text(QUEUE_HEADER + "".join(rows))
    return sum(r.count("\n") for r in rows)


def session_record(session: Session, probe: list[WindowProbe] | None = None) -> dict:
    rec = asdict(session)
    if probe is not None:
        rec["window_probe"] = [asdict(p) for p in probe]
    return rec


def accepted_from_probe(probe: list[WindowProbe]) -> list[str]:
    return [p.window for p in probe if p.accepted]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=["windows", "series"], default="series")
    ap.add_argument("--outdir", default="nightly", help="where the record and table live")
    ap.add_argument(
        "--venues",
        default="anthropic,openai,gemini,mistral",
        help="comma-separated venues to measure (groq is opt-in and needs its own key)",
    )
    ap.add_argument(
        "--groq-windows",
        default="",
        help="comma-separated windows to measure on Groq; empty means none, "
        "because none have been confirmed to exist",
    )
    ap.add_argument("--cap", type=float, default=DEFAULT_CAP_USD, help="hard USD cap per run")
    ap.add_argument("--max-wait", type=float, default=1800.0, help="seconds to wait per leg")
    ap.add_argument("--poll", type=float, default=15.0, help="seconds between polls")
    ap.add_argument("--dry-run", action="store_true", help="plan and quote, submit nothing")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()

    if a.mode == "windows":
        if not os.environ.get("GROQ_API_KEY"):
            print("no GROQ_API_KEY — nothing to probe", flush=True)
            return 1
        probe = probe_completion_windows()
        for p in probe:
            mark = {True: "accepted", False: "rejected", None: "unknown "}[p.accepted]
            print(f"  {p.window:>5}  {mark}  {p.detail}", flush=True)
        accepted = accepted_from_probe(probe)
        print(f"\naccepted: {accepted or '(none confirmed)'}", flush=True)
        path = out / f"{today}-windows.json"
        path.write_text(
            json.dumps(
                {
                    "probed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "candidates": list(CANDIDATE_WINDOWS),
                    "driver_offers": list(COMPLETION_WINDOWS),
                    "results": [asdict(p) for p in probe],
                    "accepted": accepted,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"wrote {path}", flush=True)
        return 0

    venues = [v.strip() for v in a.venues.split(",") if v.strip()]
    unknown = [v for v in venues if v not in VENUE_SPECS]
    if unknown:
        print(f"ABORT: unknown venue(s) {unknown} — known: {sorted(VENUE_SPECS)}")
        return 4

    groq_windows = [w.strip() for w in a.groq_windows.split(",") if w.strip()]
    accepted = {name: (VENUE_SPECS[name]["windows"] or groq_windows) for name in venues}

    if a.dry_run:
        for venue_name, window in plan_legs(venues, accepted):
            book = build_book(VENUE_SPECS[venue_name]["model"])
            print(
                f"  would measure {venue_name} @ {window}: {len(book)} job(s), "
                f"worst case ${_quote_list_usd(book, venue_name, window):.6f}",
                flush=True,
            )
        print(f"--dry-run: cap ${a.cap:.4f}, nothing submitted.", flush=True)
        return 0

    # First act of every run: try to settle what earlier runs left open. The
    # rows this rewrites are the tail of the distribution — the entire reason
    # the probe exists — so resolution runs before any new money is spent.
    resolved_lines = resolve_open(out)
    if resolved_lines:
        print("resolving parked legs:", flush=True)
        for line in resolved_lines:
            print(line, flush=True)

    session = run_series(
        venues, accepted, cap_usd=a.cap, max_wait=a.max_wait, poll=a.poll
    )
    record = session_record(session)
    path = out / f"{today}-queue.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    parked = register_open_legs(out, path.name, session)
    if parked:
        print(f"parked {parked} still-open leg(s) for a later run to resolve", flush=True)
    rebuild_table(out / "QUEUE.md", out)

    for leg in session.legs:
        print(
            f"  {leg.venue:>9} @ {leg.declared_window:<5} {leg.status:<14} "
            f"{_fmt_elapsed(leg.elapsed_seconds):>7} "
            f"{leg.skipped_reason or leg.note or ''}",
            flush=True,
        )
    print(f"\nspent ${session.spent_usd:.6f} of the ${session.cap_usd:.4f} cap", flush=True)
    print(f"wrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
