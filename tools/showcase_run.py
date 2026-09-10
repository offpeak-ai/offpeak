#!/usr/bin/env python3
"""A real settlement run at book scale — the showcase, not the mechanics proof.

``mechanics_run.py`` proves the machinery on two dozen one-line jobs. This runs
the same machinery on real work: a few public-domain novels cut into passages,
one summarisation job per passage, submitted to a single venue's batch tier and
settled against the bundled price sheet.

    showcase_run.py --out run/showcase --dry-run    # quote only, nothing submitted
    showcase_run.py --out run/showcase              # for real

It borrows every guard rail from ``mechanics_run`` and keeps them in the same
order:

1. **A free quote runs first, and it is the gate.** Its ``list_usd`` is the
   worst case — every job falling back to sync and paying list — and if that
   exceeds ``--cap``, nothing is submitted. ``--min-list`` guards the other
   end, so a book that quietly shrank does not get submitted as a showcase.
2. **Handles are recorded before anything else happens**, through the same
   ``Recording`` mixin, so a killed process is still cancellable with
   ``--cancel``. The ticket is saved next to them for the same reason.
3. **Any exception after submission cancels the recorded handles.**
4. **It refuses to re-submit** over an existing handle log.

One rail this adds: the run polls the batch itself and **refuses to settle
silently over a straggler**. ``collect()`` would rescue a late job
synchronously at list, which is the right thing for a deadline and the wrong
thing to discover afterwards in a receipt, so anything not finished when the
poll gives up is reported before collection rather than after.

Novel text is cached under the run directory and is not committed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mechanics_run import VENUES, cancel_all  # noqa: E402

import offpeak  # noqa: E402
from offpeak.quote import CHARS_PER_TOKEN  # noqa: E402

# Five long public-domain novels. Long ones on purpose: 2,000 passages of ~1,300
# tokens needs ~10.4M characters, and five average novels hold about a third of
# that. These five hold ~13M.
BOOKS = {
    2600: "War and Peace — Tolstoy",
    135: "Les Misérables — Hugo",
    996: "Don Quixote — Cervantes",
    1399: "Anna Karenina — Tolstoy",
    145: "Middlemarch — Eliot",
}

GUTENBERG = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"

PROMPT = "Summarize this passage in three sentences.\n\n{passage}"

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_JOBS = 2000
DEFAULT_PASSAGE_TOKENS = 1300
DEFAULT_MAX_TOKENS = 200
DEFAULT_DEADLINE = "12h"
DEFAULT_CAP_USD = 12.00
DEFAULT_MIN_LIST_USD = 6.00

# Gutenberg wraps every text in a licence header and footer. Neither is the
# novel, and neither should be summarised or paid for.
START_RE = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.S)
END_RE = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.S)


def fetch_book(book_id: int, cache_dir: Path) -> str:
    """Download one novel, or read the cached copy. Cached, never committed."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"pg{book_id}.txt"
    if path.exists():
        raw = path.read_text(encoding="utf-8", errors="replace")
        print(f"  pg{book_id}: {len(raw):>9,} chars (cached)", flush=True)
        return raw
    url = GUTENBERG.format(id=book_id)
    req = urllib.request.Request(url, headers={"User-Agent": "offpeak-showcase/1.0"})
    with urllib.request.urlopen(req, timeout=120) as fh:  # noqa: S310
        raw = fh.read().decode("utf-8", errors="replace")
    path.write_text(raw, encoding="utf-8")
    print(f"  pg{book_id}: {len(raw):>9,} chars (fetched)", flush=True)
    return raw


def strip_gutenberg(raw: str) -> str:
    m = START_RE.search(raw)
    if m:
        raw = raw[m.end():]
    m = END_RE.search(raw)
    if m:
        raw = raw[: m.start()]
    return raw.strip()


def passages(text: str, target_chars: int) -> list[str]:
    """Cut *text* into ~*target_chars* pieces, breaking on paragraph boundaries.

    Breaking mid-sentence would make the summarisation task artificial, so a
    passage runs to the end of whatever paragraph crosses the target.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        buf.append(p)
        size += len(p) + 2
        if size >= target_chars:
            out.append("\n\n".join(buf))
            buf, size = [], 0
    if size >= target_chars // 2 and buf:
        out.append("\n\n".join(buf))
    return out


def build_book(args, cache_dir: Path) -> list[offpeak.Job]:
    """One job per passage, drawn round-robin so the sample spans every novel."""
    target_chars = args.passage_tokens * CHARS_PER_TOKEN
    print(f"fetching {len(BOOKS)} novels into {cache_dir}", flush=True)
    per_book: list[list[str]] = []
    for book_id, title in BOOKS.items():
        text = strip_gutenberg(fetch_book(book_id, cache_dir))
        ps = passages(text, target_chars)
        print(f"    {title}: {len(ps):,} passages", flush=True)
        per_book.append(ps)

    total = sum(len(p) for p in per_book)
    if total < args.jobs:
        raise SystemExit(
            f"ABORT: {total:,} passages available, {args.jobs:,} wanted. "
            "Add a novel or lower --passage-tokens."
        )

    interleaved: list[str] = []
    for i in range(max(len(p) for p in per_book)):
        for ps in per_book:
            if i < len(ps):
                interleaved.append(ps[i])
        if len(interleaved) >= args.jobs:
            break

    chosen = interleaved[: args.jobs]
    jobs = [
        offpeak.job(args.model, PROMPT.format(passage=p), max_tokens=args.max_tokens)
        for p in chosen
    ]
    chars = sum(len(PROMPT.format(passage=p)) for p in chosen)
    print(
        f"\nbook: {len(jobs):,} jobs · {chars:,} prompt chars"
        f" · ~{chars // CHARS_PER_TOKEN:,} input tokens"
        f" · ceiling {args.max_tokens} out",
        flush=True,
    )
    return jobs


def poll(ticket, venues, deadline_s: float, interval: float) -> dict:
    """Watch the batch to completion, printing progress. Returns the last states."""
    started = time.monotonic()
    states: dict = {}
    while True:
        states = offpeak.status(ticket, venues=venues)
        elapsed = time.monotonic() - started
        done = sum(1 for s in states.values() if s.done)
        completed = sum(s.completed or 0 for s in states.values())
        total = sum(s.total or 0 for s in states.values())
        raw = ",".join(sorted({str(s.raw_status) for s in states.values()}))
        print(
            f"  [{elapsed / 60:6.1f}m] batches {done}/{len(states)} done"
            f" · jobs {completed:,}/{total:,} · {raw}",
            flush=True,
        )
        if states and all(s.done for s in states.values()):
            return states
        if elapsed > deadline_s:
            print("  poll gave up: deadline window elapsed", flush=True)
            return states
        time.sleep(interval)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="run/showcase")
    ap.add_argument("--run-id", default="2026-09-10-sonnet-showcase-1")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--venue", default="anthropic", choices=sorted(VENUES))
    ap.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    ap.add_argument("--passage-tokens", type=int, default=DEFAULT_PASSAGE_TOKENS)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--deadline", default=DEFAULT_DEADLINE)
    ap.add_argument("--cap", type=float, default=DEFAULT_CAP_USD,
                    help=f"hard cap on list exposure, USD (default {DEFAULT_CAP_USD})")
    ap.add_argument("--min-list", type=float, default=DEFAULT_MIN_LIST_USD,
                    help="refuse to submit below this quoted list, USD")
    ap.add_argument("--poll-interval", type=float, default=60.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cancel", action="store_true")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    out = Path(a.out).expanduser().resolve()
    handles = out / "handles.jsonl"
    cache = out / "books"

    venue_cls, plain_cls = VENUES[a.venue]

    if a.cancel:
        cancel_all(handles, [plain_cls()])
        return 0

    out.mkdir(parents=True, exist_ok=True)
    if handles.exists():
        print(f"ABORT: {handles} already exists — refusing to re-submit over a "
              "recorded run. Move it aside if this is deliberate.", flush=True)
        return 3

    jobs = build_book(a, cache)

    venue = venue_cls()
    venue.log = handles

    # --- Gate: price it before spending anything. No API calls here. ---
    q = offpeak.quote(jobs, a.deadline, venues=[venue])
    card = str(q)
    print("\n" + card, flush=True)
    (out / "quote.txt").write_text(card + "\n")

    print(
        f"\ncap check: worst case (all sync at list) ${q.list_usd:.4f}"
        f" vs hard cap ${a.cap:.2f} and floor ${a.min_list:.2f}",
        flush=True,
    )
    if q.list_usd > a.cap:
        print("ABORT: over the hard cap. Nothing submitted.", flush=True)
        return 2
    if q.list_usd < a.min_list:
        print("ABORT: under the showcase floor — resize passages and re-quote. "
              "Nothing submitted.", flush=True)
        return 2
    if a.dry_run:
        print("--dry-run: inside the window, stopping before submit.", flush=True)
        return 0
    print("inside the window — proceeding to submit\n", flush=True)

    started = datetime.now().astimezone()
    try:
        ticket = offpeak.submit(jobs, a.deadline, venues=[venue])
        ticket.save(out / "ticket.json")
        print(f"  ticket saved to {out / 'ticket.json'}", flush=True)

        deadline_s = offpeak.seconds_until(offpeak.parse_deadline(a.deadline))
        states = poll(ticket, [venue], deadline_s, a.poll_interval)

        stragglers = {k: s for k, s in states.items() if not s.done}
        failed = {k: s for k, s in states.items() if (s.failed or 0)}
        if stragglers or failed:
            print("\n!! NOT SETTLING SILENTLY", flush=True)
            for k, s in stragglers.items():
                print(f"   straggler {k}: {s.raw_status} "
                      f"{s.completed or 0}/{s.total or 0}", flush=True)
            for k, s in failed.items():
                print(f"   failures  {k}: {s.failed} of {s.total or 0}", flush=True)
            print("   collect() would rescue these synchronously at list price.",
                  flush=True)
        else:
            print("\nall batches complete, no stragglers — collecting", flush=True)

        results = offpeak.collect(ticket, venues=[venue])
    except BaseException:
        traceback.print_exc()
        print("\ncancelling recorded handles server-side...", flush=True)
        cancel_all(handles, [plain_cls()])
        raise
    finished = datetime.now().astimezone()

    settlement = offpeak.receipt(results)
    print("\n" + str(settlement), flush=True)
    (out / "settlement.txt").write_text(str(settlement) + "\n")

    per_job = [
        {
            "job_id": r.job.id,
            "venue": r.receipt.venue,
            "model": r.receipt.model,
            "ok": r.ok,
            "error": r.error,
            "input_tokens": r.receipt.input_tokens,
            "output_tokens": r.receipt.output_tokens,
            "list_usd": r.receipt.list_usd,
            "paid_usd": r.receipt.paid_usd,
            "fell_back": r.receipt.fell_back,
            "sla_met": r.receipt.sla_met,
        }
        for r in results
    ]

    venue_handles: dict[str, list[str]] = {}
    if handles.exists():
        for line in handles.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                venue_handles.setdefault(e["venue"], []).append(e["handle"])

    record = {
        "run_id": a.run_id,
        "venue_handles": venue_handles,
        "scale": f"showcase ({settlement.total:,} jobs, {a.model}, batch tier)",
        "settled_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "deadline": a.deadline,
        "price_sheet": offpeak.prices.PRICE_SHEET_DATE,
        "offpeak_version": offpeak.__version__,
        "hard_cap_usd": a.cap,
        "quoted_list_usd": q.list_usd,
        "quoted_batch_usd": q.batch_usd,
        "jobs": settlement.total,
        "ok": settlement.ok,
        "failed": settlement.failed,
        "fell_back": settlement.fell_back,
        "sla_met": settlement.sla_met,
        "input_tokens": settlement.input_tokens,
        "output_tokens": settlement.output_tokens,
        "list_usd": settlement.list_usd,
        "paid_usd": settlement.paid_usd,
        "captured_usd": settlement.captured_usd,
        "captured_pct": settlement.captured_pct,
        "left_on_table_usd": settlement.left_on_table_usd,
        "by_venue": settlement.by_venue,
        "per_job": per_job,
    }
    (out / "settlement.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nwrote {out}/settlement.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
