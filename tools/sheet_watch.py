#!/usr/bin/env python3
"""Sheet watch — does the price sheet this repo ships still match the ones it cites?

``offpeak``'s bundled sheet is a dated snapshot of numbers other people publish.
Providers move those numbers whenever they like and announce it nowhere this
repo can subscribe to, so ``PRICE_SHEET_DATE`` drifts silently from the truth.
This tool makes the drift loud.

Every source it watches is one of the pages :mod:`offpeak.prices` actually cites
in a comment, plus the four venues the sheet does not price yet but is asked
about most. It fetches each page, strips it to text, hashes it, and compares
against the text committed on ``board-data``. A hash that moved appends a dated
row to ``WATCH.md``, and the page text itself is committed next to it — so
``git diff`` on the ledger shows exactly which line moved, which is the record
this is really for.

**It never edits the price sheet.** Not a number, not a date. Drift detection
and drift resolution are different jobs, and only one of them is safe to
automate: a provider's page can move for a dozen reasons that are not a price
change, and a tool that rewrote ``prices.py`` on a hash diff would eventually
launder a marketing rewrite into a receipt. This tool says "something moved,
here"; a human reads it and settles what it means.

What it can and cannot see
--------------------------

It reads the HTML the server returns. Some providers render their rate tables in
the browser, so the static text carries no figures at all — the watch still sees
that page move, but it cannot see a *rate* move on it. That is not a bug to hide
behind a green row: every run counts the price-like figures it found per source
and writes the count to ``snapshots.json``, and the ``rates visible`` column in
``WATCH.md`` reports it. A source sitting at ``0`` is watching a shell, and the
table says so rather than implying coverage it does not have.

Dogfood
-------

Which is where the second half comes in. Classifying a diff as *price change*,
*copy change* or *noise* is exactly the kind of work this whole library exists
to price: a small, unglamorous LLM job that nobody is waiting on, with a real
deadline hours away. So the classifier runs **through** ``offpeak`` — same
``job()``/``run()``/``receipt()`` path any user gets, batch tier, cheapest venue
that supports the cheapest model on the sheet — and reports what it paid.

It is a convenience on top of the watch, never a precondition for it:

    Rows publish first. Classification is decoration.

If the classifier errors, the keys are absent, the cap is too tight or the
deadline has already passed, every hash-diff row still lands, marked
``unclassified`` with the reason. A missed classification must never mean a
missed watch — the watch is the product, and the classifier is the demo.

Usage
-----

    python tools/sheet_watch.py --outdir board/watch
    python tools/sheet_watch.py --outdir board/watch --no-classify
    python tools/sheet_watch.py --outdir board/watch --deadline 2026-08-26T06:00:00Z
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import offpeak  # noqa: E402
from offpeak import prices  # noqa: E402

__all__ = [
    "SOURCES",
    "Source",
    "Change",
    "html_to_text",
    "digest",
    "fetch",
    "detect_changes",
    "classify",
    "render_watch_md",
    "main",
]


@dataclass(frozen=True)
class Source:
    """A page whose text this repo wants to notice moving."""

    name: str
    url: str
    #: True when :mod:`offpeak.prices` cites this page for a row on the sheet.
    #: A cited page that moves may invalidate a shipped number; an uncited one
    #: is being watched ahead of pricing it.
    cited: bool


# The five cited sources are the ones named in comments in src/offpeak/prices.py.
# The four uncited ones are venues the sheet does not price yet: watching them
# before they are on the sheet is how the sheet gets added to honestly, with a
# history of what the page said rather than one reading on one day.
SOURCES: tuple[Source, ...] = (
    Source("anthropic:pricing", "https://platform.claude.com/docs/en/about-claude/pricing", True),
    Source("openai:pricing", "https://developers.openai.com/api/docs/pricing", True),
    Source("groq:pricing", "https://groq.com/pricing", True),
    Source("mistral:pricing", "https://mistral.ai/pricing/api", True),
    Source("google:pricing", "https://ai.google.dev/pricing", True),
    Source("groq:plans", "https://console.groq.com/docs/service-tiers", False),
    Source("xai:pricing", "https://docs.x.ai/docs/models", False),
    Source("deepseek:pricing", "https://api-docs.deepseek.com/quick_start/pricing", False),
    Source("qwen:pricing", "https://www.alibabacloud.com/help/en/model-studio/models", False),
)

# Candidate classifier models, cheapest-first is *computed* not assumed — see
# pick_model(). Each entry is (model, env var whose absence rules it out). Only
# models on the bundled sheet can appear here: a classifier whose cost cannot be
# quoted has no business running under a cap.
CLASSIFIER_MODELS: tuple[tuple[str, str], ...] = (
    ("gpt-5.6-luna", "OPENAI_API_KEY"),
    ("claude-haiku-4-5", "ANTHROPIC_API_KEY"),
)

#: The board marks at 06:30Z. A classification that lands after the mark missed
#: the thing it was for, so the deadline must sit before it.
BOARD_MARK_UTC = (6, 30)
#: Default classifier deadline: half an hour before the mark.
DEFAULT_DEADLINE_UTC = (6, 0)

#: Ceiling for a classifier answer. Deliberately not dozens of tokens: on a
#: reasoning model the ceiling covers reasoning *and* visible output, and a
#: ceiling too low bills a full one and returns an empty string. See the
#: quickstart's warning — this tool is a user of the library, and gets to make
#: the same mistake if it is careless.
CLASSIFIER_MAX_TOKENS = 256

#: How much of a diff the classifier is shown. A pricing page that is rebuilt
#: wholesale produces a diff thousands of lines long; sending all of it would
#: blow the cap to classify a change already obvious from the line count.
DIFF_LINES_SENT = 120

LABELS = ("price change", "copy change", "noise")

#: Price-like figures ("$0.75", "$ 4"). Counted per source so a page that
#: renders its rates in the browser — and therefore hands us no numbers — is
#: visibly distinguishable from one we are genuinely reading.
_PRICE_FIGURE = re.compile(r"\$\s?\d")

_USER_AGENT = "offpeak-sheet-watch/1.0 (+https://github.com/offpeak-ai/offpeak)"
_WS = re.compile(r"[ \t\u00a0]+")
_ROW_MARKER = "<!-- rows appended below by tools/sheet_watch.py -->"


# --------------------------------------------------------------------------- #
# Fetch and normalize
# --------------------------------------------------------------------------- #


class _TextExtractor(HTMLParser):
    """Strip markup to visible text. Deliberately crude, deliberately stable.

    A real renderer would produce nicer text and a different answer on every
    version bump. What this needs is a function whose output only changes when
    the *page* changes, so the hash means what it says.
    """

    _SKIP = frozenset({"script", "style", "noscript", "svg", "template", "head"})
    _BREAK = frozenset(
        {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BREAK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(html: str) -> str:
    """Normalized visible text: one logical line per line, no blank runs.

    Normalization is part of the contract, not a tidy-up. Whitespace churn is
    the most common way a page "changes" without changing, and a watch that
    cried wolf on every rebuild would be turned off within a week.
    """
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
        raw = parser.text
    except Exception:  # noqa: BLE001 — malformed markup is the page's problem
        raw = re.sub(r"<[^>]*>", " ", html)
    lines = (_WS.sub(" ", line).strip() for line in raw.splitlines())
    return "\n".join(line for line in lines if line)


def digest(text: str) -> str:
    """SHA-256 of the normalized text — the thing actually compared."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch(url: str, *, timeout: float = 30.0) -> str:
    """GET *url* and return its decoded body. Raises on any failure."""
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


# --------------------------------------------------------------------------- #
# Diffing
# --------------------------------------------------------------------------- #


@dataclass
class Change:
    """One source's outcome for one run."""

    source: Source
    #: "changed" | "unreachable" | "baseline" | "unchanged"
    status: str
    added: int = 0
    removed: int = 0
    diff: str = ""
    detail: str = ""
    label: str = ""
    reason: str = ""
    model: str = ""
    paid_usd: float | None = None

    @property
    def reportable(self) -> bool:
        """Whether this outcome earns a row. Silence is the normal case."""
        return self.status in ("changed", "unreachable", "baseline")


def _page_path(outdir: Path, name: str) -> Path:
    return outdir / "pages" / f"{name.replace(':', '__')}.txt"


def _load_snapshots(outdir: Path) -> dict[str, dict]:
    path = outdir / "snapshots.json"
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt snapshot must not stop the watch. Treat it as a first run:
        # one noisy day of baselines beats a silent tool.
        return {}
    return loaded if isinstance(loaded, dict) else {}


def detect_changes(
    sources: tuple[Source, ...],
    outdir: Path,
    *,
    fetcher: Callable[[str], str] | None = None,
) -> tuple[list[Change], dict[str, dict], dict[str, str]]:
    """Fetch every source and compare it against the committed snapshot.

    Returns the per-source outcomes, the snapshot index to write, and the page
    texts to write. A source that could not be fetched keeps its previous
    snapshot untouched: overwriting it with nothing would report a change
    tomorrow that was really a timeout today.
    """
    # Resolved here rather than as a default argument: a default would bind the
    # module-level fetch() once at import, which no test could then replace.
    fetch_fn = fetcher or fetch
    previous = _load_snapshots(outdir)
    snapshots: dict[str, dict] = dict(previous)
    pages: dict[str, str] = {}
    changes: list[Change] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for source in sources:
        try:
            text = html_to_text(fetch_fn(source.url))
        except (URLError, OSError, ValueError, TimeoutError) as exc:
            changes.append(
                Change(source, "unreachable", detail=f"{type(exc).__name__}: {exc}"[:200])
            )
            continue

        if not text.strip():
            changes.append(Change(source, "unreachable", detail="fetched, but stripped to nothing"))
            continue

        new_hash = digest(text)
        old = previous.get(source.name) or {}
        old_hash = old.get("sha256")

        snapshots[source.name] = {
            "url": source.url,
            "sha256": new_hash,
            "chars": len(text),
            "lines": text.count("\n") + 1,
            "price_figures": len(_PRICE_FIGURE.findall(text)),
            "cited": source.cited,
            "fetched_at": now,
        }
        pages[source.name] = text

        if old_hash is None:
            changes.append(Change(source, "baseline", detail=f"{len(text):,} chars recorded"))
            continue
        if old_hash == new_hash:
            changes.append(Change(source, "unchanged"))
            continue

        old_text = ""
        old_path = _page_path(outdir, source.name)
        if old_path.exists():
            old_text = old_path.read_text(encoding="utf-8")
        old_lines, new_lines = old_text.splitlines(), text.splitlines()
        diff_lines = list(
            difflib.unified_diff(old_lines, new_lines, "before", "after", n=1, lineterm="")
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(
            1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
        )
        changes.append(
            Change(
                source,
                "changed",
                added=added,
                removed=removed,
                diff="\n".join(diff_lines[:DIFF_LINES_SENT]),
                detail=f"{old_hash[:12]} -> {new_hash[:12]}",
            )
        )

    return changes, snapshots, pages


# --------------------------------------------------------------------------- #
# The dogfood classifier — everything below here is allowed to fail
# --------------------------------------------------------------------------- #


def next_utc(hour: int, minute: int, *, now: datetime | None = None) -> datetime:
    """The next occurrence of *hour*:*minute* UTC, strictly in the future."""
    now = now or datetime.now(timezone.utc)
    candidate = now.astimezone(timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def pick_model(
    candidates: tuple[tuple[str, str], ...] = CLASSIFIER_MODELS,
    *,
    env: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """Cheapest candidate whose venue key is present and whose price is known.

    "Cheapest" is arithmetic against the bundled sheet at this job's actual
    shape, not a hard-coded favourite — the day a cheaper model lands on the
    sheet, adding one line here is the whole change.
    """
    environ = os.environ if env is None else env
    priced: list[tuple[float, str]] = []
    missing: list[str] = []
    for model, key in candidates:
        if not environ.get(key):
            missing.append(key)
            continue
        cost = prices.batch_cost_usd(model, 2_000, CLASSIFIER_MAX_TOKENS)
        if cost is None:
            continue
        priced.append((cost, model))
    if not priced:
        return None, f"no classifier key in the environment (looked for: {', '.join(missing)})"
    priced.sort()
    return priced[0][1], ""


def _prompt(change: Change) -> str:
    return (
        "You are watching a provider's public pricing or docs page for drift.\n"
        "Below is a unified diff of that page's visible text between two daily "
        "readings.\n\n"
        "Classify the change as exactly one of:\n"
        "  price change  — a number that money is charged against moved, was "
        "added, or was removed\n"
        "  copy change   — wording, layout, navigation or examples moved; no "
        "rate changed\n"
        "  noise         — timestamps, session ids, banners, ordering or other "
        "churn with no content\n\n"
        "Reply with exactly two lines:\n"
        "LABEL: <one of: price change | copy change | noise>\n"
        "WHY: <one sentence, under 20 words>\n\n"
        f"Page: {change.source.name} ({change.source.url})\n"
        f"Diff (+{change.added} / -{change.removed} lines, truncated):\n"
        f"{change.diff}\n"
    )


def _parse_label(text: str) -> tuple[str, str]:
    """Pull LABEL/WHY out of a reply, without trusting it to be well-formed."""
    label, why = "", ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("label:"):
            candidate = stripped.split(":", 1)[1].strip().lower()
            for known in LABELS:
                if known in candidate:
                    label = known
                    break
        elif lowered.startswith("why:"):
            why = stripped.split(":", 1)[1].strip()
    if not label:
        lowered = (text or "").lower()
        for known in LABELS:
            if known in lowered:
                label = known
                break
    return label, why


def classify(
    changes: list[Change],
    *,
    deadline: datetime,
    cap_usd: float,
    model: str | None = None,
    env: dict[str, str] | None = None,
    runner: Callable[..., list] | None = None,
) -> str:
    """Classify every changed source in one batch. Never raises.

    Mutates the ``label`` / ``reason`` / ``model`` / ``paid_usd`` fields of the
    changes it was given and returns a note for the run summary. Every exit
    path leaves the changes renderable, because the caller is going to publish
    them either way.
    """
    targets = [c for c in changes if c.status == "changed"]
    if not targets:
        return "no changed sources to classify"

    def bail(reason: str) -> str:
        for change in targets:
            change.reason = reason
        return reason

    mark = next_utc(*BOARD_MARK_UTC)
    if deadline >= mark:
        return bail(
            f"deadline {deadline.isoformat()} is not before the {mark.isoformat()} board mark"
        )

    if model is None:
        model, why = pick_model(env=env)
        if model is None:
            return bail(why)

    try:
        jobs = [
            offpeak.job(
                model,
                _prompt(change),
                max_tokens=CLASSIFIER_MAX_TOKENS,
                metadata={"source": change.source.name},
            )
            for change in targets
        ]

        estimate = offpeak.quote(jobs, deadline=deadline)
        if estimate.batch_usd > cap_usd:
            return bail(
                f"quoted ${prices.format_usd(estimate.batch_usd)} over the "
                f"${prices.format_usd(cap_usd)} cap"
            )

        results = list((runner or offpeak.run)(jobs, deadline))

        for change, result in zip(targets, results, strict=False):
            change.model = model
            receipt = getattr(result, "receipt", None)
            change.paid_usd = getattr(receipt, "paid_usd", None)
            if not getattr(result, "ok", False):
                change.reason = (getattr(result, "error", None) or "no text returned")[:200]
                continue
            label, why = _parse_label(result.text or "")
            if label:
                change.label = label
                change.reason = why
            else:
                change.reason = "classifier reply did not name a label"

        settlement = offpeak.receipt(results)
    except Exception as exc:  # noqa: BLE001 — the classifier is decoration
        return bail(f"{type(exc).__name__}: {exc}"[:200])

    return (
        f"{len(targets)} diff(s) classified on {model} — "
        f"paid ${prices.format_usd(settlement.paid_usd)}, "
        f"list ${prices.format_usd(settlement.list_usd)}, "
        f"captured ${prices.format_usd(settlement.captured_usd)}"
    )


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def _row(date: str, change: Change) -> str:
    if change.status == "changed":
        moved = f"+{change.added} / −{change.removed}"
        # "unclassified" is a statement about a diff nobody labelled. A baseline
        # or an unreachable page has no diff, so it gets a dash instead — the
        # column must not imply a classifier failed when none was due.
        label = change.label or "unclassified"
    else:
        moved = "—"
        label = "—"
    note = change.reason or change.detail or ""
    cost = "—" if change.paid_usd is None else f"${prices.format_usd(change.paid_usd)}"
    note = note.replace("|", "\\|").replace("\n", " ")[:160]
    return (
        f"| {date} | `{change.source.name}` | {change.status} | {moved} | "
        f"{label} | {change.model or '—'} | {cost} | {note} |"
    )


def _rates_visible(snapshots: dict[str, dict], name: str) -> str:
    """How many price-like figures the last reading of *name* actually held."""
    figures = (snapshots.get(name) or {}).get("price_figures")
    if figures is None:
        return "—"
    return str(figures) if figures else "**0 — rendered client-side**"


def render_watch_md(
    existing: str,
    rows: list[str],
    sources: tuple[Source, ...],
    snapshots: dict[str, dict] | None = None,
) -> str:
    """Rebuild the header, keep every row ever written, append the new ones."""
    kept = ""
    if _ROW_MARKER in existing:
        kept = existing.split(_ROW_MARKER, 1)[1].strip("\n")

    snaps = snapshots or {}
    source_rows = "\n".join(
        f"| `{s.name}` | {'cited by prices.py' if s.cited else 'watched, not yet priced'} "
        f"| {_rates_visible(snaps, s.name)} | <{s.url}> |"
        for s in sources
    )
    header = f"""# WATCH — provider sheet drift

`offpeak` ships a **dated snapshot** of numbers other people publish. This table
is the record of those pages moving underneath it.

Every row is a hash diff of one page's visible text against the reading
committed here the day before. The page text itself is committed alongside, in
`pages/`, so `git diff` on this branch shows the line that moved — this table
says *that* something moved; the diff says *what*.

**No number here has edited the price sheet.** Detection and resolution are
different jobs. A page can move for a dozen reasons that are not a price change,
so `tools/sheet_watch.py` never writes to `src/offpeak/prices.py`: a human reads
a row and settles what it meant.

The `classification` column is produced by an LLM job submitted **through
`offpeak` itself** — batch tier, cheapest model on the sheet whose key is
present, deadline before the 06:30Z mark — and is *advisory*. It is allowed to
be absent: rows publish whether or not it ran, and `unclassified` in that column
means the classifier did not answer, never that the page did not move.

## Sources

`rates visible` is how many price-like figures (`$0.75`) the last reading found
in the page's static text. A source at **0** renders its rates in the browser:
this watch still sees that page change, but it cannot see a rate change on it,
and no row about it should be read as rate coverage.

| source | why | rates visible | url |
| --- | --- | --- | --- |
{source_rows}

## Drift

| date (UTC) | source | status | lines | classification | classifier | cost | note |
| --- | --- | --- | --- | --- | --- | --- | --- |
{_ROW_MARKER}"""

    body = "\n".join(filter(None, [kept, "\n".join(rows)]))
    return f"{header}\n{body}\n" if body else f"{header}\n"


def _write(
    outdir: Path, snapshots: dict[str, dict], pages: dict[str, str], rows: list[str]
) -> None:
    """Ledger first, snapshot second — deliberately.

    If this process dies between the two writes, the next run re-detects the
    same change and writes a duplicate row. The other order would mark the
    change as seen and never report it. Duplicates are noise; a silent miss is
    a broken watch.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    watch = outdir / "WATCH.md"
    existing = watch.read_text(encoding="utf-8") if watch.exists() else ""
    watch.write_text(render_watch_md(existing, rows, SOURCES, snapshots), encoding="utf-8")

    (outdir / "pages").mkdir(parents=True, exist_ok=True)
    for name, text in pages.items():
        _page_path(outdir, name).write_text(text, encoding="utf-8")
    (outdir / "snapshots.json").write_text(
        json.dumps(snapshots, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _deadline_arg(value: str) -> datetime:
    parsed = offpeak.parse_deadline(value)
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--outdir", type=Path, required=True, help="ledger dir on board-data")
    parser.add_argument(
        "--deadline",
        type=_deadline_arg,
        default=None,
        help="classifier deadline (default: the next 06:00Z, before the 06:30Z mark)",
    )
    parser.add_argument("--cap", type=float, default=0.01, help="hard USD cap for classification")
    parser.add_argument("--model", default=None, help="force a classifier model")
    parser.add_argument("--no-classify", action="store_true", help="hash diff only")
    parser.add_argument(
        "--only", default="", help="comma-separated source names to watch (default: all)"
    )
    args = parser.parse_args(argv)

    sources = SOURCES
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        sources = tuple(s for s in SOURCES if s.name in wanted)
        if not sources:
            parser.error(f"no source matches {args.only!r}")

    changes, snapshots, pages = detect_changes(sources, args.outdir)

    note = "classification disabled (--no-classify)"
    if args.no_classify:
        # Without this the row prints `unclassified` next to the hash detail and
        # says nothing about why — which is exactly how four 2026-08-27 rows came
        # to look like a classifier failure when the classifier was never asked
        # to run. `unclassified` must always carry its reason.
        for change in changes:
            if change.status == "changed":
                change.reason = note
    else:
        deadline = args.deadline or next_utc(*DEFAULT_DEADLINE_UTC)
        note = classify(changes, deadline=deadline, cap_usd=args.cap, model=args.model)

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [_row(date, c) for c in changes if c.reportable]
    _write(args.outdir, snapshots, pages, rows)

    changed = [c for c in changes if c.status == "changed"]
    unreachable = [c for c in changes if c.status == "unreachable"]
    print(f"sheet_watch {date}: {len(sources)} source(s) watched")
    print(f"  changed     {len(changed)}")
    print(f"  unreachable {len(unreachable)}")
    print(f"  rows written {len(rows)} -> {args.outdir / 'WATCH.md'}")
    print(f"  classifier  {note}")
    for change in changed:
        print(f"    {change.source.name}: +{change.added} / -{change.removed} {change.detail}")
    for change in unreachable:
        print(f"    {change.source.name}: unreachable — {change.detail}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
