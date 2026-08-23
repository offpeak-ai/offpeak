"""Groq venue — network-free. The live proof is receipts/2026-08-23-groq-1.json."""

import pytest

import offpeak
from offpeak import job
from offpeak.venues.base import BatchState
from offpeak.venues.groq_batch import (
    COMPLETION_WINDOWS,
    MAX_INPUT_BYTES,
    MAX_JSONL_LINES,
    GroqBatch,
    window_for_seconds,
)


class FakeGroqClient:
    """Mimics the OpenAI-shaped surface Groq exposes."""

    def __init__(self, status="completed"):
        self.uploaded = None
        self.created = None
        self._status = status
        self.files = self._Files(self)
        self.batches = self._Batches(self)

    class _Files:
        def __init__(self, outer):
            self.outer = outer

        def create(self, file=None, purpose=None):
            self.outer.uploaded = {"file": file, "purpose": purpose}
            return type("Upload", (), {"id": "file_1"})()

        def content(self, file_id):
            return type("Content", (), {"text": self.outer.output})()

    class _Batches:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.created = kwargs
            return type("Batch", (), {"id": "batch_groq_1"})()

        def retrieve(self, handle):
            return type(
                "Batch",
                (),
                {
                    "status": self.outer._status,
                    "request_counts": type("C", (), {"completed": 1, "failed": 0, "total": 1})(),
                    "output_file_id": "out_1",
                    "error_file_id": None,
                },
            )()


class TestTermStructure:
    def test_reaches_for_the_longest_window_that_fits(self):
        # Groq recommends the longest window you can tolerate.
        assert window_for_seconds(7 * 24 * 3600) == "7d"
        assert window_for_seconds(50 * 3600) == "48h"
        assert window_for_seconds(24 * 3600) == "24h"

    def test_a_deadline_under_the_shortest_window_still_returns_one(self):
        # The batch may not land; that is what the sync fallback is for.
        assert window_for_seconds(60) == "24h"

    def test_a_very_long_deadline_is_capped_at_the_published_maximum(self):
        assert window_for_seconds(365 * 24 * 3600) == "7d"

    def test_every_published_window_is_selectable(self):
        for w in COMPLETION_WINDOWS:
            assert GroqBatch(client=object(), completion_window=w).completion_window == w

    def test_an_unpublished_window_is_rejected(self):
        with pytest.raises(ValueError, match="completion_window must be one of"):
            GroqBatch(client=object(), completion_window="30m")


class TestRouting:
    @pytest.mark.parametrize(
        "model",
        [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-safeguard-20b",
            "groq/compound",
            "groq/compound-mini",
        ],
    )
    def test_claims_the_production_chat_lineup(self, model):
        # Checked against GET /openai/v1/models on 2026-08-22. Before this,
        # supports() matched none of these: every prefix it carried named a
        # model Groq had already shut off, so the venue answered "no" to the
        # entire catalogue it exists to reach.
        assert GroqBatch(client=object()).supports(model)

    @pytest.mark.parametrize(
        "model",
        [
            "llama-3.3-70b-versatile",  # shut down 2026-08-16
            "llama-3.1-8b-instant",  # shut down 2026-08-16
            "qwen/qwen3-32b",  # shut down 2026-07-17
            "meta-llama/llama-4-scout",  # shut down 2026-07-17
            "mixtral-8x7b",
            "gemma2-9b",
            "deepseek-r1-distill-llama-70b",
            "moonshotai/kimi-k2",
            "allam-2-7b",
            "compound-beta",  # renamed groq/compound
            # Live on the models endpoint, but off the production chat lineup —
            # and a bare "qwen" prefix would claim the dead 32b with it.
            "qwen/qwen3.6-27b",
        ],
    )
    def test_does_not_claim_models_groq_retired_or_left_off_the_lineup(self, model):
        # Groq's batch docs page still lists the Llamas. It is stale; the
        # deprecations page is the truth. A venue that claims a dead model
        # routes work to a 404 instead of letting another venue have it.
        assert not GroqBatch(client=object()).supports(model)

    def test_does_not_claim_whisper(self):
        # build_jsonl writes /v1/chat/completions with a messages body.
        # Transcription needs /v1/audio/transcriptions with a file, so a
        # Whisper job here is a batch of 400s discovered hours later.
        assert not GroqBatch(client=object()).supports("whisper-large-v3")
        assert not GroqBatch(client=object()).supports("whisper-large-v3-turbo")

    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "claude-haiku-4-5"])
    def test_does_not_poach_models_another_venue_owns(self, model):
        # A job must not silently change provider because two venues answer to
        # the same name.
        assert not GroqBatch(client=object()).supports(model)

    def test_claims_gpt_oss_by_name_not_the_whole_openai_namespace(self):
        # "openai/gpt-oss" rather than a bare "openai/": the namespace is
        # Groq's catalogue, and claiming all of it would poach every future
        # OpenAI-authored model that lands there.
        assert not GroqBatch(client=object()).supports("openai/whisper-large-v3")
        assert not GroqBatch(client=object()).supports("openai/some-future-model")

    def test_is_not_in_default_venues(self):
        # Opt-in: Groq needs its own key and its own extra, and a model name
        # should not start costing money at a venue nobody asked for.
        assert "groq:batch" not in {v.name for v in offpeak.default_venues()}


class TestPublishedLimits:
    def test_too_many_requests_is_refused_before_the_upload(self):
        client = FakeGroqClient()
        jobs = [job("openai/gpt-oss-20b", "hi")] * (MAX_JSONL_LINES + 1)
        with pytest.raises(ValueError, match="at most 50,000 requests"):
            GroqBatch(client=client).submit(jobs)
        assert client.uploaded is None, "nothing should have crossed the wire"

    def test_an_oversized_file_is_refused_before_the_upload(self, monkeypatch):
        import offpeak.venues.groq_batch as gb

        monkeypatch.setattr(gb, "MAX_INPUT_BYTES", 32)
        client = FakeGroqClient()
        with pytest.raises(ValueError, match="capped at"):
            GroqBatch(client=client).submit([job("openai/gpt-oss-20b", "x" * 500)])
        assert client.uploaded is None

    def test_the_limits_are_groqs_published_ones(self):
        assert MAX_JSONL_LINES == 50_000
        assert MAX_INPUT_BYTES == 200 * 1024 * 1024

    def test_a_batch_inside_the_limits_goes_through(self):
        client = FakeGroqClient()
        handle = GroqBatch(client=client).submit([job("openai/gpt-oss-20b", "hi")])
        assert handle == "batch_groq_1"
        assert client.uploaded is not None


class TestSubmit:
    def test_submit_sends_the_chosen_window(self):
        client = FakeGroqClient()
        venue = GroqBatch(client=client, completion_window="7d")
        handle = venue.submit([job("openai/gpt-oss-20b", "hello")])
        assert handle == "batch_groq_1"
        assert client.created["completion_window"] == "7d"
        assert client.created["endpoint"] == "/v1/chat/completions"
        assert client.created["input_file_id"] == "file_1"
        assert client.uploaded["purpose"] == "batch"

    def test_request_payload_is_the_openai_jsonl_shape(self):
        import json

        client = FakeGroqClient()
        j = job("openai/gpt-oss-20b", "hello", temperature=0.1)
        GroqBatch(client=client).submit([j])
        line = json.loads(client.uploaded["file"][1].decode().strip())
        assert line["custom_id"] == j.id
        assert line["body"]["model"] == "openai/gpt-oss-20b"
        assert line["body"]["temperature"] == 0.1

    def test_status_maps_onto_the_shared_vocabulary(self):
        for raw, expected in [
            ("validating", "in_progress"),
            ("in_progress", "in_progress"),
            ("finalizing", "in_progress"),
            ("completed", "completed"),
            ("failed", "failed"),
            ("expired", "failed"),
            ("cancelled", "cancelled"),
        ]:
            venue = GroqBatch(client=FakeGroqClient(status=raw))
            state = venue.status("batch_groq_1")
            assert isinstance(state, BatchState)
            assert state.status == expected


class TestPricing:
    def test_gpt_oss_is_on_the_sheet_so_a_groq_run_settles(self):
        # Before this the sheet had no Groq rows at all, so a Groq settlement
        # produced a receipt with no cost and no captured spread — a real run
        # that looked like it had been free.
        assert offpeak.prices.get_price("openai/gpt-oss-120b") == (0.15, 0.60)
        assert offpeak.prices.get_price("openai/gpt-oss-20b") == (0.075, 0.30)

    def test_the_batch_tier_is_the_standard_fifty_percent_rule(self):
        for model in ("openai/gpt-oss-120b", "openai/gpt-oss-20b"):
            listed = offpeak.prices.list_cost_usd(model, 1_000_000, 1_000_000)
            batched = offpeak.prices.batch_cost_usd(model, 1_000_000, 1_000_000)
            assert batched == pytest.approx(listed * 0.5)

    def test_groq_publishes_no_fast_tier_so_none_is_implied(self):
        # An urgency spread the venue does not sell is not one this sheet
        # should invent.
        assert offpeak.prices.get_fast_price("openai/gpt-oss-120b") is None
        assert offpeak.prices.urgency_spread("openai/gpt-oss-120b") is None

    def test_a_priced_groq_quote_captures_a_real_spread(self):
        q = offpeak.quote(
            [job("openai/gpt-oss-20b", "hi", max_tokens=256)],
            "48h",
            venues=[GroqBatch(client=object())],
        )
        assert q.unpriced == 0
        assert q.list_usd > 0
        assert q.batch_usd == pytest.approx(q.list_usd * 0.5)

    def test_an_unknown_model_is_unpriced_rather_than_guessed(self):
        # The sheet's contract, unchanged: a model nobody published a rate for
        # settles as unpriced, never as free. Groq models used to land here;
        # now only genuinely unknown ones do.
        assert offpeak.prices.get_price("some-unlisted-model-v9") is None
        q = offpeak.quote(
            [job("groq/compound", "hi", max_tokens=10)],
            "48h",
            venues=[GroqBatch(client=object())],
        )
        assert q.unpriced == 1
        assert q.list_usd == 0.0
