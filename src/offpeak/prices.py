"""List-price sheet and batch discounts, for receipts.

Receipts are arithmetic against public price sheets — no estimates. The prices
below are a **bundled snapshot** (see ``PRICE_SHEET_DATE``); providers change
prices, so verify against their published sheets and override at runtime with
:func:`register_price` where they have moved. Costs for unknown models resolve
to ``None`` rather than a guess.

Batch tiers at OpenAI, Anthropic, Google, Groq and Mistral are publicly priced
at 50% of list, which is what :data:`BATCH_DISCOUNT` encodes. OpenAI's flex tier prices
identically to its batch tier on the gpt-5.6 family, and its *fast* tier at
twice list — the same model, priced for urgency. Fast is stored rather than
derived (:func:`get_fast_price`), because unlike batch it is not a discount
rule but its own published row; :func:`urgency_spread` divides the two so the
price of an hour is a computed number and not a claim in prose.

Some list prices are promotional and will step up on a published date. Those
carry a :class:`PromoNote` in :data:`PROMO_NOTES` — the date and the post-promo
list — so a quote or a docs page can flag the decay instead of reading a
temporary number as permanent.

Corrections
-----------

**2026-08-23** — the Mistral and Google blocks were read off
mistral.ai/pricing/api and ai.google.dev/pricing on this date, and the snapshot
date moved with them. The Anthropic, OpenAI and Groq blocks
are carried forward unchanged from the 2026-08-21 reading; the date on the sheet
is when it was last *touched*, not a claim that every row was re-verified.

**2026-08-21** — the OpenAI block through 0.2.0 held that provider's *batch*
sheet in the standard-price table (gpt-5.6-sol 2.50/15.00, terra 1.00/6.00,
luna 0.10/0.60). The published short-context standard rates are 4.00/20.00,
2.00/12.00 and 0.20/1.20; the batch rows are 2.00/10.00, 1.00/6.00 and
0.10/0.60. Receipts for OpenAI models in 0.1.1–0.2.0 therefore understated both
the list cost they compared against and the batch price actually billed — the
wrong sheet derived $1.25/$7.50 for a batched sol job against a true $2.00 /
$10.00. Anthropic's block was unaffected.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

__all__ = [
    "PRICE_SHEET_DATE",
    "BATCH_DISCOUNT",
    "PROMO_NOTES",
    "PromoNote",
    "format_usd",
    "register_price",
    "get_price",
    "get_fast_price",
    "get_promo_note",
    "promo_decay",
    "list_cost_usd",
    "batch_cost_usd",
    "fast_cost_usd",
    "urgency_spread",
    "SHEET_SCHEMA",
    "SheetLoad",
    "sheet_date",
    "export_sheet",
    "load_sheet",
    "reset_sheet",
]

#: The date of the sheet **bundled in this release**. It never moves at runtime:
#: a receipt that names it must stay checkable against the same numbers later.
PRICE_SHEET_DATE = "2026-08-23"

#: The date of the sheet currently in force. Equal to :data:`PRICE_SHEET_DATE`
#: until :func:`load_sheet` replaces it. Read it through :func:`sheet_date` —
#: modules that did ``from .prices import PRICE_SHEET_DATE`` bound a copy of the
#: string at import and would otherwise print a stale date beside fresh prices.
_SHEET_DATE = PRICE_SHEET_DATE
_SHEET_SOURCE = "bundled"

# Fraction of list price paid on provider batch tiers (published: 50%).
BATCH_DISCOUNT = 0.5

# model -> (USD per 1M input tokens, USD per 1M output tokens), standard list.
# Only models currently on a public price sheet appear here — receipts must be
# checkable against a published number. Models off the sheet (e.g. the gpt-5.1
# family, still callable but no longer listed) resolve to None; use
# :func:`register_price` if you run one at a privately known rate.
_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic (per platform.claude.com/docs/en/about-claude/pricing)
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-5": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI (per developers.openai.com/api/docs/pricing; standard tier,
    # ≤272k-token context). Long context is 2x input / 1.5x output; the batch
    # and flex tiers are both 50% of these. gpt-5.6-sol is promotional — see
    # PROMO_NOTES.
    "gpt-5.6-sol": (4.00, 20.00),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    # Groq (per groq.com/pricing), on-demand standard rates for the open-weight
    # gpt-oss models it serves. Groq's batch tier is the same 50%-of-standard
    # rule the other venues publish, so BATCH_DISCOUNT covers it and there is no
    # separate batch row. Groq publishes no fast tier, so no _FAST_PRICES entry:
    # an urgency spread it does not sell is not one this sheet should imply.
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
    # Mistral (per mistral.ai/pricing/api, read 2026-08-23). Keyed on the family
    # prefix rather than the SKU: _lookup takes the longest registered prefix, so
    # "mistral-medium" covers -latest, -2505, -2508, -2604, -3 and -3.5 without a
    # row each, and keeps covering the next date-pinned release.
    #
    # Batch is 50% of standard, the same rule the other venues publish, so
    # BATCH_DISCOUNT covers it. No fast tier: Mistral does not sell one.
    #
    # Deliberately absent, because they are not on the published table: the
    # magistral, devstral, mistral-code, mistral-vibe and labs-leanstral
    # families. They resolve to None, which is the correct answer for a rate
    # nobody published.
    "mistral-medium": (1.50, 7.50),
    "mistral-large": (0.50, 1.50),
    "mistral-small": (0.15, 0.60),
    "ministral-3b": (0.10, 0.10),
    "ministral-8b": (0.15, 0.15),
    "ministral-14b": (0.20, 0.20),
    "codestral": (0.30, 0.90),
    # Served by Mistral, priced on the same sheet, under both of its ids.
    "glm-5-2": (1.40, 4.40),
    "zai-glm-5-2": (1.40, 4.40),
    # Embeddings bill input only — the sheet lists no output rate, and zero is
    # the rate rather than a guess at one.
    "mistral-embed": (0.10, 0.00),
    # Google (per ai.google.dev/pricing, read 2026-08-23; paid tier). Batch is
    # 50% of standard, so BATCH_DISCOUNT covers it. No fast tier.
    #
    # 3.7 and 3.6 Flash are introductory — see PROMO_NOTES for the step-up.
    #
    # ``gemini-3.5-flash`` is a prefix of ``gemini-3.5-flash-lite``; both are
    # registered and _lookup takes the longest match, so the lite model keeps
    # its own rate rather than inheriting the larger one.
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    # Short-context rate. Prompts over 200k tokens are $4.00 / $18.00; the sheet
    # has no long-context dimension, so the shorter rate is the one stored and
    # a long-context run under-states. Same convention as the OpenAI block.
    "gemini-3.1-pro-preview": (2.00, 12.00),
    #
    # ``gemini-3.1-flash-lite`` is deliberately absent. Its text rate is
    # published ($0.25 / $1.50) but the id is a prefix of
    # ``gemini-3.1-flash-lite-image``, whose rate is not on the text sheet —
    # registering the family would silently price an image model at text rates.
    # Under-covering resolves to None, which is correct; over-covering invents a
    # number. Add it the day the image rate is on the sheet too.
}

# model -> (input, output) USD per 1M on a venue's *fast* tier: the same model,
# priced for urgency instead of patience. Only OpenAI publishes one today, for
# the gpt-5.6 family, at 2x standard (developers.openai.com/api/docs/pricing).
#
# This table is what makes the urgency spread a computed number rather than a
# claim in prose: fast over batch on the same model is what a venue charges for
# the hour, with the model held constant.
_FAST_PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.6-sol": (8.00, 40.00),
    "gpt-5.6-terra": (4.00, 24.00),
    "gpt-5.6-luna": (0.40, 2.40),
}


@dataclass(frozen=True)
class PromoNote:
    """A list price that is promotional, and what it decays to.

    A promotional rate is a real price today and a wrong one later. Carrying the
    step-up here keeps the sheet honest in both directions: receipts settle at
    the price actually charged, while a quote or a docs page can say — from data
    rather than prose — that the number has an expiry and what replaces it.
    """

    #: The date the provider guarantees the promo through (ISO 8601). It may run
    #: longer: the sheet says "at least through" this date, never "until".
    through: str
    #: (input, output) USD per 1M tokens once the promo lapses.
    post_promo: tuple[float, float]
    #: Where the claim is checkable.
    source: str
    #: The provider's own wording, verbatim.
    note: str


# model -> PromoNote. Prefix-matched the same way prices are, so a date-pinned
# model name inherits its family's note.
PROMO_NOTES: dict[str, PromoNote] = {
    "gemini-3.7-flash": PromoNote(
        through="2026-12-31",
        post_promo=(1.50, 7.50),
        source="ai.google.dev/pricing",
        note=(
            "Gemini 3.7 Flash is $0.75 input / $3.75 output through "
            "December 31, 2026, and $1.50 / $7.50 starting January 1, 2027."
        ),
    ),
    "gemini-3.6-flash": PromoNote(
        through="2026-12-31",
        post_promo=(1.50, 7.50),
        source="ai.google.dev/pricing",
        note=(
            "Gemini 3.6 Flash is $0.75 input / $3.75 output through "
            "December 31, 2026, and $1.50 / $7.50 starting January 1, 2027."
        ),
    ),
    "gpt-5.6-sol": PromoNote(
        through="2026-11-21",
        post_promo=(5.00, 30.00),
        source="developers.openai.com/api/docs/pricing",
        note=(
            "GPT-5.6 Sol's promotional pricing is available at least through "
            "November 21, 2026."
        ),
    ),
}


#: Wire format of a published sheet. Bumped only for a breaking change, and
#: :func:`load_sheet` refuses a major version it does not know — a sheet it
#: half-understands would price jobs against numbers it guessed at.
SHEET_SCHEMA = "offpeak.price-sheet/1"

# Captured at import so reset_sheet() can put the release's own numbers back
# after a published sheet has been loaded over them.
_BUNDLED_PRICES = dict(_PRICES)
_BUNDLED_FAST = dict(_FAST_PRICES)
_BUNDLED_PROMO = dict(PROMO_NOTES)


@dataclass(frozen=True)
class SheetLoad:
    """What :func:`load_sheet` did — reported, never assumed."""

    sheet_date: str
    source: str
    models: int
    added: int
    changed: int
    unchanged: int
    fast_models: int
    promo_notes: int

    def __str__(self) -> str:
        return (
            f"loaded price sheet {self.sheet_date} from {self.source}: "
            f"{self.models} model(s) — {self.added} new, {self.changed} changed, "
            f"{self.unchanged} unchanged; {self.fast_models} fast row(s), "
            f"{self.promo_notes} promo note(s)"
        )


def sheet_date() -> str:
    """The date of the sheet currently in force.

    :data:`PRICE_SHEET_DATE` is the sheet this *release* bundles and never
    moves. This is what is actually pricing jobs right now, which is the figure
    a quote or a receipt should print.
    """
    return _SHEET_DATE


def export_sheet() -> dict:
    """The sheet in force, as the published wire format.

    This is the whole publishing story: the sheet is data, so it serializes.
    No service, no database — a dated JSON file that anyone can fetch, diff,
    pin, or check against the provider pages named in ``sources``.
    """
    return {
        "schema": SHEET_SCHEMA,
        "sheet_date": _SHEET_DATE,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch_discount": BATCH_DISCOUNT,
        "prices": {
            model: {"input_per_m": rates[0], "output_per_m": rates[1]}
            for model, rates in sorted(_PRICES.items())
        },
        "fast_prices": {
            model: {"input_per_m": rates[0], "output_per_m": rates[1]}
            for model, rates in sorted(_FAST_PRICES.items())
        },
        "promo_notes": {
            model: {
                "through": note.through,
                "post_promo": list(note.post_promo),
                "source": note.source,
                "note": note.note,
            }
            for model, note in sorted(PROMO_NOTES.items())
        },
    }


def _read_source(source: str | Path) -> tuple[dict, str]:
    """Fetch *source* as JSON. Returns the document and a printable origin."""
    text = str(source)
    if text.startswith("http://"):
        raise ValueError(
            "refusing to load a price sheet over plain http — a sheet that "
            "settles real bills must not be modifiable in transit"
        )
    if text.startswith("https://"):
        request = Request(text, headers={"User-Agent": "offpeak/price-sheet"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 — https enforced above
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset)), text
    path = Path(source)
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def load_sheet(source: str | Path | dict, *, replace: bool = False) -> SheetLoad:
    """Load a published price sheet over the bundled one. **Opt in, always.**

    *source* is an ``https://`` URL, a filesystem path, or an already-parsed
    dict. Nothing in the library calls this for you: the default sheet is the
    one this release shipped with, so ``offpeak`` keeps working offline and a
    receipt settled today can still be checked next year against the numbers
    that settled it.

    ``replace=True`` clears the table first, so the loaded sheet is the whole
    truth and a model it omits resolves to ``None``. The default merges, which
    keeps any :func:`register_price` overrides and older models you still run.

    A sheet declaring a different ``batch_discount`` than this release is
    **refused** rather than applied. The discount is a rule the venues publish
    identically, not a row — and ``client`` and ``quote`` bound their copy of it
    at import, so honouring it here would price some arithmetic at the new rate
    and some at the old. That is a release, not a download.
    """
    global _SHEET_DATE, _SHEET_SOURCE

    if isinstance(source, dict):
        document, origin = source, "<dict>"
    else:
        document, origin = _read_source(source)

    schema = str(document.get("schema", ""))
    family, _, major = schema.partition("/")
    if family != SHEET_SCHEMA.partition("/")[0] or major != SHEET_SCHEMA.rpartition("/")[2]:
        raise ValueError(
            f"unsupported price-sheet schema {schema!r}; this build reads {SHEET_SCHEMA}"
        )

    date = document.get("sheet_date")
    if not date:
        raise ValueError("price sheet has no sheet_date — a sheet with no date is not checkable")

    declared = document.get("batch_discount", BATCH_DISCOUNT)
    if float(declared) != BATCH_DISCOUNT:
        raise ValueError(
            f"price sheet declares batch_discount {declared}, this build applies "
            f"{BATCH_DISCOUNT}. The discount is a published rule rather than a row; "
            "upgrade offpeak rather than loading a sheet that disagrees with it"
        )

    rows = document.get("prices") or {}
    if not rows:
        raise ValueError("price sheet carries no prices")

    parsed: dict[str, tuple[float, float]] = {}
    for model, rates in rows.items():
        try:
            parsed[str(model)] = (float(rates["input_per_m"]), float(rates["output_per_m"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"price sheet row {model!r} is not a pair of rates: {exc}") from None

    before = dict(_PRICES)
    added = sum(1 for m in parsed if m not in before)
    changed = sum(1 for m, r in parsed.items() if m in before and before[m] != r)
    unchanged = sum(1 for m, r in parsed.items() if m in before and before[m] == r)

    if replace:
        _PRICES.clear()
    _PRICES.update(parsed)

    fast = document.get("fast_prices") or {}
    if replace:
        _FAST_PRICES.clear()
    for model, rates in fast.items():
        _FAST_PRICES[str(model)] = (float(rates["input_per_m"]), float(rates["output_per_m"]))

    notes = document.get("promo_notes") or {}
    if replace:
        PROMO_NOTES.clear()
    for model, note in notes.items():
        PROMO_NOTES[str(model)] = PromoNote(
            through=str(note["through"]),
            post_promo=(float(note["post_promo"][0]), float(note["post_promo"][1])),
            source=str(note.get("source", "")),
            note=str(note.get("note", "")),
        )

    _SHEET_DATE = str(date)
    _SHEET_SOURCE = origin
    return SheetLoad(
        sheet_date=_SHEET_DATE,
        source=origin,
        models=len(parsed),
        added=added,
        changed=changed,
        unchanged=unchanged,
        fast_models=len(fast),
        promo_notes=len(notes),
    )


def reset_sheet() -> str:
    """Put the release's own bundled sheet back. Returns its date."""
    global _SHEET_DATE, _SHEET_SOURCE
    _PRICES.clear()
    _PRICES.update(_BUNDLED_PRICES)
    _FAST_PRICES.clear()
    _FAST_PRICES.update(_BUNDLED_FAST)
    PROMO_NOTES.clear()
    PROMO_NOTES.update(_BUNDLED_PROMO)
    _SHEET_DATE = PRICE_SHEET_DATE
    _SHEET_SOURCE = "bundled"
    return _SHEET_DATE


def register_price(model: str, input_per_m: float, output_per_m: float) -> None:
    """Set or override the list price for *model* (USD per 1M tokens)."""
    _PRICES[model] = (float(input_per_m), float(output_per_m))


def _lookup(table: dict, model: str):
    """Exact match first, then longest registered prefix (handles date-pinned
    model names like ``claude-sonnet-4-5-20250929``)."""
    if model in table:
        return table[model]
    best = None
    for name, value in table.items():
        if model.startswith(name) and (best is None or len(name) > best[0]):
            best = (len(name), value)
    return best[1] if best else None


def get_price(model: str) -> tuple[float, float] | None:
    """Standard (synchronous) list price for *model*, USD per 1M tokens."""
    return _lookup(_PRICES, model)


def get_fast_price(model: str) -> tuple[float, float] | None:
    """Fast-tier price for *model*, USD per 1M tokens.

    ``None`` where the venue publishes no fast tier — which is everywhere
    except OpenAI's gpt-5.6 family today. Unlike batch, fast is not a discount
    rule applied to list: it is its own published row, so it is stored, not
    derived.
    """
    return _lookup(_FAST_PRICES, model)


def get_promo_note(model: str) -> PromoNote | None:
    """The :class:`PromoNote` for *model*, if its list price is promotional.

    ``None`` means "no published promotion", which is also what a model
    registered at runtime with :func:`register_price` returns — an override is
    a price we were told, not a price we can date.
    """
    return _lookup(PROMO_NOTES, model)


def promo_decay(model: str) -> tuple[float, float] | None:
    """Multiple the (input, output) price steps up by when the promo lapses.

    ``(1.25, 1.5)`` on gpt-5.6-sol: $4/$20 today, $5/$30 after. ``None`` where
    the price is not promotional or the model is off the sheet.
    """
    note = get_promo_note(model)
    price = get_price(model)
    if note is None or price is None or not price[0] or not price[1]:
        return None
    return (note.post_promo[0] / price[0], note.post_promo[1] / price[1])


def list_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = get_price(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def batch_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    cost = list_cost_usd(model, input_tokens, output_tokens)
    return None if cost is None else cost * BATCH_DISCOUNT


def fast_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """What the same tokens cost on the venue's fast tier, where it has one."""
    price = get_fast_price(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def urgency_spread(model: str) -> float | None:
    """How much the same model costs at its most urgent published tier over its
    most patient one: fast ÷ batch.

    This is the intra-venue price of an hour with the model held constant — one
    provider, one model, two deadlines. On gpt-5.6-sol that is $8/$40 per 1M
    against $2/$10, a **4x** spread.

    Both legs are checked and the *lower* is returned, so the figure can never
    overstate what a venue publishes. ``None`` where the venue prices no fast
    tier for the model, or the model is off the sheet.
    """
    fast = get_fast_price(model)
    standard = get_price(model)
    if fast is None or standard is None:
        return None
    legs = [
        fast[i] / (standard[i] * BATCH_DISCOUNT)
        for i in (0, 1)
        if standard[i] * BATCH_DISCOUNT
    ]
    return min(legs) if legs else None


def format_usd(amount: float | None) -> str:
    """Money for humans: 2dp once there are cents to show, more significant
    digits below that so a sub-cent job does not settle as a column of $0.00.

    ``None`` (an unpriced model) renders as an em dash, never as zero — a price
    we do not know is not a price of nothing.
    """
    if amount is None:
        return "—"
    if amount == 0:
        return "0.00"
    if abs(amount) >= 0.005:
        return f"{amount:,.2f}"
    return f"{amount:,.{-math.floor(math.log10(abs(amount))) + 2}f}"
