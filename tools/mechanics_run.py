#!/usr/bin/env python3
"""A capped, real settlement run — the mechanics proof, not a production book.

This is what produced the receipts in ``receipts/``. It spends real money at
real venues, which is the point: a receipt whose runner nobody can see is a
number you are asked to trust.

A few dozen small jobs across the cheapest lane at each venue, submitted for
real, polled for real, settled against the bundled price sheet. Point it at
your own keys and it will settle your own receipt.

    mechanics_run.py --out run1 --dry-run     # quote only, nothing submitted
    mechanics_run.py --out run1               # for real

Guard rails, in order:

1. **A free quote runs first, and it is the gate.** Its ``list_usd`` is the
   worst case the run can reach — every job falling back to sync and paying
   list — and if that plus what the session has already exposed exceeds
   ``--cap``, nothing is submitted at all.
2. **Handles are recorded before anything else happens.** Every batch handle
   goes to ``handles.jsonl`` the instant the venue returns it, so a killed
   process is still cancellable. Killing this process does not cancel a batch;
   only the provider API does, which is what ``--cancel`` is for.
3. **Any exception after submission cancels the recorded handles** through the
   provider APIs before re-raising.
4. **It refuses to re-submit** over an existing handle log.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import offpeak  # noqa: E402
from offpeak.venues.anthropic_batch import AnthropicBatch  # noqa: E402
from offpeak.venues.openai_batch import OpenAIBatch  # noqa: E402

DEFAULT_CAP_USD = 0.05  # on total list exposure, not per run
DEFAULT_DEADLINE = "06:00"
# Sized for a model that reasons before it answers: a ceiling smaller than the
# reasoning buys a bill and an empty string. See the quickstart's warning.
DEFAULT_MAX_TOKENS = 256

# Twenty-four short public-domain lines. Real text, real work, tiny.
LINES = [
    "It was the best of times, it was the worst of times.",
    "Call me Ishmael.",
    "All happy families are alike.",
    "It is a truth universally acknowledged.",
    "The past is a foreign country.",
    "In the beginning the universe was created.",
    "It was a bright cold day in April.",
    "Happy families are all alike; every unhappy family is unhappy in its own way.",
    "The sky above the port was the color of television.",
    "It was a pleasure to burn.",
    "Many years later he was to remember that distant afternoon.",
    "I am an invisible man.",
    "Mother died today.",
    "There was no possibility of taking a walk that day.",
    "You don't know about me without you have read a book.",
    "It was love at first sight.",
    "The sun shone, having no alternative, on the nothing new.",
    "He was an old man who fished alone in a skiff.",
    "Someone must have slandered Josef K.",
    "It was a queer, sultry summer.",
    "Lolita, light of my life.",
    "A screaming comes across the sky.",
    "The snow began to fall again as he walked home.",
    "Ships at a distance have every man's wish on board.",
]

PROMPT = (
    "Reply with exactly one word: the dominant mood of this line.\n\nLine: {line}"
)


class Recording:
    """Mixin that writes a batch handle to disk the moment it exists."""

    log: Path

    def submit(self, jobs):  # type: ignore[override]
        handle = super().submit(jobs)
        with self.log.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "venue": self.name,
                        "handle": handle,
                        "jobs": len(jobs),
                        "submitted_utc": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
                + "\n"
            )
            fh.flush()
        print(f"  submitted {len(jobs)} job(s) to {self.name}: {handle}", flush=True)
        return handle


class RecordingAnthropic(Recording, AnthropicBatch):
    pass


class RecordingOpenAI(Recording, OpenAIBatch):
    pass


def build_book(models, max_tokens=DEFAULT_MAX_TOKENS) -> list[offpeak.Job]:
    """The same twenty-four lines through each venue's cheapest model."""
    jobs = []
    for model in models:
        for line in LINES:
            jobs.append(
                offpeak.job(model, PROMPT.format(line=line), max_tokens=max_tokens)
            )
    return jobs


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="run", help="directory for this run's artifacts")
    ap.add_argument(
        "--models",
        default="claude-haiku-4-5,gpt-5.6-luna",
        help="comma-separated models, one lane each",
    )
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--deadline", default=DEFAULT_DEADLINE)
    ap.add_argument(
        "--cap",
        type=float,
        default=DEFAULT_CAP_USD,
        help=f"hard cap on total list exposure, USD (default {DEFAULT_CAP_USD})",
    )
    ap.add_argument(
        "--already-spent",
        type=float,
        default=0.0,
        help="list USD already exposed this session; the cap is on the total",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="quote and check the cap, then stop without submitting",
    )
    ap.add_argument(
        "--cancel",
        action="store_true",
        help="cancel the batches in --out's handle log, server-side, and exit",
    )
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)

    global OUT
    OUT = Path(a.out).expanduser().resolve()
    handles = OUT / "handles.jsonl"

    if a.cancel:
        cancel_all(handles, [AnthropicBatch(), OpenAIBatch()])
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    if handles.exists():
        print(f"ABORT: {handles} already exists — refusing to re-submit over a "
              "recorded run. Move it aside if this is deliberate.", flush=True)
        return 3

    jobs = build_book(
        [m.strip() for m in a.models.split(",") if m.strip()], a.max_tokens
    )
    print(f"book: {len(jobs)} jobs\n", flush=True)

    # --- Gate 1: price it before spending anything. No API calls here. ---
    q = offpeak.quote(jobs, a.deadline)
    card = str(q)
    print(card, flush=True)
    (OUT / "quote.txt").write_text(card + "\n")

    exposure = q.list_usd + a.already_spent
    print(
        f"\ncap check: worst case (all sync at list) ${q.list_usd:.6f}"
        f" + already exposed ${a.already_spent:.6f}"
        f" = ${exposure:.6f} vs hard cap ${a.cap:.4f}",
        flush=True,
    )
    if exposure > a.cap:
        print("ABORT: over the hard cap. Nothing submitted.", flush=True)
        return 2
    if a.dry_run:
        print("--dry-run: under cap, stopping before submit.", flush=True)
        return 0
    print("under cap — proceeding to submit\n", flush=True)

    venues = [RecordingAnthropic(), RecordingOpenAI()]
    for v in venues:
        v.log = handles

    started = datetime.now().astimezone()
    try:
        results = offpeak.run(jobs, a.deadline, venues=venues)
    except BaseException:
        # Nothing should reach here — run() captures provider failures — but a
        # local error or a Ctrl-C must not leave batches running at a venue.
        traceback.print_exc()
        print("\ncancelling recorded handles server-side...", flush=True)
        cancel_all(handles, venues)
        raise
    finished = datetime.now().astimezone()

    settlement = offpeak.receipt(results)
    print("\n" + str(settlement), flush=True)
    (OUT / "settlement.txt").write_text(str(settlement) + "\n")

    per_job = []
    for r in results:
        rec = r.receipt
        per_job.append(
            {
                "job_id": r.job.id,
                "venue": rec.venue,
                "model": rec.model,
                "ok": r.ok,
                "error": r.error,
                "text": (r.text or "").strip(),
                "input_tokens": rec.input_tokens,
                "output_tokens": rec.output_tokens,
                "list_usd": rec.list_usd,
                "paid_usd": rec.paid_usd,
                "fell_back": rec.fell_back,
                "sla_met": rec.sla_met,
            }
        )

    record = {
        "run": "capped settlement run (mechanics proof)",
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
    (OUT / "settlement.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nwrote {OUT}/settlement.json", flush=True)
    return 0


def cancel_all(handles: Path, venues) -> None:
    if not handles.exists():
        print("  no handles recorded", flush=True)
        return
    by_name = {v.name: v for v in venues}
    for line in handles.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        venue = by_name.get(entry["venue"])
        if venue is None:
            print(f"  {entry['venue']} {entry['handle']}: no venue to cancel with")
            continue
        venue.cancel(entry["handle"])
        print(f"  cancel requested: {entry['venue']} {entry['handle']}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
