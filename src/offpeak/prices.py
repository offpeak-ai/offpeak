"""List-price sheet and batch discounts, for receipts.

Receipts are arithmetic against public price sheets — no estimates. The prices
below are a **bundled snapshot** (see ``PRICE_SHEET_DATE``); providers change
prices, so verify against their published sheets and override at runtime with
:func:`register_price` where they have moved. Costs for unknown models resolve
to ``None`` rather than a guess.

Batch tiers at OpenAI, Anthropic, and Google are publicly priced at 50% of
list, which is what :data:`BATCH_DISCOUNT` encodes.
"""

from __future__ import annotations

import math

__all__ = [
    "PRICE_SHEET_DATE",
    "BATCH_DISCOUNT",
    "format_usd",
    "register_price",
    "get_price",
    "list_cost_usd",
    "batch_cost_usd",
]

PRICE_SHEET_DATE = "2026-08"

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
    # OpenAI (per developers.openai.com/api/docs/pricing; ≤272k-token context)
    "gpt-5.6-sol": (2.50, 15.00),
    "gpt-5.6-terra": (1.00, 6.00),
    "gpt-5.6-luna": (0.10, 0.60),
}


def register_price(model: str, input_per_m: float, output_per_m: float) -> None:
    """Set or override the list price for *model* (USD per 1M tokens)."""
    _PRICES[model] = (float(input_per_m), float(output_per_m))


def get_price(model: str) -> tuple[float, float] | None:
    """Exact match first, then longest registered prefix (handles date-pinned
    model names like ``claude-sonnet-4-5-20250929``)."""
    if model in _PRICES:
        return _PRICES[model]
    best = None
    for name, price in _PRICES.items():
        if model.startswith(name) and (best is None or len(name) > best[0]):
            best = (len(name), price)
    return best[1] if best else None


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
