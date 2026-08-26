#!/usr/bin/env python3
"""Render settled runs onto the board — the ledger of real money, not observation.

The Spread Board (`BOARD.md`) marks open grid data and spends nothing. This
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
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from offpeak import format_usd  # noqa: E402

REQUIRED = ("run_id", "scale", "settled_utc", "jobs", "list_usd", "paid_usd")

#: Wire format of the machine-readable ledger written beside SETTLED.md.
#: The Markdown is for people; this is for the website and anyone else who
#: would otherwise have to scrape a table or hand-copy rows into a page.
SETTLED_SCHEMA = "offpeak.settled-runs/1"

#: Namespace for :func:`receipt_uuid`. Fixed forever: the whole value of a
#: derived id is that anyone can recompute it, and a namespace that moved would
#: silently produce a different answer for the same run.
RECEIPT_NAMESPACE = uuid.UUID("6f1b1a3e-6a2f-5c4d-9f0e-0b7a2c9d4e51")

# Strings that must never reach a published receipt. Receipts are written by
# hand from a run artifact that *does* hold verbatim provider text — and
# provider errors routinely carry request URLs, org ids and account hints. Care
# is not a control, so the ledger refuses them mechanically. Same reason
# board-data is machine-written: a rule nobody can forget is worth more than one
# everybody means to follow.
_SECRET_SHAPED = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"\borg-[A-Za-z0-9]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    # Separators are matched as a run so an escaped JSON quote (\") between the
    # key and its value does not walk the pattern straight past a real leak.
    re.compile(
        r"(?:api[_-]?key|secret|token|password)[\"\'\\\s:=]{1,6}[A-Za-z0-9._-]{12,}",
        re.I,
    ),
)

# Fields that belong to the run artifact, never to the published ledger. A
# receipt is aggregate by construction: per-job rows carry model output, and on
# somebody else's key that output is their data, not evidence of ours.
_ARTIFACT_ONLY = ("per_job", "results", "messages", "prompts", "raw")

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
    "`receipts/`, never by hand. Those receipts come from "
    "`tools/mechanics_run.py`, which is in the tree for the same reason: a "
    "number you cannot reproduce is a number you are asked to trust.\n\n"
    "| run | scale | jobs | venues | tokens | list | paid | captured | SLA |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
)

# A run id opens with the date it settled, so a data row is unambiguous even
# when the header above it is a stale one from an older column set.
_RUN_ID = re.compile(r"^\d{4}-\d{2}-\d{2}[0-9A-Za-z._-]*$")
_DATA_ROW = re.compile(r"^\| \d{4}-\d{2}-\d{2}")


def receipt_uuid(record: dict) -> str:
    """A stable id for a run, **derived rather than minted**.

    A random uuid4 would identify a receipt and prove nothing about it: only
    whoever generated it could say it was right. This is a uuid5 over
    ``run_id|settled_utc``, so anyone holding the receipt can recompute it and
    check it — the same standard every money figure on this ledger is held to.

    It also means the id cannot drift from the run it names. Change either
    field and the id changes with it; store a different one and it is provably
    wrong rather than merely unfamiliar.
    """
    return str(uuid.uuid5(RECEIPT_NAMESPACE, f"{record['run_id']}|{record['settled_utc']}"))


def _refuse_unpublishable(path: Path, record: dict) -> None:
    """Refuse a receipt carrying anything that must not be published."""
    for field in _ARTIFACT_ONLY:
        if field in record:
            raise ValueError(
                f"{path.name}: receipt carries {field!r}, which belongs to the run "
                "artifact and not the published ledger — a receipt is aggregate "
                "by construction"
            )
    blob = json.dumps(record)
    for pattern in _SECRET_SHAPED:
        found = pattern.search(blob)
        if found:
            raise ValueError(
                f"{path.name}: receipt contains something secret-shaped "
                f"({found.group(0)[:12]}…) — receipts are public, and provider "
                "error text is not safe to copy verbatim"
            )


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
    _refuse_unpublishable(path, record)

    # A receipt may carry its own id, but it does not get to disagree with the
    # one its own fields derive. An id that can drift from the run it names is
    # worse than no id at all.
    derived = receipt_uuid(record)
    stated = record.get("receipt_uuid")
    if stated is not None and str(stated) != derived:
        raise ValueError(
            f"{path.name}: receipt_uuid {stated!r} does not match the id derived "
            f"from run_id and settled_utc ({derived})"
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

    Same contract as the Spread Board's: a re-render corrects a run rather than
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


def as_record(record: dict) -> dict:
    """One receipt, normalized for machines rather than for a column width."""
    captured = record.get("captured_usd")
    if captured is None:
        captured = record["list_usd"] - record["paid_usd"]
    pct = record.get("captured_pct")
    if pct is None:
        pct = 0.0 if not record["list_usd"] else 100.0 * captured / record["list_usd"]
    return {
        "run_id": record["run_id"],
        "receipt_uuid": receipt_uuid(record),
        "venue_handles": record.get("venue_handles") or {},
        "scale": record["scale"],
        "settled_utc": record["settled_utc"],
        "price_sheet": record.get("price_sheet"),
        "offpeak_version": record.get("offpeak_version"),
        "jobs": record["jobs"],
        "ok": record.get("ok"),
        "failed": record.get("failed"),
        "fell_back": record.get("fell_back") or 0,
        "sla_met": record.get("sla_met", 0),
        "input_tokens": record.get("input_tokens", 0),
        "output_tokens": record.get("output_tokens", 0),
        "list_usd": record["list_usd"],
        "paid_usd": record["paid_usd"],
        "captured_usd": captured,
        "captured_pct": pct,
        "by_venue": record.get("by_venue") or {},
        "notes": record.get("notes") or [],
    }


def summarize(runs: list[dict]) -> dict:
    """Totals the site would otherwise hand-maintain — and get wrong.

    ``venues_capturing`` counts venues that reached a batch tier **and kept
    it**. A run that reached the tier and then lost the results is not a
    capturing venue, and a summary that counted it would be the marketing
    version of this ledger rather than the accounting one.
    """
    capturing = sorted(
        {
            venue
            for run in runs
            if run["captured_usd"] > 0 and not run["fell_back"]
            for venue in run["by_venue"]
        }
    )
    return {
        "runs": len(runs),
        "jobs": sum(r["jobs"] for r in runs),
        "list_usd": sum(r["list_usd"] for r in runs),
        "paid_usd": sum(r["paid_usd"] for r in runs),
        "captured_usd": sum(r["captured_usd"] for r in runs),
        "runs_capturing": sum(1 for r in runs if r["captured_usd"] > 0),
        "runs_capturing_nothing": sum(1 for r in runs if r["captured_usd"] <= 0),
        "venues_capturing": capturing,
    }


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
    runs: list[dict] = []
    seen: dict[str, Path] = {}
    for path in receipts:
        record = load_receipt(path)
        run_id = record["run_id"]

        # Two receipts claiming one run_id used to be silently destructive:
        # SETTLED.md kept the last row and dropped the earlier settlement,
        # while SETTLED.json kept both and double-counted the money. The two
        # ledgers disagreed and nothing said so. A run id is an identity, and
        # a collision is a mistake worth stopping for.
        if run_id in seen:
            raise ValueError(
                f"{path.name}: run_id {run_id!r} is already claimed by "
                f"{seen[run_id].name}. Two settlements cannot share one id — "
                "the ledger would drop one of them and double-count the other."
            )
        seen[run_id] = path

        upsert_settled_row(board, run_id, render_row(record))
        runs.append(as_record(record))
        print(f"settled {run_id} ({record['scale']})")

    runs.sort(key=lambda r: r["run_id"])
    ledger = {
        "schema": SETTLED_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summarize(runs),
        "runs": runs,
    }
    (out / "SETTLED.json").write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out / 'SETTLED.json'} ({len(runs)} run(s))")

    print()
    print(board.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
