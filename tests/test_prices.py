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


class TestMistralSheet:
    def test_the_family_prefix_covers_the_date_pinned_skus(self):
        # _lookup takes the longest registered prefix, so one row per family
        # keeps covering the next dated release without a code change.
        for sku in ("mistral-medium-latest", "mistral-medium-2604", "mistral-medium-3.5"):
            assert prices.get_price(sku) == (1.50, 7.50), sku
        assert prices.get_price("mistral-large-2512") == (0.50, 1.50)
        assert prices.get_price("mistral-small-2603") == (0.15, 0.60)
        assert prices.get_price("codestral-latest") == (0.30, 0.90)

    def test_the_ministral_sizes_do_not_collide(self):
        assert prices.get_price("ministral-3b-latest") == (0.10, 0.10)
        assert prices.get_price("ministral-8b-latest") == (0.15, 0.15)
        assert prices.get_price("ministral-14b-latest") == (0.20, 0.20)

    def test_glm_is_priced_under_both_ids_mistral_serves_it_as(self):
        assert prices.get_price("glm-5-2") == (1.40, 4.40)
        assert prices.get_price("zai-glm-5-2") == (1.40, 4.40)

    def test_batch_is_the_standard_half(self):
        listed = prices.list_cost_usd("mistral-large-latest", 1_000_000, 1_000_000)
        assert prices.batch_cost_usd("mistral-large-latest", 1_000_000, 1_000_000) == listed * 0.5

    def test_mistral_publishes_no_fast_tier_so_none_is_implied(self):
        assert prices.get_fast_price("mistral-large-latest") is None
        assert prices.urgency_spread("mistral-large-latest") is None

    def test_families_off_the_published_table_stay_unpriced(self):
        # magistral, devstral, mistral-code, mistral-vibe and labs-leanstral are
        # served by the API and are not on the price sheet. None is the correct
        # answer for a rate nobody published — never zero, never a guess.
        for model in (
            "magistral-medium-latest",
            "devstral-latest",
            "mistral-code-latest",
            "mistral-vibe-cli-latest",
            "labs-leanstral-1-5",
        ):
            assert prices.get_price(model) is None, model

    def test_mistral_code_is_not_swallowed_by_the_codestral_prefix(self):
        # "codestral" and "mistral-code-*" are different products; a sloppy
        # prefix would price one at the other's rate.
        assert prices.get_price("mistral-code-latest") is None
        assert prices.get_price("codestral-2508") == (0.30, 0.90)


class TestGoogleSheet:
    def test_the_flash_rates_are_the_introductory_ones(self):
        assert prices.get_price("gemini-3.7-flash") == (0.75, 3.75)
        assert prices.get_price("gemini-3.6-flash") == (0.75, 3.75)

    def test_the_flash_promo_carries_its_step_up(self):
        for model in ("gemini-3.7-flash", "gemini-3.6-flash"):
            note = prices.get_promo_note(model)
            assert note is not None and note.through == "2026-12-31", model
            assert note.post_promo == (1.50, 7.50)
            # The dollars double on both legs; the batch ratio does not move.
            assert prices.promo_decay(model) == (2.0, 2.0)

    def test_flash_lite_keeps_its_own_rate_under_the_flash_prefix(self):
        # "gemini-3.5-flash" is a prefix of "gemini-3.5-flash-lite"; _lookup
        # takes the longest match, so the lite model must not inherit the
        # larger model's rate.
        assert prices.get_price("gemini-3.5-flash") == (1.50, 9.00)
        assert prices.get_price("gemini-3.5-flash-lite") == (0.30, 2.50)

    def test_the_pro_preview_prices_with_its_tools_variant(self):
        assert prices.get_price("gemini-3.1-pro-preview") == (2.00, 12.00)
        assert prices.get_price("gemini-3.1-pro-preview-customtools") == (2.00, 12.00)

    def test_the_image_variants_are_not_priced_at_text_rates(self):
        # gemini-3.1-flash-lite has a published *text* rate, but its id is a
        # prefix of gemini-3.1-flash-lite-image, whose rate is not on the text
        # sheet. Registering the family would silently price an image model as
        # text, so the family is left off and both resolve to None.
        assert prices.get_price("gemini-3.1-flash-lite") is None
        assert prices.get_price("gemini-3.1-flash-lite-image") is None
        assert prices.get_price("gemini-3-pro-image") is None

    def test_batch_is_the_standard_half(self):
        listed = prices.list_cost_usd("gemini-3.7-flash", 1_000_000, 1_000_000)
        batched = prices.batch_cost_usd("gemini-3.7-flash", 1_000_000, 1_000_000)
        assert batched == listed * 0.5

    def test_google_publishes_no_fast_tier_so_none_is_implied(self):
        assert prices.get_fast_price("gemini-3.7-flash") is None
        assert prices.urgency_spread("gemini-3.7-flash") is None
