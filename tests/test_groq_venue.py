"""Groq venue — network-free only. Nothing here has touched the live API."""

import pytest

import offpeak
from offpeak import job
from offpeak.venues.base import BatchState
from offpeak.venues.groq_batch import (
    COMPLETION_WINDOWS,
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
        "model", ["llama-3.3-70b", "mixtral-8x7b", "gemma2-9b", "qwen-2.5", "kimi-k2"]
    )
    def test_claims_open_weight_families(self, model):
        assert GroqBatch(client=object()).supports(model)

    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "claude-haiku-4-5"])
    def test_does_not_poach_models_another_venue_owns(self, model):
        # A job must not silently change provider because two venues answer to
        # the same name.
        assert not GroqBatch(client=object()).supports(model)

    def test_is_not_in_default_venues(self):
        # Opt-in only while untested against the live API.
        assert "groq:batch" not in {v.name for v in offpeak.default_venues()}


class TestSubmit:
    def test_submit_sends_the_chosen_window(self):
        client = FakeGroqClient()
        venue = GroqBatch(client=client, completion_window="7d")
        handle = venue.submit([job("llama-3.3-70b", "hello")])
        assert handle == "batch_groq_1"
        assert client.created["completion_window"] == "7d"
        assert client.created["endpoint"] == "/v1/chat/completions"
        assert client.created["input_file_id"] == "file_1"
        assert client.uploaded["purpose"] == "batch"

    def test_request_payload_is_the_openai_jsonl_shape(self):
        import json

        client = FakeGroqClient()
        j = job("llama-3.3-70b", "hello", temperature=0.1)
        GroqBatch(client=client).submit([j])
        line = json.loads(client.uploaded["file"][1].decode().strip())
        assert line["custom_id"] == j.id
        assert line["body"]["model"] == "llama-3.3-70b"
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
    def test_groq_models_are_unpriced_rather_than_guessed(self):
        # No Groq rates are on the bundled sheet, so a quote reports them
        # unpriced instead of inventing a number.
        q = offpeak.quote(
            [job("llama-3.3-70b", "hi", max_tokens=10)],
            "48h",
            venues=[GroqBatch(client=object())],
        )
        assert q.unpriced == 1
        assert q.list_usd == 0.0
