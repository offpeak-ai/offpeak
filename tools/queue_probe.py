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
from offpeak.venues.groq_batch import COMPLETION_WINDOWS, GroqBatch  # noqa: E402
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
}


def _venue(name: str, window: str):
    if name == "anthropic":
        return AnthropicBatch()
    if name == "openai":
        return OpenAIBatch()
    if name == "groq":
        return GroqBatch(completion_window=window)
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

    A batch still open at ``max_wait`` is **cancelled and recorded as censored**,
    never as having taken ``max_wait``. A lower bound written down as an
    observation is the one way this record could start lying.
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

    deadline = time.monotonic() + max_wait
    state = None
    while True:
        try:
            state = venue.status(handle)
        except Exception as exc:  # noqa: BLE001
            leg.status = "poll_failed"
            leg.note = f"{type(exc).__name__}: {str(exc)[:300]}"
            venue.cancel(handle)
            return leg
        if state.done:
            break
        if time.monotonic() >= deadline:
            venue.cancel(handle)
            leg.status = "censored"
            leg.note = (
                f"still open after {max_wait:.0f}s and cancelled; the batch took "
                f"longer than this run waited, which is a lower bound and not a "
                f"completion time"
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
    "from. A row marked *censored* was still open when the run stopped waiting "
    "and was cancelled — a lower bound, never a completion time.\n\n"
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
        default="anthropic,openai",
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

    session = run_series(
        venues, accepted, cap_usd=a.cap, max_wait=a.max_wait, poll=a.poll
    )
    record = session_record(session)
    path = out / f"{today}-queue.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
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
