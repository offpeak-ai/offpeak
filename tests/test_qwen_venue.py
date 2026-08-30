"""Qwen venue — network-free. There is no live receipt yet; see the module
warning in ``offpeak.venues.qwen_batch``."""

import json

import pytest

import offpeak
from offpeak import job
from offpeak.venues.base import BatchState
from offpeak.venues.qwen_batch import (
    MAX_WINDOW_HOURS,
    MIN_WINDOW_HOURS,
    REGIONS,
    QwenBatch,
    window_hours,
)


class FakeQwenClient:
    """The OpenAI-shaped surface Model Studio's compatible mode exposes."""

    def __init__(self, status="completed"):
        self.uploaded = None
        self.created = None
        self._status = status
        self.output = ""
        self.files = self._Files(self)
        self.batches = self._Batches(self)

    class _Files:
        def __init__(self, outer):
            self.outer = outer

        def create(self, file=None, purpose=None):
            self.outer.uploaded = {"file": file, "purpose": purpose}
            return type("Upload", (), {"id": "file-qwen-1"})()

        def content(self, file_id):
            return type("Content", (), {"text": self.outer.output})()

    class _Batches:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.created = kwargs
            return type("Batch", (), {"id": "batch_qwen_1"})()

        def retrieve(self, handle):
            return type(
                "Batch",
                (),
                {
                    "status": self.outer._status,
                    "request_counts": type("C", (), {"completed": 1, "failed": 0, "total": 1})(),
                    "output_file_id": "out_1",
                    "error_file_id": None,
                    "created_at": 1787925600,
                    "completed_at": 1787929200,
                },
            )()


class TestWindow:
    @pytest.mark.parametrize(
        "window,hours",
        [("24h", 24), ("48h", 48), ("336h", 336), ("1d", 24), ("7d", 168), ("14d", 336)],
    )
    def test_accepts_the_documented_range_in_both_units(self, window, hours):
        assert window_hours(window) == hours
        assert QwenBatch(client=object(), completion_window=window).completion_window == window

    @pytest.mark.parametrize("window", ["23h", "337h", "15d", "0h"])
    def test_refuses_a_window_outside_the_range(self, window):
        with pytest.raises(ValueError, match="between 24h and 336h"):
            QwenBatch(client=object(), completion_window=window)

    @pytest.mark.parametrize("window", ["24", "1w", "24 hours", "", "h24"])
    def test_refuses_a_spelling_the_docs_do_not(self, window):
        with pytest.raises(ValueError, match="integer with an 'h' or 'd' unit"):
            window_hours(window)

    def test_the_bounds_are_the_documented_ones(self):
        assert (MIN_WINDOW_HOURS, MAX_WINDOW_HOURS) == (24, 336)


class TestRegion:
    def test_international_is_the_default(self):
        venue = QwenBatch(client=object())
        assert venue.region == "intl"
        assert venue.base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def test_china_region_has_its_own_base_url(self):
        venue = QwenBatch(client=object(), region="cn")
        assert venue.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_an_unknown_region_is_refused(self):
        with pytest.raises(ValueError, match="region must be one of"):
            QwenBatch(client=object(), region="us")

    def test_both_regions_are_https(self):
        assert all(url.startswith("https://") for url in REGIONS.values())

    def test_refuses_to_build_a_client_without_a_key(self, monkeypatch):
        pytest.importorskip("openai")
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
            _ = QwenBatch().client

    @pytest.mark.parametrize("name", ["DASHSCOPE_API_KEY", "ALIBABA_API_KEY"])
    def test_either_key_name_builds_a_client_at_the_regions_url(self, monkeypatch, name):
        pytest.importorskip("openai")
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
        monkeypatch.setenv(name, "sk-test")
        client = QwenBatch(region="cn").client
        assert str(client.base_url).rstrip("/") == REGIONS["cn"]


class TestRouting:
    @pytest.mark.parametrize(
        "model", ["qwen3.7-max", "qwen3.8-max", "qwen-max", "qwen-plus", "qwen3.7-plus"]
    )
    def test_claims_model_studios_spelling(self, model):
        assert QwenBatch(client=object()).supports(model)

    @pytest.mark.parametrize("model", ["qwen/qwen3.6-27b", "qwen/qwen3-32b"])
    def test_does_not_claim_another_catalogues_namespace(self, model):
        # "qwen/…" is how Groq spells the open-weight models it serves. A bare
        # "qwen" prefix would route a Groq-spelled id to Alibaba.
        assert not QwenBatch(client=object()).supports(model)

    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "claude-haiku-4-5", "deepseek-v4-flash"])
    def test_does_not_poach_models_another_venue_owns(self, model):
        assert not QwenBatch(client=object()).supports(model)

    def test_is_not_in_default_venues(self):
        assert "qwen:batch" not in {v.name for v in offpeak.default_venues()}


class TestSubmit:
    def test_submit_is_the_openai_shape_with_the_chosen_window(self):
        client = FakeQwenClient()
        venue = QwenBatch(client=client, completion_window="14d")
        handle = venue.submit([job("qwen3.7-max", "hello")])
        assert handle == "batch_qwen_1"
        assert client.uploaded["purpose"] == "batch"
        assert client.created["input_file_id"] == "file-qwen-1"
        assert client.created["endpoint"] == "/v1/chat/completions"
        assert client.created["completion_window"] == "14d"

    def test_request_line_matches_the_endpoint_and_passes_max_tokens_through(self):
        client = FakeQwenClient()
        j = job("qwen3.7-max", "hello", max_tokens=64, temperature=0.2)
        QwenBatch(client=client).submit([j])
        line = json.loads(client.uploaded["file"][1].decode().strip())
        assert line["custom_id"] == j.id
        assert line["url"] == "/v1/chat/completions"
        assert line["body"]["model"] == "qwen3.7-max"
        assert line["body"]["max_tokens"] == 64
        assert "max_completion_tokens" not in line["body"]

    def test_status_maps_onto_the_shared_vocabulary_and_carries_both_stamps(self):
        for raw, expected in [
            ("validating", "in_progress"),
            ("in_progress", "in_progress"),
            ("completed", "completed"),
            ("failed", "failed"),
            ("expired", "failed"),
            ("cancelled", "cancelled"),
        ]:
            state = QwenBatch(client=FakeQwenClient(status=raw)).status("batch_qwen_1")
            assert isinstance(state, BatchState)
            assert state.status == expected
        state = QwenBatch(client=FakeQwenClient()).status("batch_qwen_1")
        assert state.created_at_utc == "2026-08-28T14:00:00+00:00"
        assert state.completed_at_utc == "2026-08-28T15:00:00+00:00"

    def test_collect_reads_the_openai_output_shape(self):
        client = FakeQwenClient()
        j = job("qwen3.7-max", "hello")
        client.output = json.dumps(
            {
                "custom_id": j.id,
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{"message": {"content": "hi"}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                    },
                },
            }
        )
        results = QwenBatch(client=client).collect("batch_qwen_1")
        assert results[j.id].text == "hi"
        assert results[j.id].raw == {"prompt_tokens": 5, "completion_tokens": 2}


class TestPricing:
    def test_the_flagship_rows_are_on_the_sheet(self):
        assert offpeak.prices.get_price("qwen3.7-max") == (2.50, 7.50)
        assert offpeak.prices.get_price("qwen3.8-max") == (2.00, 6.00)
        # A date-pinned id inherits its family row.
        assert offpeak.prices.get_price("qwen3.7-max-2026-05-20") == (2.50, 7.50)

    def test_the_batch_tier_is_the_standard_fifty_percent_rule(self):
        for model in ("qwen3.7-max", "qwen3.8-max"):
            listed = offpeak.prices.list_cost_usd(model, 1_000_000, 1_000_000)
            batched = offpeak.prices.batch_cost_usd(model, 1_000_000, 1_000_000)
            assert batched == pytest.approx(listed * 0.5)

    def test_the_tiered_families_are_unpriced_rather_than_guessed(self):
        # plus/flash price by context tier and thinking mode; the sheet has
        # neither dimension, so a single number would be wrong.
        assert offpeak.prices.get_price("qwen-plus") is None
        assert offpeak.prices.get_price("qwen3.7-plus") is None

    def test_no_promo_note_without_a_date(self):
        # "Limited-time 50% off" with no end date is not a PromoNote.
        assert offpeak.prices.get_promo_note("qwen3.7-max") is None

    def test_the_lane_is_a_batch(self):
        assert offpeak.prices.lane_for("qwen3.7-max") == "batch"

    def test_a_priced_quote_captures_a_real_spread(self):
        q = offpeak.quote(
            [job("qwen3.7-max", "hi", max_tokens=256)],
            "48h",
            venues=[QwenBatch(client=object())],
        )
        assert q.unpriced == 0
        assert q.batch_usd == pytest.approx(q.list_usd * 0.5)
