#!/usr/bin/env python3
"""Generate a synthetic job log, shaped like a mid-size AI-native shop.

Exists so `tools/flexibility_report.py` ships with a worked example that anyone
can regenerate and check, without a real customer's spend in the repository.

    make_synthetic_log.py --out examples/synthetic-job-log.json

**The output is fake and says so** — the file carries a `_synthetic` block, and
the report rendered from it carries a banner in its own header rather than only
in a commit message. A synthetic number that loses its label on the way to a
slide is worse than no example at all.

The shape is deliberate, not random. Four workloads that a shop of this size
actually has, chosen because they classify differently:

- **evals per release** — big, bursty, and given a real deadline days out. The
  textbook deferrable workload.
- **embedding backfill** — a standing recurring job with a week of slack. Cheap
  per token and enormous in aggregate, which is where the money hides.
- **weekly report generation** — a scheduled write-up with a day and a half of
  slack. Deferrable, but only just; it is here to sit near the boundary.
- **the interactive control group** — a product surface with a human waiting.
  It carries no deadline and **must** classify as non-deferrable. It is the
  control: if a report ever counts this as savable, the report is broken.

Deliberately mixed so the report has to cope: token counts arrive as totals on
some rows and as request-count-times-average on others, one venue is left off
and has to be derived from the model name, one row is already running on a
batch tier so its saving must be recognised as *already captured*, one runs on
a venue with no batch tier at all, and one runs a model that is not on the
bundled price sheet.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A fixed anchor, so the file regenerates byte-identically. A synthetic example
# that churns on every run cannot be diffed, and an example nobody can diff
# stops being checked.
ANCHOR = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)

DAY = 86400


def _at(days: float) -> str:
    return (ANCHOR + timedelta(days=days)).isoformat(timespec="seconds")


def build_log() -> dict:
    rows = []

    # --- evals per release: four releases, four weeks, deadline 3 days out ---
    for i, day in enumerate((0, 7.1, 14.2, 21.3)):
        rows.append(
            {
                "job_class": "release evals",
                "model": "claude-sonnet-5",
                "venue": "anthropic",
                "venue_tier": "standard",
                "requests": 4200,
                "avg_input_tokens": 3100,
                "avg_output_tokens": 420,
                "submitted_at": _at(day),
                "required_by": _at(day + 3),
                "note": f"release r-{i + 1}",
            }
        )

    # --- embedding backfill: recurring, a week of slack, already batched ---
    for day in (1, 8, 15, 22):
        rows.append(
            {
                "job_class": "embedding backfill",
                "model": "text-embedding-3-large",  # deliberately off the sheet
                "venue_tier": "batch",
                "requests": 180_000,
                "avg_input_tokens": 512,
                "avg_output_tokens": 0,
                "submitted_at": _at(day),
                "required_by": _at(day + 7),
            }
        )

    # --- summarisation backfill: the big one, and half of it already batched.
    # A fleet that has partly adopted batch is the normal case, and it is the
    # case where a flat "you could save 50%" is most wrong.
    for day, tier in ((2, "on_demand"), (9, "batch"), (16, "on_demand"), (23, "batch")):
        rows.append(
            {
                "job_class": "corpus summarisation",
                "model": "gpt-5.6-luna",
                # venue omitted on purpose — derived from the model name.
                "venue_tier": tier,
                "requests": 26_000,
                "avg_input_tokens": 4800,
                "avg_output_tokens": 300,
                "submitted_at": _at(day),
                "required_by": _at(day + 5),
            }
        )

    # --- weekly report generation: 36h of slack, just over the line ---
    for day in (4.5, 11.5, 18.5, 25.5):
        rows.append(
            {
                "job_class": "weekly report generation",
                "model": "claude-haiku-4-5",
                "venue": "anthropic",
                "venue_tier": "standard",
                "input_tokens": 5_400_000,
                "output_tokens": 690_000,
                "submitted_at": _at(day),
                "required_by": _at(day + 1.5),
            }
        )

    # --- a nightly job on a clock-priced venue, no batch tier to move to ---
    for day in (3, 10, 17, 24):
        rows.append(
            {
                "job_class": "corpus summarisation",
                "model": "deepseek-chat",
                "venue": "deepseek",
                "venue_tier": "standard",
                "requests": 9_000,
                "avg_input_tokens": 2_400,
                "avg_output_tokens": 260,
                "submitted_at": _at(day),
                "required_by": _at(day + 2),
            }
        )

    # --- a marginal one: real deadline, but inside the batch window ---
    for day in (5, 12, 19, 26):
        rows.append(
            {
                "job_class": "pre-merge checks",
                "model": "claude-haiku-4-5",
                "venue": "anthropic",
                "venue_tier": "standard",
                "requests": 3_400,
                "avg_input_tokens": 2_100,
                "avg_output_tokens": 180,
                "submitted_at": _at(day),
                "required_by": _at(day + 0.25),  # 6 hours: inside the window
            }
        )

    # --- the control group: a human is waiting, and it must stay non-deferrable ---
    for day in range(0, 28, 2):
        rows.append(
            {
                "job_class": "interactive product surface",
                "model": "gpt-5.6-terra",
                "venue": "openai",
                "venue_tier": "standard",
                "requests": 41_000,
                "avg_input_tokens": 1_900,
                "avg_output_tokens": 340,
                "submitted_at": _at(day),
                "required_by": "interactive",
            }
        )

    return {
        "_synthetic": {
            "warning": "GENERATED DATA. Not a customer, not a real fleet, not "
            "real spend. Written by tools/make_synthetic_log.py to give "
            "tools/flexibility_report.py a worked example.",
            "generator": "tools/make_synthetic_log.py",
            "anchor_utc": ANCHOR.isoformat(timespec="seconds"),
            "shape": "mid-size AI-native shop, 28 days",
        },
        "jobs": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    log = build_log()
    path.write_text(json.dumps(log, indent=2) + "\n")
    print(f"wrote {path} — {len(log['jobs'])} synthetic rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
