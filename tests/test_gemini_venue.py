"""Gemini venue — network-free. The live proof is receipts/*-gemini-1.json.

The first venue here that is not OpenAI-shaped, so most of these tests are
about the translation rather than the plumbing.
"""

import pytest

import offpeak
from offpeak import job
from offpeak.venues.base import BatchState
from offpeak.venues.gemini_batch import (
    GeminiBatch,
    build_requests,
    response_text,
    to_config,
    to_contents,
    usage_tokens,
)


class FakeGeminiClient:
    def __init__(self, state="JOB_STATE_SUCCEEDED", responses=None, stats=(1, 0, 0)):
        self.created = None
        self.cancelled = []
        self._state = state
        self._responses = responses or []
        self._stats = stats
        self.batches = self._Batches(self)
        self.models = self._Models(self)

    class _Batches:
        def __init__(self, outer):
            self.outer = outer

        def create(self, *, model, src, config=None):
            self.outer.created = {"model": model, "src": src, "config": config}
            return type("J", (), {"name": "batches/abc"})()

        def get(self, *, name):
            o = self.outer
            s, f, i = o._stats
            stats = type("S", (), {"successful_count": s, "failed_count": f,
                                   "incomplete_count": i})()
            state = type("E", (), {"name": o._state})()
            dest = type("D", (), {"inlined_responses": o._responses})()
            return type("J", (), {"state": state, "completion_stats": stats, "dest": dest})()

        def cancel(self, *, name):
            self.outer.cancelled.append(name)

    class _Models:
        def __init__(self, outer):
            self.outer = outer

        def generate_content(self, *, model, contents, config=None):
            self.outer.sync_call = {"model": model, "contents": contents, "config": config}
            usage = type("U", (), {"prompt_token_count": 5, "candidates_token_count": 2,
                                   "thoughts_token_count": 0, "total_token_count": 7})()
            return type("R", (), {
                "candidates": [{"content": {"parts": [{"text": "blue"}]}}],
                "usage_metadata": usage,
            })()


def inlined(key, text=None, error=None, usage=None):
    response = None
    if error is None:
        response = type("R", (), {
            "candidates": [{"content": {"parts": [{"text": text}]} if text is not None else {}}],
            "usage_metadata": usage or {"promptTokenCount": 4, "candidatesTokenCount": 1,
                                        "thoughtsTokenCount": 0, "totalTokenCount": 5},
        })()
    return type("I", (), {"metadata": {"key": key}, "response": response, "error": error})()


class TestMessageTranslation:
    def test_the_assistant_role_becomes_model(self):
        contents, _ = to_contents([{"role": "assistant", "content": "hi"}])
        assert contents[0]["role"] == "model"

    def test_text_becomes_parts(self):
        contents, _ = to_contents([{"role": "user", "content": "hi"}])
        assert contents == [{"role": "user", "parts": [{"text": "hi"}]}]

    def test_a_system_message_is_lifted_out_not_passed_through(self):
        # Gemini has no system role. Sending one as a turn puts the instruction
        # in the conversation instead of above it, and the model answers it.
        contents, system = to_contents(
            [{"role": "system", "content": "Be terse."}, {"role": "user", "content": "hi"}]
        )
        assert system == "Be terse."
        assert len(contents) == 1 and contents[0]["role"] == "user"

    def test_several_system_messages_are_joined(self):
        _, system = to_contents(
            [{"role": "system", "content": "A"}, {"role": "system", "content": "B"}]
        )
        assert system == "A\n\nB"

    def test_content_blocks_are_flattened(self):
        contents, _ = to_contents(
            [{"role": "user", "content": [{"text": "a"}, {"text": "b"}]}]
        )
        assert contents[0]["parts"][0]["text"] == "ab"

    def test_an_unknown_role_falls_back_to_user_rather_than_failing(self):
        contents, _ = to_contents([{"role": "tool", "content": "x"}])
        assert contents[0]["role"] == "user"


class TestConfigTranslation:
    def test_max_tokens_is_renamed(self):
        assert to_config({"max_tokens": 256}) == {"max_output_tokens": 256}

    def test_the_providers_own_spelling_also_works(self):
        assert to_config({"max_output_tokens": 256}) == {"max_output_tokens": 256}

    def test_an_unknown_param_is_dropped_not_forwarded(self):
        # The config is a typed object; an unknown key is a hard error at
        # request time, and on a batch that is discovered hours later.
        assert to_config({"frequency_penalty": 0.5}) == {}

    def test_the_system_instruction_rides_on_the_config(self):
        assert to_config({}, "Be terse.")["system_instruction"] == "Be terse."

    def test_an_explicit_system_instruction_wins(self):
        out = to_config({"system_instruction": "explicit"}, "derived")
        assert out["system_instruction"] == "explicit"

    def test_stop_is_renamed_to_stop_sequences(self):
        assert to_config({"stop": ["x"]}) == {"stop_sequences": ["x"]}


class TestUsage:
    def test_thinking_tokens_are_output_tokens(self):
        # They are billed as output. A receipt counting only the visible answer
        # would under-report the bill, and on a reasoning model that gap is
        # most of it. These are the real numbers from the first live batch.
        assert usage_tokens(
            {"promptTokenCount": 9, "thoughtsTokenCount": 13, "totalTokenCount": 22}
        ) == (9, 13)

    def test_visible_and_thinking_tokens_are_added(self):
        assert usage_tokens(
            {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 20,
             "totalTokenCount": 35}
        ) == (10, 25)

    def test_the_total_wins_where_a_sub_count_is_missing(self):
        # A missing sub-count must never shrink the receipt below the figure
        # the bill is drawn from.
        assert usage_tokens({"promptTokenCount": 10, "totalTokenCount": 100}) == (10, 90)

    def test_snake_case_from_the_sdk_reads_the_same_as_camel_from_rest(self):
        obj = type("U", (), {"prompt_token_count": 3, "candidates_token_count": 4,
                             "thoughts_token_count": 0, "total_token_count": 7})()
        assert usage_tokens(obj) == (3, 4)

    def test_no_usage_is_zero_not_a_crash(self):
        assert usage_tokens(None) == (0, 0)


class TestResponseText:
    def test_a_normal_answer(self):
        assert response_text({"candidates": [{"content": {"parts": [{"text": "blue"}]}}]}) == "blue"

    def test_a_ceiling_spent_on_thinking_is_an_empty_answer_not_a_missing_one(self):
        # finishReason MAX_TOKENS with no parts: the model was billed and said
        # nothing. That is "" — an answer of nothing — not None.
        assert response_text({"candidates": [{"content": {}}]}) == ""

    def test_no_candidates_at_all_is_none(self):
        assert response_text({"candidates": []}) is None
        assert response_text(None) is None

    def test_several_parts_are_joined(self):
        assert response_text(
            {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
        ) == "ab"


class TestRouting:
    @pytest.mark.parametrize(
        "model", ["gemini-3.7-flash", "gemini-3.5-flash-lite", "models/gemini-3.6-flash"]
    )
    def test_claims_the_gemini_catalogue(self, model):
        assert GeminiBatch(client=object()).supports(model)

    @pytest.mark.parametrize(
        "model", ["gpt-5.6-luna", "claude-haiku-4-5", "mistral-small-latest", "openai/gpt-oss-20b"]
    )
    def test_does_not_poach_models_another_venue_owns(self, model):
        assert not GeminiBatch(client=object()).supports(model)

    def test_is_not_in_default_venues(self):
        assert "gemini:batch" not in {v.name for v in offpeak.default_venues()}


class TestSubmit:
    def test_the_job_id_rides_in_metadata_so_results_can_be_matched(self):
        # Gemini has no custom_id. Without this the responses are unattributable.
        j = job("gemini-3.7-flash", "hi", max_tokens=256)
        req = build_requests([j])[0]
        assert req["metadata"] == {"key": j.id}
        assert req["config"]["max_output_tokens"] == 256

    def test_one_model_per_batch(self):
        client = FakeGeminiClient()
        with pytest.raises(ValueError, match="one model"):
            GeminiBatch(client=client).submit(
                [job("gemini-3.7-flash", "a"), job("gemini-3.6-flash", "b")]
            )
        assert client.created is None

    def test_a_single_model_batch_goes_through(self):
        client = FakeGeminiClient()
        handle = GeminiBatch(client=client).submit([job("gemini-3.7-flash", "a")])
        assert handle == "batches/abc"
        assert client.created["model"] == "gemini-3.7-flash"


class TestStatus:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("JOB_STATE_PENDING", "in_progress"),
            ("JOB_STATE_QUEUED", "in_progress"),
            ("JOB_STATE_RUNNING", "in_progress"),
            ("JOB_STATE_UPDATING", "in_progress"),
            ("JOB_STATE_PAUSED", "in_progress"),
            ("JOB_STATE_UNSPECIFIED", "in_progress"),
            ("JOB_STATE_SUCCEEDED", "completed"),
            ("JOB_STATE_PARTIALLY_SUCCEEDED", "completed"),
            ("JOB_STATE_FAILED", "failed"),
            ("JOB_STATE_EXPIRED", "failed"),
            ("JOB_STATE_CANCELLING", "cancelled"),
            ("JOB_STATE_CANCELLED", "cancelled"),
        ],
    )
    def test_all_twelve_states_map(self, raw, expected):
        state = GeminiBatch(client=FakeGeminiClient(state=raw)).status("batches/abc")
        assert isinstance(state, BatchState)
        assert state.status == expected

    def test_partial_success_is_a_completion_not_a_failure(self):
        # Per-job errors already ride on the individual results; failing the
        # whole batch would discard the work that succeeded.
        assert GeminiBatch(
            client=FakeGeminiClient(state="JOB_STATE_PARTIALLY_SUCCEEDED")
        ).status("b").status == "completed"

    def test_counts_come_off_completion_stats(self):
        state = GeminiBatch(client=FakeGeminiClient(stats=(3, 1, 2))).status("b")
        assert (state.completed, state.failed, state.total) == (3, 1, 6)


class TestCollect:
    def test_results_are_keyed_back_to_their_jobs(self):
        client = FakeGeminiClient(responses=[inlined("job_a", "blue")])
        results = GeminiBatch(client=client).collect("b")
        assert results["job_a"].text == "blue"
        assert results["job_a"].raw == {"prompt_tokens": 4, "completion_tokens": 1}

    def test_a_per_request_error_is_carried(self):
        client = FakeGeminiClient(responses=[inlined("job_b", error="quota exhausted")])
        results = GeminiBatch(client=client).collect("b")
        assert "quota exhausted" in results["job_b"].error
        assert results["job_b"].text is None

    def test_a_response_with_no_key_is_skipped_rather_than_mis_attributed(self):
        orphan = type("I", (), {"metadata": {}, "response": None, "error": None})()
        assert GeminiBatch(client=FakeGeminiClient(responses=[orphan])).collect("b") == {}

    def test_thinking_tokens_reach_the_receipt(self):
        client = FakeGeminiClient(
            responses=[inlined("job_c", None, usage={"promptTokenCount": 9,
                                                     "thoughtsTokenCount": 13,
                                                     "totalTokenCount": 22})]
        )
        results = GeminiBatch(client=client).collect("b")
        assert results["job_c"].raw == {"prompt_tokens": 9, "completion_tokens": 13}
        assert results["job_c"].text == ""


class TestSyncFallback:
    def test_it_translates_and_reports_usage(self):
        client = FakeGeminiClient()
        result = GeminiBatch(client=client).run_sync(
            job("gemini-3.7-flash", "hi", max_tokens=64)
        )
        assert result.text == "blue"
        assert result.raw == {"prompt_tokens": 5, "completion_tokens": 2}
        assert client.sync_call["config"]["max_output_tokens"] == 64

    def test_a_provider_failure_is_captured_not_raised(self):
        client = FakeGeminiClient()

        def explode(**kw):
            raise RuntimeError("upstream is down")

        client.models.generate_content = explode
        result = GeminiBatch(client=client).run_sync(job("gemini-3.7-flash", "hi"))
        assert "upstream is down" in result.error


class TestCancel:
    def test_cancel_reaches_the_venue(self):
        client = FakeGeminiClient()
        GeminiBatch(client=client).cancel("batches/abc")
        assert client.cancelled == ["batches/abc"]

    def test_a_failing_cancel_does_not_raise(self):
        client = FakeGeminiClient()

        def explode(**kw):
            raise RuntimeError("nope")

        client.batches.cancel = explode
        GeminiBatch(client=client).cancel("b")


class TestPricing:
    def test_a_gemini_quote_captures_a_real_spread(self):
        q = offpeak.quote(
            [job("gemini-3.7-flash", "hi", max_tokens=256)],
            "48h",
            venues=[GeminiBatch(client=object())],
        )
        assert q.unpriced == 0
        assert q.batch_usd == pytest.approx(q.list_usd * 0.5)
