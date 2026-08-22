"""List-price sheet and batch discounts, for receipts.

Receipts are arithmetic against public price sheets — no estimates. The prices
below are a **bundled snapshot** (see ``PRICE_SHEET_DATE``); providers change
prices, so verify against their published sheets and override at runtime with
:func:`register_price` where they have moved. Costs for unknown models resolve
to ``None`` rather than a guess.

Batch tiers at OpenAI, Anthropic, and Google are publicly priced at 50% of
list, which is what :data:`BATCH_DISCOUNT` encodes. OpenAI's flex tier prices
identically to its batch tier on the gpt-5.6 family.

Some list prices are promotional and will step up on a published date. Those
carry a :class:`PromoNote` in :data:`PROMO_NOTES` — the date and the post-promo
list — so a quote or a docs page can flag the decay instead of reading a
temporary number as permanent.

Corrections
-----------

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

import math
from dataclasses import dataclass

__all__ = [
    "PRICE_SHEET_DATE",
    "BATCH_DISCOUNT",
    "PROMO_NOTES",
    "PromoNote",
    "format_usd",
    "register_price",
    "get_price",
    "get_promo_note",
    "promo_decay",
    "list_cost_usd",
    "batch_cost_usd",
]

PRICE_SHEET_DATE = "2026-08-21"

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
