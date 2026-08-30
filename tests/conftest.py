"""Shared fixtures.

The price sheet is module-global mutable state, so a test that calls
``register_price`` or ``load_sheet`` leaks into every test that runs after it —
which makes outcomes depend on file and test ordering. Restore the whole sheet
around every test so a change stays local to the test that asked for it.

"The whole sheet" is five things, not one: the price table, the fast-tier table,
the promo notes, the lane table, and the date in force. ``load_sheet`` moves all four, and a
fixture that put back only the prices would leave a later test quoting today's
rates under a downloaded sheet's date.
"""

import pytest

from offpeak import prices


@pytest.fixture(autouse=True)
def restore_price_sheet():
    snapshot = dict(prices._PRICES)
    fast = dict(prices._FAST_PRICES)
    promo = dict(prices.PROMO_NOTES)
    lanes = dict(prices._LANES)
    date = prices.sheet_date()
    source = prices._SHEET_SOURCE
    yield
    prices._PRICES.clear()
    prices._PRICES.update(snapshot)
    prices._FAST_PRICES.clear()
    prices._FAST_PRICES.update(fast)
    prices.PROMO_NOTES.clear()
    prices.PROMO_NOTES.update(promo)
    prices._LANES.clear()
    prices._LANES.update(lanes)
    prices._SHEET_DATE = date
    prices._SHEET_SOURCE = source
