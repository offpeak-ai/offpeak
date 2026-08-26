#!/usr/bin/env python3
"""Publish the bundled price sheet as dated JSON anyone can fetch.

`offpeak` ships a **snapshot** of numbers other people publish, and a release is
the only thing that moves it. That is the right default — a receipt settled today
has to stay checkable next year against the numbers that settled it, and a
library that silently repriced itself overnight could not offer that. But it
leaves users on whatever sheet their install happened to freeze.

This is the other half: the same sheet, written out as data, so a user can pick
up a newer one *deliberately* without waiting for a release.

    python -m offpeak ...                       # bundled sheet, offline, default
    offpeak.prices.load_sheet(SHEET_URL)        # opt in to a published one

There is no database and no service behind that URL. It is a dated file on the
`board-data` branch, served by whatever CDN fronts a git host, and it is
immutable once written: `sheet/2026-08-23.json` will say the same thing forever.
`sheet/latest.json` is a copy of the newest one, for callers who want current
over reproducible — and it names its own date, so a caller can pin the dated file
the moment it wants reproducibility back.

    publish_sheet.py --outdir board/sheet
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import offpeak  # noqa: E402
from offpeak import prices  # noqa: E402

INDEX_SCHEMA = "offpeak.price-sheet-index/1"


def build_index(outdir: Path, current: str) -> dict:
    """Every dated sheet on disk, newest first.

    Read off the directory rather than accumulated in a file: the sheets *are*
    the index, and one that disagreed with them would be a third thing to keep
    in sync.
    """
    dates = sorted(
        (p.stem for p in outdir.glob("*.json") if p.stem not in ("latest", "index")),
        reverse=True,
    )
    return {
        "schema": INDEX_SCHEMA,
        "latest": current,
        "offpeak_version": offpeak.__version__,
        "sheets": [{"sheet_date": d, "path": f"{d}.json"} for d in dates],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outdir", type=Path, required=True, help="where the sheets live")
    ap.add_argument(
        "--force",
        action="store_true",
        help="rewrite a dated sheet that already exists (it should not change)",
    )
    a = ap.parse_args(argv)

    document = prices.export_sheet()
    date = document["sheet_date"]
    a.outdir.mkdir(parents=True, exist_ok=True)
    dated = a.outdir / f"{date}.json"

    # A dated sheet is immutable. If one already exists and says something
    # different, that is a sheet edited without moving its date — which would
    # quietly change what an old receipt settled against. Say so and stop.
    if dated.exists() and not a.force:
        existing = json.loads(dated.read_text(encoding="utf-8"))
        if _comparable(existing) != _comparable(document):
            print(
                f"REFUSING: {dated.name} already exists and differs from the "
                f"sheet in this build. A dated sheet is immutable — bump "
                f"PRICE_SHEET_DATE, or pass --force if you know why.",
                file=sys.stderr,
            )
            return 1
        print(f"{dated.name} already published and identical — nothing to do")
    else:
        dated.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {dated}")

    (a.outdir / "latest.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (a.outdir / "index.json").write_text(
        json.dumps(build_index(a.outdir, date), indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {a.outdir / 'latest.json'} and {a.outdir / 'index.json'}")
    print(f"  sheet {date}: {len(document['prices'])} model(s), "
          f"{len(document['fast_prices'])} fast row(s), "
          f"{len(document['promo_notes'])} promo note(s)")
    return 0


def _comparable(document: dict) -> dict:
    """The sheet minus the stamp that moves on every run."""
    return {k: v for k, v in document.items() if k != "generated_utc"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
