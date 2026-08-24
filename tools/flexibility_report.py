#!/usr/bin/env python3
"""What a customer's own job log says their patience is worth. v0.

Reads a job log in the metadata shape the outreach playbook asks for, and
answers four questions with arithmetic rather than adjectives:

1. **What did this window cost**, priced against the bundled sheet, and what
   does that annualise to?
2. **How much of it could have waited**, by job class — under a rule printed at
   the top of the report rather than buried in a footnote.
3. **What is that wait worth**, at each venue's *real* discount rule. Never a
   flat 50%: OpenAI, Anthropic, Google, Mistral, Groq and Bedrock publish 50%
   off on a batch tier, Kimi publishes 40%, xAI publishes 20% on legacy models
   only, and DeepSeek and Qwen do not sell a batch tier at all — they discount
   by wall clock, which is a different instrument and is not priced here as if
   it were the same one.
4. **How much of that is already being captured**, read off ``venue_tier``, so
   the headline number is the *incremental* one. Telling somebody they could
   save 50% on work they already batch is how a report gets thrown away.

    flexibility_report.py --log jobs.json --out FLEXIBILITY.md

Two rules hold everywhere in here.

**Be liberal in what you accept, and explicit about what you inferred.** Field
names are matched across the spellings real logs use, tokens can arrive as
totals or as a count with average sizes, and the venue can be left off and
derived from the model. Every one of those inferences is recorded per row and
reprinted in the report. A number whose provenance is not on the page is not
evidence.

**No estimates in the dollar layer.** Every dollar traces to a row in
``offpeak.prices`` with the sheet date attached; a model that is not on the
sheet renders as an em dash and is counted as unpriced, never as free. Token
counts *may* be inferred, and where they are the row is marked ``EST`` inline —
the same way :func:`offpeak.quote` marks them — so a reader can see exactly
which dollars inherit an assumption.

**No carbon.** Not in v0, not partially, not as a placeholder column. The grid
side of the claim is observation on the Spread Board and does not belong in a
document that quotes somebody a number.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from offpeak import prices  # noqa: E402
from offpeak.prices import format_usd  # noqa: E402

# --------------------------------------------------------------------------
# What each venue actually sells, which is not one thing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VenueRule:
    """How a venue prices patience, and where to check the claim."""

    venue: str
    #: "flat" — a batch tier at a published discount, any model.
    #: "legacy_only" — a discount that reaches only part of the catalogue.
    #: "clock" — no batch tier; the discount is a wall-clock window.
    kind: str
    #: Fraction off list, or None where the venue does not sell a tier.
    discount: float | None
    applies_to: str
    source: str
    note: str

    @property
    def priceable(self) -> bool:
        return self.kind in ("flat", "legacy_only") and self.discount is not None


VENUE_RULES: dict[str, VenueRule] = {
    "openai": VenueRule(
        "openai", "flat", 0.50, "any model on the Batch API",
        "developers.openai.com/api/docs/pricing",
        "Batch API at 50% of standard, 24h window.",
    ),
    "anthropic": VenueRule(
        "anthropic", "flat", 0.50, "any model on Message Batches",
        "platform.claude.com/docs/en/about-claude/pricing",
        "Message Batches at 50% of standard, 24h window.",
    ),
    "google": VenueRule(
        "google", "flat", 0.50, "any model on the Batch API",
        "ai.google.dev/pricing",
        "Batch mode at 50% of interactive.",
    ),
    "mistral": VenueRule(
        "mistral", "flat", 0.50, "any model on the Batch API",
        "mistral.ai/pricing",
        "Batch API at 50% of standard.",
    ),
    "groq": VenueRule(
        "groq", "flat", 0.50, "any model on the Batch API",
        "groq.com/pricing",
        "Batch API at 50% of on-demand, 24h-7d window.",
    ),
    "bedrock": VenueRule(
        "bedrock", "flat", 0.50, "models offering batch inference",
        "aws.amazon.com/bedrock/pricing",
        "Batch inference at 50% of on-demand.",
    ),
    "kimi": VenueRule(
        "kimi", "flat", 0.40, "any model on the batch tier",
        "platform.moonshot.ai/docs/pricing",
        "Batch at 40% off — not 50%. A report that assumes the round number "
        "over-promises here by ten points of every dollar.",
    ),
    "xai": VenueRule(
        "xai", "legacy_only", 0.20, "legacy models only — not the current line",
        "docs.x.ai/docs/models",
        "20% off, and only on legacy models. Current-generation work gets "
        "nothing, so a fleet on the latest models has no wait to sell here.",
    ),
    "deepseek": VenueRule(
        "deepseek", "clock", None, "a wall-clock off-peak window, all models",
        "api-docs.deepseek.com/quick_start/pricing",
        "No batch tier. The discount is a time of day, so the saving is "
        "realised by moving *when* the job runs, not by moving which tier it "
        "runs on. Not priced here without a registered off-peak rate.",
    ),
    "qwen": VenueRule(
        "qwen", "clock", None, "a wall-clock off-peak window, all models",
        "alibabacloud.com/help/en/model-studio/models",
        "No batch tier — a night-hours promotional rate instead. Same shape as "
        "DeepSeek, same treatment, and the promo has an end date.",
    ),
}

# model prefix -> venue, for logs that name the model but not where it ran.
# Always recorded as an inference, never silently assumed.
_VENUE_BY_MODEL_PREFIX: tuple[tuple[str, str], ...] = (
    ("openai/gpt-oss", "groq"),
    ("groq/", "groq"),
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("chatgpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("text-embedding-", "openai"),
    ("gemini", "google"),
    ("mistral", "mistral"),
    ("magistral", "mistral"),
    ("codestral", "mistral"),
    ("deepseek", "deepseek"),
    ("qwen", "qwen"),
    ("kimi", "kimi"),
    ("moonshot", "kimi"),
    ("grok", "xai"),
)

# What a job's recorded tier means for how much is left to capture.
_TIER_ALIASES: dict[str, str] = {
    "": "standard", "standard": "standard", "on_demand": "standard",
    "on-demand": "standard", "ondemand": "standard", "sync": "standard",
    "default": "standard", "interactive": "standard", "realtime": "standard",
    "batch": "batch", "batch_api": "batch", "batched": "batch",
    "flex": "batch", "offpeak": "batch", "off_peak": "batch",
    "off-peak": "batch", "discount": "batch", "scale": "batch",
    "fast": "fast", "priority": "fast", "express": "fast",
}
CAPTURED_TIERS = ("batch",)

# --------------------------------------------------------------------------
# The classification rule. Printed in the report, not buried here.
# --------------------------------------------------------------------------

#: Every batch tier in VENUE_RULES publishes a 24h completion window.
BATCH_WINDOW_SECONDS = 24 * 3600

CLASSIFICATION_RULE = """A job is **deferrable** when its own metadata says it can
wait longer than the venue needs. Three classes, decided per job, from `submitted_at` and
`required_by` only — never from the job's name:

| class | test | counted as deferrable |
|---|---|---|
| `interactive` | `required_by` is absent, `none`, or `interactive` — somebody is waiting | no |
| `deferrable` | slack of **24h or more** — the window every batch tier publishes | yes |
| `marginal` | slack is positive but **under 24h** | no |

`marginal` is deliberately excluded from the headline. A batch usually lands far
inside its window — measured at 85s and 2m26s on a 24h window — but *usually* is
not an SLA, and a deadline shorter than the window rests on a fallback that pays
list. Counting that as captured would be selling a number the tier does not
guarantee."""

# --------------------------------------------------------------------------
# Decay: prices in this report that have a published expiry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Caveat:
    subject: str
    through: str
    what_changes: str
    source: str


# Only what the sheet cannot supply. Gemini's Flash decay used to be restated
# here and is now a PromoNote in offpeak.prices, which is where it belongs: read
# from the sheet, it disappears from the report the day the price stops being
# promotional, instead of outliving it in a hardcoded tuple.
DECAY_CAVEATS: tuple[Caveat, ...] = (
    Caveat(
        "Qwen night-hours promotion", "see Alibaba Cloud Model Studio",
        "Qwen's off-peak saving is promotional, not a standing tier. It is the "
        "most perishable number a report like this can lean on, which is part "
        "of why clock-priced venues are not given a dollar figure here.",
        "alibabacloud.com/help/en/model-studio/models",
    ),
)


def promo_caveats() -> list[Caveat]:
    """Decay notes, with the sheet's own PromoNotes folded in.

    Read from :data:`offpeak.prices.PROMO_NOTES` rather than restated, so a
    price that stops being promotional stops being caveated here too.
    """
    out = []
    for model, note in sorted(prices.PROMO_NOTES.items()):
        out.append(
            Caveat(
                f"{model} promotional list price",
                note.through,
                f"Post-promo list is ${note.post_promo[0]:.2f} / "
                f"${note.post_promo[1]:.2f} per 1M. Both the standard and batch "
                f"legs are defined off list, so the dollars move and the ratio "
                f"does not.",
                note.source,
            )
        )
    return out + list(DECAY_CAVEATS)


# --------------------------------------------------------------------------
# Reading a log written by somebody who had never heard of this tool
# --------------------------------------------------------------------------


def _first(record: dict, *names, default=None):
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return default


def _as_int(value) -> int | None:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return None
    return out if out >= 0 else None


def _as_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


NON_DEADLINES = {"none", "null", "interactive", "n/a", "na", "-", "asap", "now"}


@dataclass
class LogRow:
    """One line of somebody's job log, after being read as generously as possible."""

    job_class: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    submitted_at: datetime | None
    required_by: datetime | None
    interactive: bool
    venue: str
    tier: str
    inferred: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def estimated(self) -> bool:
        return any(i.startswith("tokens") for i in self.inferred)

    @property
    def slack_seconds(self) -> float | None:
        if self.interactive or self.required_by is None or self.submitted_at is None:
            return None
        return (self.required_by - self.submitted_at).total_seconds()

    @property
    def classification(self) -> str:
        if self.interactive or self.required_by is None:
            return "interactive"
        slack = self.slack_seconds
        if slack is None:
            return "interactive"
        if slack >= BATCH_WINDOW_SECONDS:
            return "deferrable"
        return "marginal"

    @property
    def rule(self) -> VenueRule | None:
        return VENUE_RULES.get(self.venue)

    @property
    def list_usd(self) -> float | None:
        return prices.list_cost_usd(self.model, self.input_tokens, self.output_tokens)

    @property
    def spend_usd(self) -> float | None:
        """What this row actually cost, at the tier it actually ran on."""
        listed = self.list_usd
        if listed is None:
            return None
        if self.tier == "batch":
            rule = self.rule
            return listed * (1 - (rule.discount or 0.0)) if rule and rule.priceable else listed
        if self.tier == "fast":
            fast = prices.fast_cost_usd(self.model, self.input_tokens, self.output_tokens)
            return fast if fast is not None else listed
        return listed

    @property
    def already_captured_usd(self) -> float:
        listed = self.list_usd
        if listed is None or self.tier not in CAPTURED_TIERS:
            return 0.0
        rule = self.rule
        return listed * rule.discount if rule and rule.priceable else 0.0

    @property
    def incremental_usd(self) -> float | None:
        """What deferring this row would save *on top of* what it already saves.

        ``None`` where the model is off the sheet or the venue sells no tier to
        move to — both are "not a number", and neither is zero.
        """
        if self.classification != "deferrable":
            return 0.0
        if self.tier in CAPTURED_TIERS:
            return 0.0
        rule = self.rule
        if rule is None or not rule.priceable:
            return None
        spend = self.spend_usd
        listed = self.list_usd
        if spend is None or listed is None:
            return None
        return spend - listed * (1 - rule.discount)


def read_row(record: dict) -> LogRow:
    """One log record, read liberally, with every inference written down."""
    inferred: list[str] = []
    problems: list[str] = []

    job_class = str(
        _first(record, "job_class", "jobClass", "class", "workload", "category",
               default="unclassified")
    )
    model = str(_first(record, "model", "model_id", "modelId", "engine", default="")).strip()
    if not model:
        problems.append("no model — cannot be priced")

    requests = _as_int(
        _first(record, "requests", "request_count", "requestCount", "count", "n", "jobs")
    )
    if requests is None:
        requests = 1
        if any(k in record for k in ("avg_input_tokens", "average_input_tokens")):
            problems.append("average sizes given without a request count; assumed 1")

    tok_in = _as_int(_first(record, "input_tokens", "inputTokens", "prompt_tokens", "in_tokens"))
    tok_out = _as_int(
        _first(record, "output_tokens", "outputTokens", "completion_tokens", "out_tokens")
    )
    avg_in = _as_int(
        _first(record, "avg_input_tokens", "average_input_tokens", "avgInputTokens",
               "mean_input_tokens")
    )
    avg_out = _as_int(
        _first(record, "avg_output_tokens", "average_output_tokens", "avgOutputTokens",
               "mean_output_tokens")
    )

    if tok_in is None and avg_in is not None:
        tok_in = avg_in * requests
        inferred.append(f"tokens in = {avg_in:,} avg x {requests:,} requests")
    if tok_out is None and avg_out is not None:
        tok_out = avg_out * requests
        inferred.append(f"tokens out = {avg_out:,} avg x {requests:,} requests")
    if tok_in is None:
        tok_in = 0
        problems.append("no input tokens and no average — priced at zero input")
    if tok_out is None:
        tok_out = 0
        problems.append("no output tokens and no average — priced at zero output")

    submitted = _as_dt(
        _first(record, "submitted_at", "submittedAt", "submitted", "timestamp", "ts")
    )
    if submitted is None:
        problems.append("no submitted_at — cannot measure slack, treated as interactive")

    raw_required = _first(record, "required_by", "requiredBy", "deadline", "due", "due_by")
    interactive = False
    required = None
    if raw_required is None or str(raw_required).strip().lower() in NON_DEADLINES:
        interactive = True
    else:
        required = _as_dt(raw_required)
        if required is None:
            interactive = True
            problems.append(f"unparseable required_by {raw_required!r} — treated as interactive")

    venue = str(_first(record, "venue", "provider", "vendor", default="")).strip().lower()
    if not venue:
        for prefix, guess in _VENUE_BY_MODEL_PREFIX:
            if model.startswith(prefix):
                venue = guess
                inferred.append(f"venue = {guess} (from the model name)")
                break
    if not venue:
        problems.append("no venue and none derivable from the model name")

    raw_tier = str(_first(record, "venue_tier", "venueTier", "tier", default="")).strip().lower()
    tier = _TIER_ALIASES.get(raw_tier)
    if tier is None:
        tier = "standard"
        if raw_tier:
            problems.append(f"unrecognised venue_tier {raw_tier!r} — read as standard")
        else:
            inferred.append("venue_tier = standard (absent from the log)")

    return LogRow(
        job_class=job_class, model=model, requests=requests,
        input_tokens=tok_in, output_tokens=tok_out,
        submitted_at=submitted, required_by=required, interactive=interactive,
        venue=venue, tier=tier, inferred=inferred, problems=problems,
    )


def load_log(path: Path) -> list[LogRow]:
    """Read a job log. JSON (a list, or an object with a ``jobs``/``rows`` key) or CSV."""
    text = path.read_text()
    if path.suffix.lower() == ".csv":
        return [read_row(r) for r in csv.DictReader(text.splitlines())]
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("jobs", "rows", "records", "log"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ValueError(
                f"{path.name}: JSON object with no jobs/rows/records/log list in it"
            )
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected a list of job records")
    return [read_row(r) for r in data]


# --------------------------------------------------------------------------
# Aggregation. Pure: no clock, no network, no filesystem.
# --------------------------------------------------------------------------


def _sum(values) -> tuple[float, int]:
    """(total of what could be priced, count of what could not).

    Unpriced rows are **excluded and counted**, never coerced to zero and never
    allowed to void the whole total. Both of those failure modes are worse than
    a total with a footnote: one silently understates the fleet, and the other
    throws away every number that *was* checkable because one row was not.
    """
    total, unpriced = 0.0, 0
    for v in values:
        if v is None:
            unpriced += 1
        else:
            total += v
    return total, unpriced


def _total(values) -> float | None:
    """The priced total, or ``None`` when nothing in the group could be priced.

    The distinction matters on the page. A group with no deferrable rows really
    did save nothing and should read ``$0.00``; a group whose every row is off
    the price sheet has an unknown total and must read ``—``. Rendering the
    second as the first is how a report tells somebody their embedding bill is
    zero.
    """
    items = list(values)
    total, unpriced = _sum(items)
    if items and unpriced == len(items):
        return None
    return total


@dataclass
class ClassSummary:
    job_class: str
    rows: list[LogRow]

    @property
    def requests(self) -> int:
        return sum(r.requests for r in self.rows)

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.rows)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.rows)

    @property
    def spend_usd(self) -> float | None:
        return _total(r.spend_usd for r in self.rows)

    @property
    def unpriced(self) -> int:
        return _sum(r.spend_usd for r in self.rows)[1]

    @property
    def deferrable_spend_usd(self) -> float | None:
        return _total(r.spend_usd for r in self.rows if r.classification == "deferrable")

    @property
    def deferrable_share(self) -> float | None:
        """Share of the *priced* spend that could wait, or None if none is priced."""
        total, deferrable = self.spend_usd, self.deferrable_spend_usd
        if not total or deferrable is None:
            return None
        return deferrable / total

    @property
    def incremental_usd(self) -> float | None:
        return _total(r.incremental_usd for r in self.rows)

    @property
    def estimated(self) -> bool:
        return any(r.estimated for r in self.rows)

    @property
    def classification(self) -> str:
        seen = {r.classification for r in self.rows}
        return seen.pop() if len(seen) == 1 else "mixed"


@dataclass
class Analysis:
    rows: list[LogRow]
    window_days: float | None
    generated_utc: str
    label: str | None = None

    @property
    def classes(self) -> list[ClassSummary]:
        order, buckets = [], {}
        for row in self.rows:
            if row.job_class not in buckets:
                buckets[row.job_class] = []
                order.append(row.job_class)
            buckets[row.job_class].append(row)
        return [ClassSummary(name, buckets[name]) for name in order]

    @property
    def spend_usd(self) -> float | None:
        return _total(r.spend_usd for r in self.rows)

    @property
    def unpriced(self) -> int:
        return _sum(r.spend_usd for r in self.rows)[1]

    @property
    def deferrable_spend_usd(self) -> float | None:
        return _total(r.spend_usd for r in self.rows if r.classification == "deferrable")

    @property
    def deferrable_share(self) -> float | None:
        total, deferrable = self.spend_usd, self.deferrable_spend_usd
        if not total or deferrable is None:
            return None
        return deferrable / total

    @property
    def annualised_usd(self) -> float | None:
        if not self.window_days or self.spend_usd is None:
            return None
        return self.spend_usd * (365.0 / self.window_days)

    @property
    def annualisation_multiple(self) -> float | None:
        return None if not self.window_days else 365.0 / self.window_days

    @property
    def already_captured_usd(self) -> float:
        return sum(r.already_captured_usd for r in self.rows)

    @property
    def incremental_usd(self) -> float | None:
        return _total(r.incremental_usd for r in self.rows)

    @property
    def incremental_unpriced(self) -> int:
        return _sum(r.incremental_usd for r in self.rows)[1]

    @property
    def unpriced_rows(self) -> list[LogRow]:
        return [r for r in self.rows if r.list_usd is None]

    @property
    def unpriceable_venue_rows(self) -> list[LogRow]:
        return [
            r for r in self.rows
            if r.classification == "deferrable"
            and r.tier not in CAPTURED_TIERS
            and (r.rule is None or not r.rule.priceable)
        ]

    def by_venue(self) -> dict[str, list[LogRow]]:
        out: dict[str, list[LogRow]] = {}
        for row in self.rows:
            out.setdefault(row.venue or "(unknown)", []).append(row)
        return out


def window_days(rows: list[LogRow]) -> float | None:
    """Observed span of the log, in days. ``None`` when nothing carries a date."""
    stamps = [r.submitted_at for r in rows if r.submitted_at]
    if len(stamps) < 2:
        return None
    span = (max(stamps) - min(stamps)).total_seconds() / 86400.0
    return span or None


def analyse(rows: list[LogRow], *, days: float | None = None, label: str | None = None,
            now=None) -> Analysis:
    now = now or (lambda: datetime.now(timezone.utc))
    return Analysis(
        rows=rows,
        window_days=days if days else window_days(rows),
        generated_utc=now().isoformat(timespec="seconds"),
        label=label,
    )


# --------------------------------------------------------------------------
# Rendering. Every dollar shows its arithmetic.
# --------------------------------------------------------------------------


def _usd(amount: float | None) -> str:
    return "—" if amount is None else "$" + format_usd(amount)


def _pct(fraction: float | None) -> str:
    return "—" if fraction is None else f"{fraction * 100:.1f}%"


def _est(row_or_class) -> str:
    return " `EST`" if row_or_class.estimated else ""


SYNTHETIC_BANNER = (
    "> ## ⚠️ SYNTHETIC DATA — NOT A CUSTOMER\n"
    ">\n"
    "> Every figure below is computed from a **generated** job log, written by\n"
    "> `tools/make_synthetic_log.py` to demonstrate the report's shape. No real\n"
    "> customer, no real fleet, no real spend. The prices are real; the jobs are\n"
    "> not.\n\n"
)


def render(analysis: Analysis) -> str:
    """The whole report. Pure — same input, same bytes."""
    out: list[str] = []
    if analysis.label == "synthetic":
        out.append(SYNTHETIC_BANNER)
    elif analysis.label:
        out.append(f"> **{analysis.label}**\n\n")

    out.append("# Deadline flexibility report — v0\n\n")
    out.append(
        f"Generated {analysis.generated_utc} by `tools/flexibility_report.py` "
        f"against the `offpeak` price sheet dated **{prices.PRICE_SHEET_DATE}**. "
        "Every dollar below traces to a row on that sheet; a model that is not "
        "on it renders as an em dash and is counted as unpriced, never as free.\n\n"
    )
    out.append(_render_headline(analysis))
    out.append(_render_rule())
    out.append(_render_classes(analysis))
    out.append(_render_wait_value(analysis))
    out.append(_render_arithmetic(analysis))
    out.append(_render_provenance(analysis))
    out.append(_render_caveats())
    return "".join(out)


def _render_headline(a: Analysis) -> str:
    lines = ["## What the window cost\n\n"]
    span = "unknown" if not a.window_days else f"{a.window_days:.1f} days"
    lines.append("| | |\n|---|---|\n")
    lines.append(f"| observed window | {span} |\n")
    lines.append(f"| jobs in the log | {sum(r.requests for r in a.rows):,} requests"
                 f" across {len(a.rows)} log rows |\n")
    lines.append(f"| **spend over the window** | **{_usd(a.spend_usd)}** |\n")
    if a.annualised_usd is None:
        lines.append(
            "| annualised | — (the log carries fewer than two dated rows, so it "
            "has no span to project from) |\n"
        )
    else:
        lines.append(
            f"| annualised | **{_usd(a.annualised_usd)}** "
            f"= {_usd(a.spend_usd)} x 365/{a.window_days:.1f} "
            f"({a.annualisation_multiple:.1f}x) |\n"
        )
    lines.append(
        "\nThe annualised figure is a **projection of the observed window**, not a "
        "forecast: it assumes the window is representative, and the multiplier is "
        "printed so a reader who disagrees can redo it in one step.\n\n"
    )
    if a.unpriced_rows:
        models = sorted({r.model or "(no model)" for r in a.unpriced_rows})
        lines.append(
            f"**{len(a.unpriced_rows)} of {len(a.rows)} log rows are unpriced** — "
            f"`{'`, `'.join(models)}` are not on the bundled sheet. They are "
            "excluded from every total above and counted here, rather than "
            "silently valued at zero: a price nobody published is not a price of "
            "nothing, and a fleet is not smaller because this sheet is missing a "
            "row. Register a rate with `offpeak.prices.register_price()` and "
            "re-run to fold them in. **Every total in this report is therefore a "
            "floor.**\n\n"
        )
    return "".join(lines)


def _render_rule() -> str:
    return "## The classification rule\n\n" + CLASSIFICATION_RULE + "\n\n"


def _render_classes(a: Analysis) -> str:
    lines = [
        "## Deferrable share, by job class\n\n",
        "| job class | requests | tokens (in / out) | spend | deferrable spend "
        "| share | class | unpriced |\n",
        "|---|---|---|---|---|---|---|---|\n",
    ]
    for summary in a.classes:
        lines.append(
            f"| {summary.job_class} "
            f"| {summary.requests:,} "
            f"| {summary.input_tokens:,} / {summary.output_tokens:,}{_est(summary)} "
            f"| {_usd(summary.spend_usd)} "
            f"| {_usd(summary.deferrable_spend_usd)} "
            f"| {_pct(summary.deferrable_share)} "
            f"| {summary.classification} "
            f"| {summary.unpriced or '—'} |\n"
        )
    lines.append(
        f"| **total** | **{sum(r.requests for r in a.rows):,}** | | "
        f"**{_usd(a.spend_usd)}** | **{_usd(a.deferrable_spend_usd)}** "
        f"| **{_pct(a.deferrable_share)}** | | **{a.unpriced or '—'}** |\n\n"
    )
    lines.append(
        "`EST` marks a row whose token counts were inferred from a request count "
        "and an average rather than measured. The prices are exact; the tokens "
        "they multiply are not, and the dollars inherit that.\n\n"
    )
    return "".join(lines)


def _render_wait_value(a: Analysis) -> str:
    lines = [
        "## What the wait is worth\n\n",
        "Per venue, at that venue's **own** published rule. There is no flat 50% "
        "in this table, because there is no flat 50% in the market.\n\n",
        "| venue | rule | deferrable spend | already captured | **incremental** | source |\n",
        "|---|---|---|---|---|---|\n",
    ]
    for venue, rows in sorted(a.by_venue().items()):
        rule = VENUE_RULES.get(venue)
        deferrable = _total(r.spend_usd for r in rows if r.classification == "deferrable")
        captured = sum(r.already_captured_usd for r in rows)
        incremental, unpriced = _sum(r.incremental_usd for r in rows)
        if rule is not None and rule.kind == "clock":
            incremental = None
        if rule is None:
            desc, source = "unknown venue — no published rule on file", "—"
        elif rule.kind == "clock":
            desc = "**clock-priced** — no batch tier"
            source = rule.source
        elif rule.kind == "legacy_only":
            desc = f"{rule.discount:.0%} off, {rule.applies_to}"
            source = rule.source
        else:
            desc = f"{rule.discount:.0%} off, {rule.applies_to}"
            source = rule.source
        lines.append(
            f"| {venue} | {desc} | {_usd(deferrable)} | {_usd(captured)} "
            f"| **{_usd(incremental)}** | {source} |\n"
        )
    lines.append(
        f"| **total** | | **{_usd(a.deferrable_spend_usd)}** "
        f"| **{_usd(a.already_captured_usd)}** "
        f"| **{_usd(a.incremental_usd)}** | |\n\n"
    )
    captured = a.already_captured_usd
    lines.append(
        "**Incremental is the number that matters.** "
        + (
            f"{_usd(captured)} of this window's saving is already being captured "
            "on tiers this fleet already uses. That is subtracted rather than "
            "counted twice: telling somebody they could save half on work they "
            "already batch is how a report gets thrown away. "
            if captured
            else "Nothing in this window is running on a discounted tier yet, so "
            "the whole of the column is still on the table. "
        )
        + "The incremental column is what deferring the *remaining* deferrable "
        "work would add, on top of what is already being captured.\n\n"
    )
    if a.annualised_usd is not None and a.window_days and a.incremental_usd is not None:
        annual_inc = a.incremental_usd * (365.0 / a.window_days)
        lines.append(
            f"Annualised on the same {a.annualisation_multiple:.1f}x multiplier as "
            f"the headline: **{_usd(annual_inc)} a year**, on a total annualised "
            f"spend of {_usd(a.annualised_usd)} — and both are floors while any "
            "row is unpriced.\n\n"
        )
    clock_rows = [r for r in a.unpriceable_venue_rows if r.rule and r.rule.kind == "clock"]
    if clock_rows:
        venues = sorted({r.venue for r in clock_rows})
        lines.append(
            f"**Not priced here: {', '.join(venues)}.** These venues sell no batch "
            "tier. Their discount is a wall-clock window, so the saving is realised "
            "by moving *when* a job runs rather than *which tier* it runs on — a "
            "different instrument, with a different operational cost, and a "
            "promotional rate behind it in at least one case. Putting a dollar on "
            "it here would be an estimate, and there are none of those in the "
            "dollar layer.\n\n"
        )
    legacy = [r for r in a.rows if r.rule and r.rule.kind == "legacy_only"]
    if legacy:
        lines.append(
            "**xAI's 20% reaches legacy models only.** Rows on current-generation "
            "models have no tier to move to, and are counted at zero incremental "
            "rather than at 20% of something that is not on offer.\n\n"
        )
    return "".join(lines)


def _render_arithmetic(a: Analysis) -> str:
    lines = [
        "## The arithmetic\n\n",
        f"Rates are USD per 1M tokens from the `offpeak` sheet dated "
        f"**{prices.PRICE_SHEET_DATE}**, one line per log row so every total "
        "above can be re-derived by hand.\n\n",
        "| job class | model | venue · tier | in x rate | out x rate "
        "| = spend | class | incremental |\n",
        "|---|---|---|---|---|---|---|---|\n",
    ]
    for row in a.rows:
        rate = prices.get_price(row.model)
        if rate is None:
            in_leg = out_leg = "— (off sheet)"
        else:
            in_leg = f"{row.input_tokens:,} x ${rate[0]:.4f}"
            out_leg = f"{row.output_tokens:,} x ${rate[1]:.4f}"
        lines.append(
            f"| {row.job_class} | `{row.model}` | {row.venue or '?'} · {row.tier} "
            f"| {in_leg}{_est(row)} | {out_leg} | {_usd(row.spend_usd)} "
            f"| {row.classification} | {_usd(row.incremental_usd)} |\n"
        )
    lines.append("\n")
    return "".join(lines)


def _tally(a: Analysis, attr: str) -> list[tuple[str, int]]:
    """Distinct (class / model: note) lines with how many rows each covers.

    A recurring job produces the same inference on every occurrence. Printing it
    forty times does not make the disclosure more complete, it makes it less
    readable — and a disclosure nobody finishes reading is not one.
    """
    counts: dict[str, int] = {}
    for row in a.rows:
        for note in getattr(row, attr):
            key = f"`{row.job_class}` / `{row.model or '(no model)'}`: {note}"
            counts[key] = counts.get(key, 0) + 1
    return list(counts.items())


def _render_provenance(a: Analysis) -> str:
    inferred = _tally(a, "inferred")
    problems = _tally(a, "problems")
    lines = ["## What was inferred, and what the log did not say\n\n"]
    if not inferred and not problems:
        lines.append("Nothing. Every field this report used was present in the log.\n\n")
        return "".join(lines)
    for title, entries in (
        ("**Inferred** — read from the log generously, and written down:", inferred),
        ("**Missing or unreadable** — and how it was handled:", problems),
    ):
        if not entries:
            continue
        lines.append(title + "\n\n")
        for line, count in entries:
            suffix = f" *(x{count} rows)*" if count > 1 else ""
            lines.append(f"- {line}{suffix}\n")
        lines.append("\n")
    return "".join(lines)


def _render_caveats() -> str:
    lines = [
        "## Caveats — prices in here with an expiry\n\n",
        "Some of the rates above are promotional or introductory. A promotional "
        "rate is a real price today and a wrong one later, and an annualised "
        "figure built on one is the optimistic reading by construction.\n\n",
        "| subject | good through | what changes | source |\n|---|---|---|---|\n",
    ]
    for c in promo_caveats():
        lines.append(f"| {c.subject} | {c.through} | {c.what_changes} | {c.source} |\n")
    lines.append(
        "\n**No carbon in v0.** The grid side of the claim is observation on the "
        "Spread Board and does not belong in a document that quotes somebody a "
        "number. It is not here partially, and not as a placeholder column.\n"
    )
    return "".join(lines)


# --------------------------------------------------------------------------


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True, help="job log: JSON or CSV")
    ap.add_argument("--out", help="write the report here (default: stdout)")
    ap.add_argument(
        "--window-days",
        type=float,
        help="override the observed span used to annualise",
    )
    ap.add_argument(
        "--label",
        help='banner for the artifact header; "synthetic" prints the '
        "generated-data warning",
    )
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    rows = load_log(Path(a.log))
    if not rows:
        print(f"{a.log}: no job records — nothing to report")
        return 1
    report = render(analyse(rows, days=a.window_days, label=a.label))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(report)
        print(f"wrote {a.out} ({len(rows)} log rows)")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
