#!/usr/bin/env python3
"""Render settled runs onto the board — the ledger of real money, not observation.

The night board (`BOARD.md`) marks open grid data and spends nothing. This
writes a second, deliberately separate ledger (`SETTLED.md`) for runs that
actually executed and actually billed. Both live on `board-data`; neither is
ever edited by hand.

Every row carries its **scale**. A forty-eight job mechanics proof and a
six-thousand job production book are both real settlements and are not the same
evidence, and a ledger that lets the reader confuse them is doing marketing
rather than accounting.

    settle_report.py --receipts receipts --outdir board/nightly
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from offpeak import format_usd  # noqa: E402

REQUIRED = ("run_id", "scale", "settled_utc", "jobs", "list_usd", "paid_usd")

SETTLED_HEADER = (
    "# Offpeak settled runs — real money\n\n"
    "Every row here is a run that executed and billed: list price, price paid, "
    "and the spread captured, as arithmetic against the price sheet named in "
    "the row. This is the ledger `BOARD.md` is not — that one marks open grid "
    "data and spends nothing at any venue.\n\n"
    "**Scale is on every row on purpose.** A mechanics proof and a production "
    "book are both real settlements and are not the same evidence. Read the "
    "scale column before the money column.\n\n"
    "Written by `tools/settle_report.py` from the receipts in the main branch's "
    "`receipts/`, never by hand.\n\n"
    "| run | scale | jobs | venues | tokens | list | paid | captured | SLA |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
)

# A run id opens with the date it settled, so a data row is unambiguous even
# when the header above it is a stale one from an older column set.
_RUN_ID = re.compile(r"^\d{4}-\d{2}-\d{2}[0-9A-Za-z._-]*$")
_DATA_ROW = re.compile(r"^\| \d{4}-\d{2}-\d{2}")


def load_receipt(path: Path) -> dict:
    """Read one receipt, refusing anything that cannot be rendered honestly."""
    record = json.loads(path.read_text())
    missing = [key for key in REQUIRED if record.get(key) is None]
    if missing:
        raise ValueError(f"{path.name}: receipt is missing {', '.join(missing)}")
    if not str(record["scale"]).strip():
        raise ValueError(f"{path.name}: scale must say what size of run this was")
    if not _RUN_ID.match(str(record["run_id"])):
        raise ValueError(
            f"{path.name}: run_id must open with the settlement date "
            f"(YYYY-MM-DD...), got {record['run_id']!r}"
        )
    return record


def render_row(record: dict) -> str:
    """One settled run, as a board row."""
    venues = " · ".join(
        f"{name} {count}" for name, count in sorted((record.get("by_venue") or {}).items())
    )
    captured = record.get("captured_usd")
    if captured is None:
        captured = record["list_usd"] - record["paid_usd"]
    pct = record.get("captured_pct")
    if pct is None:
        pct = 0.0 if not record["list_usd"] else 100.0 * captured / record["list_usd"]
    sla = f"{record.get('sla_met', 0)}/{record['jobs']}"
    fell_back = record.get("fell_back") or 0
    if fell_back:
        sla += f" ({fell_back} fell back)"
    return (
        f"| {record['run_id']} "
        f"| {record['scale']} "
        f"| {record['jobs']} "
        f"| {venues or '—'} "
        f"| {record.get('input_tokens', 0):,} in · {record.get('output_tokens', 0):,} out "
        f"| ${format_usd(record['list_usd'])} "
        f"| ${format_usd(record['paid_usd'])} "
        f"| ${format_usd(captured)} ({pct:.1f}%) "
        f"| {sla} |\n"
    )


def upsert_settled_row(board: Path, run_id: str, row: str) -> None:
    """Write *row* for *run_id*, replacing any existing row for the same run.

    Same contract as the night board's: a re-render corrects a run rather than
    appending a second opinion, and the header is rewritten every time so a new
    column repairs the file instead of orphaning the rows beneath it.
    """
    existing = board.read_text().splitlines(keepends=True) if board.exists() else []
    rows = [ln for ln in existing if _DATA_ROW.match(ln)]

    marker = f"| {run_id} |"
    for i, line in enumerate(rows):
        if line.startswith(marker):
            rows[i] = row
            break
    else:
        rows.append(row)

    board.write_text(SETTLED_HEADER + "".join(rows))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--receipts", default="receipts", help="directory of receipt JSON")
    ap.add_argument("--outdir", default="nightly", help="where SETTLED.md lives")
    a = ap.parse_args()

    receipts = sorted(Path(a.receipts).glob("*.json"))
    if not receipts:
        print(f"no receipts in {a.receipts} — nothing settled yet")
        return 0

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    board = out / "SETTLED.md"
    for path in receipts:
        record = load_receipt(path)
        upsert_settled_row(board, record["run_id"], render_row(record))
        print(f"settled {record['run_id']} ({record['scale']})")

    print()
    print(board.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
