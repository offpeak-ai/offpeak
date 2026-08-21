"""Shared fixtures.

``offpeak.prices._PRICES`` is module-global mutable state, so a test that calls
``register_price`` leaks that model into every test that runs after it — which
makes outcomes depend on file and test ordering. Restore the sheet around every
test so registration stays local to the test that asked for it.
"""

import pytest

from offpeak import prices


@pytest.fixture(autouse=True)
def restore_price_sheet():
    snapshot = dict(prices._PRICES)
    yield
    prices._PRICES.clear()
    prices._PRICES.update(snapshot)
