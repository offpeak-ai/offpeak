"""The price sheet itself — the numbers receipts are arithmetic against.

Every figure here is a transcription of a published sheet, so these tests read
like a proofreading pass: they assert the bundled numbers against the rows a
human can go and check.
"""

import pytest

from offpeak import prices
from offpeak.prices import (
    BATCH_DISCOUNT,
    batch_cost_usd,
    get_price,
    get_promo_note,
    list_cost_usd,
    promo_decay,
    register_price,
)

# developers.openai.com/api/docs/pricing, short-context rows, USD per 1M tokens.
# The standard tab is what the sheet calls list; the batch tab is what a batched
# job is actually billed at. 0.1.1-0.2.0 stored the second as the first.
OPENAI_STANDARD = {
    "gpt-5.6-sol": (4.00, 20.00),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
}
OPENAI_BATCH = {
    "gpt-5.6-sol": (2.00, 10.00),
    "gpt-5.6-terra": (1.00, 6.00),
    "gpt-5.6-luna": (0.10, 0.60),
}


class TestOpenAISheet:
    @pytest.mark.parametrize("model,published", sorted(OPENAI_STANDARD.items()))
    def test_standard_rows_are_the_published_standard_rows(self, model, published):
        assert get_price(model) == published

    @pytest.mark.parametrize("model,published", sorted(OPENAI_BATCH.items()))
    def test_the_derived_batch_price_equals_the_published_batch_row(self, model, published):
        # The regression that matters: BATCH_DISCOUNT is a rule, not a guess.
        # If it ever stops reproducing the provider's own batch tab, either the
        # rule or the sheet has moved and receipts are wrong.
        derived_in = get_price(model)[0] * BATCH_DISCOUNT
        derived_out = get_price(model)[1] * BATCH_DISCOUNT
        assert (derived_in, derived_out) == pytest.approx(published)

    @pytest.mark.parametrize("model,published", sorted(OPENAI_BATCH.items()))
    def test_a_batched_million_tokens_bills_the_published_batch_row(self, model, published):
        assert batch_cost_usd(model, 1_000_000, 0) == pytest.approx(published[0])
        assert batch_cost_usd(model, 0, 1_000_000) == pytest.approx(published[1])

    def test_a_sol_job_is_no_longer_settled_off_the_batch_sheet(self):
        # The 0.2.0 bug, stated as a number: a 1M/1M sol job settled $1.25 in
        # and $7.50 out against a true batch price of $2.00 and $10.00.
        assert batch_cost_usd("gpt-5.6-sol", 1_000_000, 1_000_000) == pytest.approx(12.00)
        assert list_cost_usd("gpt-5.6-sol", 1_000_000, 1_000_000) == pytest.approx(24.00)


class TestPromoNotes:
    def test_sol_carries_its_promo_and_the_price_it_decays_to(self):
        note = get_promo_note("gpt-5.6-sol")
        assert note.through == "2026-11-21"
        assert note.post_promo == (5.00, 30.00)
        assert "openai.com" in note.source
        assert "November 21, 2026" in note.note

    def test_the_decay_is_computable_not_prose(self):
        assert promo_decay("gpt-5.6-sol") == pytest.approx((1.25, 1.5))

    def test_a_date_pinned_model_inherits_its_family_note(self):
        assert get_promo_note("gpt-5.6-sol-2026-08-01") is get_promo_note("gpt-5.6-sol")

    def test_a_non_promotional_model_has_no_note_and_no_decay(self):
        assert get_promo_note("gpt-5.6-luna") is None
        assert promo_decay("gpt-5.6-luna") is None
        assert get_promo_note("claude-haiku-4-5") is None

    def test_an_unknown_model_has_no_note(self):
        assert get_promo_note("mistral-large") is None
        assert promo_decay("mistral-large") is None

    def test_a_runtime_override_is_a_price_we_were_told_not_one_we_can_date(self):
        register_price("my-fine-tune", 4.0, 16.0)
        assert get_price("my-fine-tune") == (4.0, 16.0)
        assert get_promo_note("my-fine-tune") is None


class TestSheetHygiene:
    def test_the_sheet_is_dated(self):
        assert prices.PRICE_SHEET_DATE.startswith("2026-")

    def test_every_promo_note_names_a_model_that_is_on_the_sheet(self):
        for model in prices.PROMO_NOTES:
            assert get_price(model) is not None, model

    def test_every_promo_is_a_discount_on_the_price_it_decays_to(self):
        for model, note in prices.PROMO_NOTES.items():
            price = get_price(model)
            assert price[0] < note.post_promo[0] and price[1] < note.post_promo[1], model


# The fast tab of the same sheet: the same models, priced for urgency.
OPENAI_FAST = {
    "gpt-5.6-sol": (8.00, 40.00),
    "gpt-5.6-terra": (4.00, 24.00),
    "gpt-5.6-luna": (0.40, 2.40),
}


class TestFastTier:
    @pytest.mark.parametrize("model,published", sorted(OPENAI_FAST.items()))
    def test_fast_rows_are_the_published_fast_rows(self, model, published):
        assert prices.get_fast_price(model) == published

    @pytest.mark.parametrize("model,published", sorted(OPENAI_FAST.items()))
    def test_fast_is_twice_standard(self, model, published):
        standard = get_price(model)
        assert published == pytest.approx((standard[0] * 2, standard[1] * 2))

    @pytest.mark.parametrize("model", sorted(OPENAI_FAST))
    def test_the_urgency_spread_is_four_times_across_the_family(self, model):
        # The citable claim, computed rather than asserted in prose: fast over
        # batch on one model at one venue. $8/$40 against $2/$10 on sol.
        assert prices.urgency_spread(model) == pytest.approx(4.0)

    def test_the_spread_is_the_conservative_leg(self, monkeypatch):
        # A sheet whose two legs disagree must report the smaller ratio: a
        # published spread should never be flattered by the arithmetic.
        monkeypatch.setitem(prices._FAST_PRICES, "gpt-5.6-luna", (0.40, 1.20))
        assert prices.urgency_spread("gpt-5.6-luna") == pytest.approx(2.0)

    def test_a_venue_with_no_fast_tier_has_no_spread(self):
        assert prices.get_fast_price("claude-haiku-4-5") is None
        assert prices.urgency_spread("claude-haiku-4-5") is None
        assert prices.fast_cost_usd("claude-haiku-4-5", 1000, 1000) is None

    def test_an_unknown_model_has_no_fast_price(self):
        assert prices.get_fast_price("mistral-large") is None
        assert prices.urgency_spread("mistral-large") is None

    def test_a_date_pinned_model_inherits_its_family_fast_row(self):
        assert prices.get_fast_price("gpt-5.6-sol-2026-08-01") == (8.00, 40.00)

    def test_a_million_tokens_bills_the_published_fast_row(self):
        assert prices.fast_cost_usd("gpt-5.6-sol", 1_000_000, 0) == pytest.approx(8.00)
        assert prices.fast_cost_usd("gpt-5.6-sol", 0, 1_000_000) == pytest.approx(40.00)

    def test_fast_and_batch_bracket_the_same_job(self):
        args = ("gpt-5.6-sol", 1_000_000, 1_000_000)
        assert prices.fast_cost_usd(*args) == pytest.approx(48.00)
        assert list_cost_usd(*args) == pytest.approx(24.00)
        assert batch_cost_usd(*args) == pytest.approx(12.00)
