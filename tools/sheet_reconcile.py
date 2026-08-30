#!/usr/bin/env python3
"""Sheet reconcile — does the bundled sheet still agree with the pages it cites?

``tools/sheet_watch.py`` answers a *relative* question: did this page move since
yesterday? It hashes each provider's visible text and reports the diff. That
catches every change from the day the watch took its first reading — and nothing
at all before it.

Which is a real hole, and this tool is the patch. On 2026-08-26 the watch
recorded its baselines. Two rows on the Anthropic page were already wrong on the
sheet that day: Fast mode had shipped on Claude Opus 5 and Opus 4.8, and Sonnet
5's introductory rate had been made permanent. No hash diff can ever surface
either, because the hash was taken *after* both. A watch that only compares a
page to itself is blind to everything it inherited.

So this compares the page to the **sheet**: it parses per-model rates out of the
page text ``sheet_watch`` already committed under ``watch/pages/``, lines them up
against :mod:`offpeak.prices`, and prints what disagrees.

    python tools/sheet_reconcile.py --pages board/watch/pages
    python tools/sheet_reconcile.py --pages board/watch/pages --outdir board/watch

Same rule as the watch, for the same reason
-------------------------------------------

**It never edits the price sheet.** It reads ``offpeak.prices`` and writes a
report; resolving a row is a human's job. A parser confident enough to rewrite
``prices.py`` from scraped text would eventually launder a table-layout change
into a receipt, and receipts are the one thing here that must not be guessed at.

What it can and cannot read
---------------------------

Every parser below is written against one provider's actual page shape, because
there is no shared shape to write against: Anthropic publishes aligned tables,
OpenAI publishes tiered tables with a context dimension, Google publishes a
per-model block with a tier section inside it, and Mistral publishes a card grid
with no tier rows at all. Four pages, four parsers, each one narrow and each one
willing to return nothing.

Returning nothing is a supported outcome, not a failure. A field this tool
cannot read is reported ``unverifiable`` and is never scored as agreement —
silence about a rate must not read as confirmation of it.

* **groq** is skipped outright. ``groq.com/pricing`` renders its rate table in
  the browser; the static text this repo commits holds one dollar figure and it
  is a funding round. ``WATCH.md`` already marks it ``0 — rendered client-side``.
  There is nothing here to reconcile against, and saying so is more honest than
  a parser that finds no rows and calls the sheet confirmed.
* **mistral** publishes standard rates per model, but prices batch ("half
  price") and priority as account-level toggles rather than per-model rows. Its
  batch and fast fields are therefore ``unverifiable``, not ``0``.

Outcomes
--------

``mismatch``
    Both sides carry a number and they differ, *or* the page publishes a tier
    for a model the sheet carries no row for. The second half is what caught
    Opus 5's fast mode: the sheet was not wrong about a number, it was missing
    one, and a reconciler that only compared numbers it already had would have
    agreed with itself forever.
``missing``
    A model on the sheet with no row on the page. Reported, but not fatal: an
    id can be an alias the provider does not print, and a model dropping off a
    published table is a question for a human, not an assertion.
``unverifiable``
    The page carries no comparable figure. Reported, never scored.
``unpriced``
    A model on the page that the sheet does not carry. Informational — the sheet
    omits models on purpose (see the comments in ``prices.py``), so this is a
    list to read, not a list to fix.

Exit code is non-zero when there is at least one ``mismatch``, so a human or a
CI step can branch on it. Nothing else fails the run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from offpeak import prices  # noqa: E402

__all__ = [
    "FIELDS",
    "PARSERS",
    "SKIPPED",
    "Finding",
    "Report",
    "parse_anthropic",
    "parse_openai",
    "parse_google",
    "parse_mistral",
    "sheet_rates",
    "reconcile_source",
    "reconcile",
    "render_reconcile_md",
    "latest_classifications",
    "main",
]

#: The rate fields compared, in report order. ``batch_*`` is the venue's
#: published batch row where it prints one; the sheet's side is always
#: ``list x BATCH_DISCOUNT``, since that rule is what the library applies.
FIELDS: tuple[str, ...] = (
    "input",
    "output",
    "batch_input",
    "batch_output",
    "fast_input",
    "fast_output",
)

#: Sources deliberately not parsed, and why. Kept as data so the reason lands in
#: the report next to the source rather than living only in this docstring.
SKIPPED: dict[str, str] = {
    "groq": (
        "groq.com/pricing renders its rate table client-side — the committed "
        "page text holds no per-model rates to reconcile against"
    ),
}

#: Fields no provider prints per model, with the reason. A field listed here is
#: reported ``unverifiable`` rather than compared.
NOT_PUBLISHED: dict[str, dict[str, str]] = {
    "mistral": {
        "batch_input": "batch is an account-level toggle (-50%), not a per-model row",
        "batch_output": "batch is an account-level toggle (-50%), not a per-model row",
        "fast_input": "priority is an account-level toggle with no published rate",
        "fast_output": "priority is an account-level toggle with no published rate",
    },
}

#: Which sheet models belong to which source. Ordered: the first rule that
#: matches wins, so Groq's ``openai/gpt-oss-*`` is claimed before OpenAI's
#: ``gpt-*`` can take it.
_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("groq", ("openai/",)),
    ("anthropic", ("claude-",)),
    ("openai", ("gpt-",)),
    ("google", ("gemini-",)),
    ("mistral", ("mistral-", "ministral-", "codestral", "glm-", "zai-glm-", "voxtral-")),
)

#: A price at the start of a line: ``$10``, ``$0.075``, ``$1,000``. Anchored,
#: because a line is a *cell* in these tables — a dollar figure in the middle of
#: a sentence is prose, and prose is not a rate.
_MONEY = re.compile(r"^\$\s?([\d,]+(?:\.\d+)?)\b")

#: Money with an explicit per-million-tokens unit, which is the only unit this
#: tool compares. ``$4 / 1000 pages`` and ``$0.003`` per audio minute both parse
#: as money and neither is a token rate; the unit check is what keeps a
#: page-priced OCR model out of a token column.
_PER_MTOK = re.compile(r"^\$\s?([\d,]+(?:\.\d+)?)\s*(?:/\s*MTok|/\s*1M)\b", re.I)

_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")


def _money(line: str) -> float | None:
    """The leading dollar figure on *line*, or ``None``.

    Trailing qualifiers are kept out of the number but do not disqualify it:
    Google writes ``$0.54 (text / image / video / audio)`` and ``$2.00, prompts
    <= 200k tokens``, and in both the first figure is the rate that applies to
    the shape this sheet prices.
    """
    match = _MONEY.match(line.strip())
    if match is None:
        return None
    return float(match.group(1).replace(",", ""))


def _mtok(line: str) -> float | None:
    """The leading dollar figure on *line* when it is priced per 1M tokens."""
    match = _PER_MTOK.match(line.strip())
    if match is None:
        return None
    return float(match.group(1).replace(",", ""))


def _column_role(header: str) -> str | None:
    """``"input"``, ``"output"`` or ``None`` for a table column heading.

    One rule for four pages. ``Cached input`` and ``Cache writes`` are
    deliberately *not* input: they are separate published rates for a different
    thing, and this sheet does not carry them.
    """
    name = header.strip().lower()
    if "cach" in name:
        return None
    if "input" in name:
        return "input"
    if "output" in name:
        return "output"
    return None


def _is_column(header: str) -> bool:
    """True for any table column heading, including the ones with no role.

    ``Cached input`` and ``Cache writes`` are columns this sheet does not carry,
    but they still sit inside the header run — a collector that stopped at the
    first roleless line would read only half of it.
    """
    return _column_role(header) is not None or "cach" in header.lower()


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def _blank(model: str) -> dict[str, float | None]:
    return {"model": model, **{f: None for f in FIELDS}}


# --------------------------------------------------------------------------- #
# Anthropic — platform.claude.com/docs/en/about-claude/pricing
# --------------------------------------------------------------------------- #

# A row label in one of the tables: "Claude Sonnet 5", "Claude Opus 4.1
# (retired, except on Bedrock and Google Cloud)", "Claude Opus 5 / Claude Opus
# 4.8". A version digit is required and punctuation is not allowed, which is
# what separates a label from the many sentences on this page that open with
# the word Claude.
_CLAUDE_ROW = re.compile(r"^Claude [A-Z][\w. /]*\d[\w.]*(?: \(.*\))?$")


def _claude_ids(label: str) -> list[str]:
    """Sheet ids for a table row label.

    ``"Claude Opus 4.1 (retired, except on Bedrock and Google Cloud)"`` is one
    model; ``"Claude Opus 5 / Claude Opus 4.8"`` is two sharing a fast rate.
    """
    ids = []
    for part in _PARENTHETICAL.sub("", label).split(" / "):
        name = part.strip().lower()
        if not name.startswith("claude "):
            continue
        ids.append(name.replace(".", "-").replace(" ", "-"))
    return ids


def parse_anthropic(text: str) -> dict[str, dict]:
    """Three aligned tables: model pricing, fast mode pricing, batch processing.

    Each is a header run (``Model`` then one line per column) followed by rows of
    one label line and one price line per column. The tier a table sets is taken
    from the section heading above it, because ``Model / Input / Output`` is not
    self-describing — it is the fast table only because "Fast mode pricing" is
    the heading it sits under.
    """
    tiers = {
        "Model pricing": "",
        "Fast mode pricing": "fast_",
        "Batch processing": "batch_",
    }
    rows: dict[str, dict] = {}
    lines = _lines(text)
    prefix, roles = "", []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line in tiers:
            prefix, roles = tiers[line], []
            index += 1
            continue
        if line == "Model":
            # The header run ends at the first row label. Collect column names
            # until then rather than counting them: the three tables have two,
            # three and five value columns.
            roles, cursor = [], index + 1
            while cursor < len(lines) and len(roles) <= 8:
                header = lines[cursor]
                if not header or _CLAUDE_ROW.match(header) or _mtok(header) is not None:
                    break
                roles.append(_column_role(header))
                cursor += 1
            index = cursor if roles else index + 1
            continue
        if roles and _CLAUDE_ROW.match(line):
            values = []
            cursor = index + 1
            while cursor < len(lines) and len(values) < len(roles):
                rate = _mtok(lines[cursor])
                if rate is None:
                    break
                values.append(rate)
                cursor += 1
            if len(values) == len(roles):
                for model in _claude_ids(line):
                    row = rows.setdefault(model, _blank(model))
                    for role, value in zip(roles, values, strict=True):
                        if role:
                            row[f"{prefix}{role}"] = value
                index = cursor
                continue
        index += 1
    return rows


# --------------------------------------------------------------------------- #
# OpenAI — developers.openai.com/api/docs/pricing
# --------------------------------------------------------------------------- #

_OPENAI_TIERS = {"Standard": "", "Batch": "batch_", "Flex": "flex_", "Fast mode": "fast_"}
_OPENAI_ROW = re.compile(r"^[a-z][a-z0-9.]*(?:-[a-z0-9.]+)+$")


def parse_openai(text: str) -> dict[str, dict]:
    """Tiered tables, each doubled across a context dimension.

    The header runs ``Model, Input, Cached input, Cache writes, Output`` once for
    short context and again for long. The sheet has no long-context dimension
    (see the OpenAI block in ``prices.py``), so the *first* input and the *first*
    output column are the ones compared — same convention the sheet stores.

    ``Flex`` is parsed and then dropped: the sheet does not carry a flex row, and
    a column nobody compares is a column that should not imply coverage.
    """
    rows: dict[str, dict] = {}
    lines = _lines(text)
    prefix, roles = None, []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line in _OPENAI_TIERS:
            prefix, roles = _OPENAI_TIERS[line], []
            index += 1
            continue
        if line == "Model" and prefix is not None:
            roles, cursor = [], index + 1
            while cursor < len(lines) and _is_column(lines[cursor]):
                roles.append(_column_role(lines[cursor]))
                cursor += 1
            index = cursor if roles else index + 1
            continue
        if roles and prefix is not None and _OPENAI_ROW.match(line):
            values, cursor = [], index + 1
            while cursor < len(lines) and len(values) < len(roles):
                rate = _money(lines[cursor])
                if rate is None:
                    break
                values.append(rate)
                cursor += 1
            if len(values) == len(roles):
                row = rows.setdefault(line, _blank(line))
                for role, value in zip(roles, values, strict=True):
                    if role and row.get(f"{prefix}{role}") is None:
                        # First wins: short context, then long. The sheet stores
                        # the short-context rate.
                        row[f"{prefix}{role}"] = value
                index = cursor
                continue
        index += 1
    return {
        m: {k: v for k, v in row.items() if not k.startswith("flex_")}
        for m, row in rows.items()
    }


# --------------------------------------------------------------------------- #
# Google — ai.google.dev/pricing
# --------------------------------------------------------------------------- #

_GEMINI_IDS = re.compile(r"^gemini[\w.\-]*(?: and gemini[\w.\-]*)*$")
_GOOGLE_TIERS = {"Standard": "", "Batch": "batch_", "Flex": "flex_", "Priority": "fast_"}
_GOOGLE_LABELS = {"Input price": "input", "Output price (including thinking tokens)": "output"}


def parse_google(text: str) -> dict[str, dict]:
    """One block per model, with a tier section inside it.

    ``Priority`` is mapped to ``fast_``: it is the same tier OpenAI sells, which
    that provider's own page settles — "Priority processing was renamed Fast mode
    on July 30, 2026."

    Two shapes of value follow each label, and the *first* dollar line is the
    right one in both. A promotional rate prints as ``$0.75 through December 31,
    2026.`` then ``$1.50 starting January 1, 2027.`` — today's rate first. A
    context-tiered rate prints as ``$2.00, prompts <= 200k tokens`` then the
    long-context figure — the short-context rate first, which is what the sheet
    stores. Free-tier cells (``Free of charge``, ``Not available``) carry no
    dollar sign and are skipped on the way past.
    """
    rows: dict[str, dict] = {}
    lines = _lines(text)
    models: list[str] = []
    prefix = ""
    for index, line in enumerate(lines):
        if _GEMINI_IDS.match(line):
            models = [part.strip() for part in line.split(" and ")]
            prefix = ""
            for model in models:
                rows.setdefault(model, _blank(model))
            continue
        if line in _GOOGLE_TIERS:
            prefix = _GOOGLE_TIERS[line]
            continue
        role = _GOOGLE_LABELS.get(line)
        if role is None or not models:
            continue
        cursor = index + 1
        while cursor < len(lines) and cursor < index + 6:
            value = _money(lines[cursor])
            if value is not None:
                for model in models:
                    if rows[model].get(f"{prefix}{role}") is None:
                        rows[model][f"{prefix}{role}"] = value
                break
            if lines[cursor] in _GOOGLE_LABELS or lines[cursor] in _GOOGLE_TIERS:
                break
            cursor += 1
    return {
        m: {k: v for k, v in row.items() if not k.startswith("flex_")}
        for m, row in rows.items()
    }


# --------------------------------------------------------------------------- #
# Mistral — mistral.ai/pricing/api
# --------------------------------------------------------------------------- #

_MISTRAL_LABEL = re.compile(r"^(?P<name>[\w /]*?(?:input|output))\b.*\(.*\)$", re.I)
_MISTRAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:[-.][a-z0-9]+)+$")


def parse_mistral(text: str) -> dict[str, dict]:
    """A card grid, so the rates are found by walking *backwards* from the id.

    There is no table and no tier row: each card ends with its model id followed
    by ``Copy to clipboardCopied``, and the rates sit above it. Walking back from
    the id and stopping at the first line that is neither a rate, a rate label,
    nor a known interstitial is what keeps one card's numbers out of the next
    one's — the page has unlabelled blocks (the fine-tuned classifier APIs) with
    no id of their own, and a forward-accumulating parser hands their rates to
    whichever card comes next.
    """
    rows: dict[str, dict] = {}
    lines = _lines(text)
    interstitial = {"Available on", "Copy to clipboardCopied"}
    for index, line in enumerate(lines):
        if line != "Copy to clipboardCopied" or index == 0:
            continue
        model = lines[index - 1]
        if not _MISTRAL_ID.match(model):
            continue
        row = rows.setdefault(model, _blank(model))
        cursor, pending = index - 2, None
        while cursor >= 0:
            current = lines[cursor]
            value = _money(current)
            label = _MISTRAL_LABEL.match(current)
            if value is not None:
                pending = value
            elif label is not None:
                role = _column_role(label.group("name"))
                if role and pending is not None and row.get(role) is None:
                    row[role] = pending
                pending = None
            elif current in interstitial or current.startswith("/v1/"):
                pass
            else:
                break
            cursor -= 1
    return rows


PARSERS: dict[str, Callable[[str], dict[str, dict]]] = {
    "anthropic": parse_anthropic,
    "openai": parse_openai,
    "google": parse_google,
    "mistral": parse_mistral,
}


# --------------------------------------------------------------------------- #
# Lining the two up
# --------------------------------------------------------------------------- #


def owner(model: str) -> str | None:
    """Which watched source publishes *model*, or ``None``."""
    for name, prefixes in _OWNERS:
        if any(model.startswith(p) for p in prefixes):
            return name
    return None


def sheet_rates(model: str) -> dict[str, float | None]:
    """What :mod:`offpeak.prices` says *model* costs, in :data:`FIELDS` shape."""
    standard = prices.get_price(model)
    fast = prices.get_fast_price(model)
    return {
        "input": standard[0] if standard else None,
        "output": standard[1] if standard else None,
        "batch_input": standard[0] * prices.BATCH_DISCOUNT if standard else None,
        "batch_output": standard[1] * prices.BATCH_DISCOUNT if standard else None,
        "fast_input": fast[0] if fast else None,
        "fast_output": fast[1] if fast else None,
    }


def match_row(model: str, page: dict[str, dict]) -> dict | None:
    """The page row for a sheet *model*.

    Sheet ids are family prefixes on purpose (``mistral-medium`` covers every
    date-pinned SKU), so an exact hit is tried first, then the provider's
    ``-latest`` alias, then the shortest id that starts with the family — which
    is the tie-break that keeps ``codestral`` off ``codestral-embed``.
    """
    if model in page:
        return page[model]
    if f"{model}-latest" in page:
        return page[f"{model}-latest"]
    candidates = sorted((k for k in page if k.startswith(model)), key=len)
    return page[candidates[0]] if candidates else None


@dataclass(frozen=True)
class Finding:
    """One disagreement, or one thing that could not be checked."""

    source: str
    model: str
    #: "mismatch" | "missing" | "unverifiable" | "unpriced"
    kind: str
    field: str = ""
    page: float | None = None
    sheet: float | None = None
    note: str = ""

    def line(self) -> str:
        """The finding as one line, for an issue body or a terminal."""
        if self.kind == "mismatch":
            page = "—" if self.page is None else f"${prices.format_usd(self.page)}"
            sheet = "—" if self.sheet is None else f"${prices.format_usd(self.sheet)}"
            return f"{self.model} {self.field}: page {page}, sheet {sheet}"
        return f"{self.model}: {self.note}" if self.note else self.model


@dataclass
class Report:
    """Every source's outcome for one run."""

    findings: list[Finding] = field(default_factory=list)
    #: source -> models parsed off its page
    parsed: dict[str, int] = field(default_factory=dict)
    #: source -> models on the sheet compared against it
    compared: dict[str, int] = field(default_factory=dict)

    def of(self, source: str, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.source == source and f.kind == kind]

    @property
    def mismatches(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "mismatch"]

    @property
    def sources(self) -> list[str]:
        return sorted({*PARSERS, *SKIPPED})


def reconcile_source(source: str, text: str | None) -> tuple[list[Finding], dict[str, dict]]:
    """Compare one source's page against the sheet. Never raises on bad text."""
    if source in SKIPPED:
        return [Finding(source, "—", "unverifiable", note=SKIPPED[source])], {}
    if text is None:
        return [
            Finding(source, "—", "unverifiable", note="no committed page text for this source")
        ], {}

    try:
        page = PARSERS[source](text)
    except Exception as exc:  # noqa: BLE001 — a page that broke the parser is a finding
        return [
            Finding(source, "—", "unverifiable", note=f"parser failed: {type(exc).__name__}: {exc}")
        ], {}

    findings: list[Finding] = []
    unpublished = NOT_PUBLISHED.get(source, {})
    matched: set[str] = set()

    for model in sorted(m for m in prices._PRICES if owner(m) == source):
        row = match_row(model, page)
        if row is None:
            findings.append(
                Finding(source, model, "missing", note="on the sheet, no row found on the page")
            )
            continue
        matched.add(row["model"])
        sheet = sheet_rates(model)
        for name in FIELDS:
            if name in unpublished:
                findings.append(
                    Finding(source, model, "unverifiable", field=name, note=unpublished[name])
                )
                continue
            published, ours = row.get(name), sheet[name]
            if published is None:
                findings.append(
                    Finding(
                        source,
                        model,
                        "unverifiable",
                        field=name,
                        sheet=ours,
                        note="no figure for this field in the page text",
                    )
                )
            elif ours is None:
                findings.append(
                    Finding(
                        source,
                        model,
                        "mismatch",
                        field=name,
                        page=published,
                        sheet=None,
                        note="the page publishes this tier; the sheet carries no row for it",
                    )
                )
            elif abs(published - ours) > 1e-9:
                findings.append(
                    Finding(source, model, "mismatch", field=name, page=published, sheet=ours)
                )

    for model in sorted(page):
        if model in matched:
            continue
        if any(page[model].get(name) is not None for name in FIELDS):
            findings.append(
                Finding(source, model, "unpriced", note="priced on the page, not on the sheet")
            )
    return findings, page


def reconcile(pages: dict[str, str | None]) -> Report:
    """Reconcile every source. *pages* maps source name to its committed text."""
    report = Report()
    for source in sorted({*PARSERS, *SKIPPED}):
        findings, page = reconcile_source(source, pages.get(source))
        report.findings.extend(findings)
        report.parsed[source] = len(page)
        report.compared[source] = len({f.model for f in findings if f.kind != "unpriced"} - {"—"})
    return report


# --------------------------------------------------------------------------- #
# The watch's classification, borrowed
# --------------------------------------------------------------------------- #

#: A drift row in WATCH.md: date, source, status, lines, classification.
_WATCH_ROW = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*`([^`]+)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|"
)


def latest_classifications(watch_md: str) -> dict[str, str]:
    """Source -> the newest classification the watch's LLM gave it.

    Reconcile findings and watch classifications answer different questions —
    "is this number wrong" and "did this page move" — and a human reading a
    drift issue wants both in one place. This borrows the second rather than
    re-deriving it: the classifier already ran, through ``offpeak``, and paid.
    """
    latest: dict[str, tuple[str, str]] = {}
    for line in watch_md.splitlines():
        match = _WATCH_ROW.match(line.strip())
        if match is None:
            continue
        date, name, _status, _lines, label = match.groups()
        label = label.strip()
        if label in ("", "—"):
            continue
        source = name.split(":")[0]
        if source not in latest or latest[source][0] <= date:
            latest[source] = (date, label)
    return {source: f"{label} ({date})" for source, (date, label) in latest.items()}


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #

_HEADER = """# RECONCILE — sheet against page

`tools/sheet_watch.py` asks whether a page moved since yesterday. This asks
whether the page and `src/offpeak/prices.py` agree **today** — which is the only
question that catches a row that was already wrong when the watch took its first
reading, because no hash diff spans a baseline.

Rates are parsed out of the page text committed in `pages/`, so every figure
below is reproducible from this branch with no network at all.

**No number here has edited the price sheet**, and nothing in CI may. Same rule
as the watch, for the same reason: a parser confident enough to rewrite
`prices.py` from scraped text would eventually launder a table-layout change
into a receipt.

`classification` is the sheet watch's most recent LLM label for that source —
what moved, per the classifier that already ran. It answers a different question
than the rows below it and is here so a reader has both at once.
"""


def render_reconcile_md(report: Report, classifications: dict[str, str], date: str) -> str:
    """The whole report, rebuilt each run — it describes now, not a history."""
    rows = []
    for source in report.sources:
        mismatch = report.of(source, "mismatch")
        missing = report.of(source, "missing")
        unverifiable = report.of(source, "unverifiable")
        if source in SKIPPED:
            status = "skipped"
        elif mismatch:
            status = "**drift**"
        else:
            status = "ok" if report.parsed.get(source) else "unreadable"
        rows.append(
            f"| `{source}` | {status} | {len(mismatch)} | {len(missing)} | "
            f"{len(unverifiable)} | {report.parsed.get(source, 0)} | "
            f"{classifications.get(source, '—')} |"
        )

    body = [
        _HEADER,
        f"Reconciled {date} against `offpeak.prices` sheet **{prices.sheet_date()}**.\n",
        "| source | status | mismatches | missing | unverifiable | models on page "
        "| classification |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        "\n".join(rows),
        "",
    ]

    for source in report.sources:
        mismatch = report.of(source, "mismatch")
        missing = report.of(source, "missing")
        unverifiable = report.of(source, "unverifiable")
        unpriced = report.of(source, "unpriced")
        body.append(f"## `{source}`\n")
        if classifications.get(source):
            body.append(f"Sheet watch's latest classification: **{classifications[source]}**.\n")
        if mismatch:
            body.append("### Mismatches\n")
            body.append("| model | field | page | sheet | note |")
            body.append("| --- | --- | --- | --- | --- |")
            for finding in mismatch:
                page = "—" if finding.page is None else f"${prices.format_usd(finding.page)}"
                ours = "—" if finding.sheet is None else f"${prices.format_usd(finding.sheet)}"
                body.append(
                    f"| `{finding.model}` | {finding.field} | {page} | {ours} | {finding.note} |"
                )
            body.append("")
        if missing:
            body.append("### Missing from the page\n")
            body.extend(f"- `{f.model}` — {f.note}" for f in missing)
            body.append("")
        if unverifiable:
            body.append(
                f"### Unverifiable ({len(unverifiable)})\n\n"
                "Not compared, and not counted as agreement.\n"
            )
            seen: set[str] = set()
            for finding in unverifiable:
                if finding.note in seen:
                    continue
                seen.add(finding.note)
                count = sum(1 for f in unverifiable if f.note == finding.note)
                body.append(f"- {finding.note} — {count} field(s)")
            body.append("")
        if unpriced:
            body.append(
                f"### On the page, not on the sheet ({len(unpriced)})\n\n"
                "Informational. The sheet omits models on purpose; see the "
                "comments in `prices.py` before adding one.\n"
            )
            body.append(", ".join(f"`{f.model}`" for f in unpriced))
            body.append("")
        if not (mismatch or missing or unverifiable or unpriced):
            body.append("Nothing to report — every sheet row was found and matched.\n")
    return "\n".join(body).rstrip() + "\n"


def issue_bodies(report: Report, classifications: dict[str, str], date: str) -> dict[str, dict]:
    """Per-source issue payloads, for a CI step that opens one issue per source.

    One issue per source rather than one per run: a source's drift is a
    conversation with one provider's page, and re-opening it daily would bury
    the thread the row belongs to.
    """
    payloads: dict[str, dict] = {}
    for source in report.sources:
        mismatch = report.of(source, "mismatch")
        missing = report.of(source, "missing")
        if not (mismatch or missing):
            continue
        lines = [
            f"`tools/sheet_reconcile.py` on {date}, against `offpeak.prices` "
            f"sheet **{prices.sheet_date()}**.",
            "",
            f"Sheet watch's latest classification for this source: "
            f"**{classifications.get(source, 'none recorded')}**.",
            "",
        ]
        if mismatch:
            lines += ["### Mismatches", "", "```"]
            lines += [f.line() for f in mismatch]
            lines += ["```", ""]
        if missing:
            lines += ["### Missing from the page", "", "```"]
            lines += [f.line() for f in missing]
            lines += ["```", ""]
        lines += [
            "The reconciler **never edits `src/offpeak/prices.py`**, and neither "
            "may CI: a page moves for reasons that are not a price change. A "
            "human settles what these mean and edits the sheet by hand.",
            "",
            "Full report: `board-data:watch/RECONCILE.md`.",
        ]
        payloads[source] = {
            "title": f"sheet drift: {source}",
            "body": "\n".join(lines),
            "mismatches": len(mismatch),
            "missing": len(missing),
            "classification": classifications.get(source, ""),
        }
    return payloads


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def load_pages(pages_dir: Path, sources: Iterable[str]) -> dict[str, str | None]:
    """Read each source's committed page text.

    ``sheet_watch`` writes ``anthropic__pricing.txt`` — the source name with its
    colon swapped. A source with several watched pages resolves to its
    ``:pricing`` one, which is the page that carries rates.
    """
    pages: dict[str, str | None] = {}
    for source in sources:
        path = pages_dir / f"{source}__pricing.txt"
        try:
            pages[source] = path.read_text(encoding="utf-8")
        except OSError:
            pages[source] = None
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--pages", type=Path, required=True, help="watch/pages dir on board-data"
    )
    parser.add_argument(
        "--outdir", type=Path, default=None, help="write RECONCILE.md and reconcile.json here"
    )
    parser.add_argument(
        "--watch",
        type=Path,
        default=None,
        help="WATCH.md to read the classifier's labels from (default: --pages/../WATCH.md)",
    )
    parser.add_argument("--date", default="", help="date stamp for the report (default: today)")
    args = parser.parse_args(argv)

    if args.date:
        date = args.date
    else:
        from datetime import datetime, timezone

        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sources = sorted({*PARSERS, *SKIPPED})
    report = reconcile(load_pages(args.pages, sources))

    watch_path = args.watch or (args.pages.parent / "WATCH.md")
    classifications = {}
    if watch_path.exists():
        classifications = latest_classifications(watch_path.read_text(encoding="utf-8"))

    print(f"sheet_reconcile {date}: sheet {prices.sheet_date()}")
    print(f"{'source':<12} {'status':<10} {'mism':>5} {'miss':>5} {'unver':>6} {'models':>7}")
    for source in sources:
        mismatch = report.of(source, "mismatch")
        if source in SKIPPED:
            status = "skipped"
        elif mismatch:
            status = "DRIFT"
        else:
            status = "ok" if report.parsed.get(source) else "unreadable"
        print(
            f"{source:<12} {status:<10} {len(mismatch):>5} "
            f"{len(report.of(source, 'missing')):>5} "
            f"{len(report.of(source, 'unverifiable')):>6} {report.parsed.get(source, 0):>7}"
        )
    for finding in report.mismatches:
        print(f"  MISMATCH {finding.source}: {finding.line()}")
    for finding in (f for f in report.findings if f.kind == "missing"):
        print(f"  MISSING  {finding.source}: {finding.line()}")

    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)
        (args.outdir / "RECONCILE.md").write_text(
            render_reconcile_md(report, classifications, date), encoding="utf-8"
        )
        (args.outdir / "reconcile.json").write_text(
            json.dumps(issue_bodies(report, classifications, date), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        print(f"  wrote {args.outdir / 'RECONCILE.md'} and {args.outdir / 'reconcile.json'}")

    return 1 if report.mismatches else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
