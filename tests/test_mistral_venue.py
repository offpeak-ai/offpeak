"""Mistral venue — network-free. The live proof is receipts/*-mistral-1.json."""

import json

import pytest

import offpeak
from offpeak import job
from offpeak.venues.base import BatchState
from offpeak.venues.mistral_batch import (
    DEFAULT_TIMEOUT_HOURS,
    MistralBatch,
    build_jsonl,
)


class FakeMistralClient:
    """Mimics the surface `mistralai` exposes, keyword-only like the real one."""

    def __init__(self, status="SUCCESS", output="", error_file=None):
        self.uploaded = None
        self.created = None
        self.cancelled = []
        self.output = output
        self._status = status
        self._error_file = error_file
        self.files = self._Files(self)
        self.batch = self._Batch(self)
        self.chat = self._Chat(self)

    class _Files:
        def __init__(self, outer):
            self.outer = outer

        def upload(self, *, file=None, purpose=None):
            self.outer.uploaded = {"file": file, "purpose": purpose}
            return type("Upload", (), {"id": "file_1"})()

        def download(self, *, file_id):
            return type("Resp", (), {"text": self.outer.output})()

    class _Batch:
        def __init__(self, outer):
            self.jobs = FakeMistralClient._Jobs(outer)

    class _Jobs:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.created = kwargs
            return type("Job", (), {"id": "batch_mistral_1"})()

        def get(self, *, job_id, **kw):
            return type(
                "Job",
                (),
                {
                    "status": self.outer._status,
                    "total_requests": 2,
                    "completed_requests": 2,
                    "succeeded_requests": 1,
                    "failed_requests": 1,
                    "output_file": "out_1",
                    "error_file": self.outer._error_file,
                },
            )()

        def cancel(self, *, job_id):
            self.outer.cancelled.append(job_id)

    class _Chat:
        def __init__(self, outer):
            self.outer = outer

        def complete(self, **kwargs):
            self.outer.sync_call = kwargs
            usage = type("U", (), {"prompt_tokens": 7, "completion_tokens": 3})()
            msg = type("M", (), {"content": "blue"})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()], "usage": usage})()


class TestRouting:
    @pytest.mark.parametrize(
        "model",
        [
            "mistral-large-latest",
            "mistral-medium-2604",
            "mistral-small-latest",
            "ministral-8b-latest",
            "magistral-medium-latest",
            "codestral-2508",
            "devstral-latest",
            "pixtral-large-latest",
        ],
    )
    def test_claims_the_mistral_catalogue(self, model):
        assert MistralBatch(client=object()).supports(model)

    def test_ministral_is_not_a_prefix_of_mistral(self):
        # "ministral-8b" does not start with "mistral-". Reading the two as one
        # would silently drop the entire small-model family.
        assert not "ministral-8b-latest".startswith("mistral-")
        assert MistralBatch(client=object()).supports("ministral-8b-latest")

    @pytest.mark.parametrize(
        "model", ["gpt-5.6-luna", "claude-haiku-4-5", "openai/gpt-oss-20b", "gemini-3.7-flash"]
    )
    def test_does_not_poach_models_another_venue_owns(self, model):
        assert not MistralBatch(client=object()).supports(model)

    def test_is_not_in_default_venues(self):
        # Opt-in: its own key, its own extra.
        assert "mistral:batch" not in {v.name for v in offpeak.default_venues()}


class TestTheJsonl:
    def test_a_line_is_custom_id_and_body_only(self):
        # Mistral takes the endpoint once at job creation, so the OpenAI line's
        # `method` and `url` are not just unnecessary here, they are not part of
        # the schema.
        line = json.loads(build_jsonl([job("mistral-small-latest", "hi")]).decode().strip())
        assert set(line) == {"custom_id", "body"}

    def test_the_model_is_not_on_the_line(self):
        # It rides on the job. Putting it here too invites the two disagreeing.
        line = json.loads(build_jsonl([job("mistral-small-latest", "hi")]).decode().strip())
        assert "model" not in line["body"]

    def test_max_tokens_is_passed_through_untranslated(self):
        # max_tokens is Mistral's own spelling. The max_completion_tokens
        # rewrite that OpenAI's newer families demand would be rejected here.
        line = json.loads(
            build_jsonl([job("mistral-small-latest", "hi", max_tokens=64)]).decode().strip()
        )
        assert line["body"]["max_tokens"] == 64
        assert "max_completion_tokens" not in line["body"]

    def test_one_line_per_job_keyed_by_job_id(self):
        jobs = [job("mistral-small-latest", f"q{i}") for i in range(3)]
        lines = build_jsonl(jobs).decode().strip().splitlines()
        assert [json.loads(ln)["custom_id"] for ln in lines] == [j.id for j in jobs]


class TestOneModelPerBatch:
    def test_a_mixed_model_batch_is_refused_with_the_reason(self):
        # `Model must be provided` is a hard validation error at create, and the
        # model applies to the whole file — so a two-model batch cannot be
        # expressed. Refusing beats running every job on the first job's model.
        client = FakeMistralClient()
        jobs = [job("mistral-small-latest", "a"), job("mistral-large-latest", "b")]
        with pytest.raises(ValueError, match="one model"):
            MistralBatch(client=client).submit(jobs)
        assert client.uploaded is None, "nothing should have crossed the wire"

    def test_the_error_names_both_models_and_the_way_out(self):
        client = FakeMistralClient()
        jobs = [job("mistral-small-latest", "a"), job("mistral-large-latest", "b")]
        with pytest.raises(ValueError) as exc:
            MistralBatch(client=client).submit(jobs)
        assert "mistral-large-latest" in str(exc.value)
        assert "mistral-small-latest" in str(exc.value)
        assert "one batch per model" in str(exc.value)

    def test_one_model_goes_through_and_is_sent_at_job_level(self):
        client = FakeMistralClient()
        handle = MistralBatch(client=client).submit(
            [job("mistral-small-latest", "a"), job("mistral-small-latest", "b")]
        )
        assert handle == "batch_mistral_1"
        assert client.created["model"] == "mistral-small-latest"
        assert client.created["endpoint"] == "/v1/chat/completions"
        assert client.created["input_files"] == ["file_1"]
        assert client.uploaded["purpose"] == "batch"


class TestTheWindow:
    def test_the_default_is_mistrals_own(self):
        assert DEFAULT_TIMEOUT_HOURS == 24
        client = FakeMistralClient()
        MistralBatch(client=client).submit([job("mistral-small-latest", "a")])
        assert client.created["timeout_hours"] == 24

    def test_a_chosen_window_reaches_the_venue(self):
        # timeout_hours is the deadline, expressed in the venue's own terms.
        client = FakeMistralClient()
        MistralBatch(client=client, timeout_hours=72).submit([job("mistral-small-latest", "a")])
        assert client.created["timeout_hours"] == 72

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_nonsense_window_is_refused_at_construction(self, bad):
        with pytest.raises(ValueError, match="timeout_hours must be positive"):
            MistralBatch(client=object(), timeout_hours=bad)


class TestStatus:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("QUEUED", "in_progress"),
            ("RUNNING", "in_progress"),
            ("SUCCESS", "completed"),
            ("FAILED", "failed"),
            ("TIMEOUT_EXCEEDED", "failed"),
            ("CANCELLATION_REQUESTED", "cancelled"),
            ("CANCELLED", "cancelled"),
        ],
    )
    def test_all_seven_states_map_onto_the_shared_vocabulary(self, raw, expected):
        state = MistralBatch(client=FakeMistralClient(status=raw)).status("h")
        assert isinstance(state, BatchState)
        assert state.status == expected

    def test_a_window_that_ran_out_is_a_failure_not_a_cancellation(self):
        # Nobody asked for it to stop, so it must not read as cancelled.
        assert MistralBatch(client=FakeMistralClient(status="TIMEOUT_EXCEEDED")).status(
            "h"
        ).status == "failed"

    def test_an_unknown_state_keeps_polling_rather_than_guessing(self):
        assert MistralBatch(client=FakeMistralClient(status="WAT")).status("h").status == (
            "in_progress"
        )

    def test_completed_counts_what_succeeded_not_what_finished(self):
        # Mistral counts a failed request as completed; everywhere else in this
        # library `completed` means landed and worked.
        state = MistralBatch(client=FakeMistralClient()).status("h")
        assert (state.completed, state.failed, state.total) == (1, 1, 2)


class TestCollect:
    LINE = json.dumps(
        {
            "custom_id": "job_a",
            "response": {
                "body": {
                    "choices": [{"message": {"content": "blue"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 2},
                }
            },
        }
    )

    def test_it_reads_the_openai_shaped_output_line(self):
        results = MistralBatch(client=FakeMistralClient(output=self.LINE)).collect("h")
        assert results["job_a"].text == "blue"
        assert results["job_a"].raw == {"prompt_tokens": 11, "completion_tokens": 2}
        assert results["job_a"].error is None

    def test_a_per_line_error_is_carried_not_dropped(self):
        line = json.dumps({"custom_id": "job_b", "error": {"message": "bad request"}})
        results = MistralBatch(client=FakeMistralClient(output=line)).collect("h")
        assert results["job_b"].error is not None
        assert "bad request" in results["job_b"].error

    def test_blank_lines_are_skipped(self):
        client = FakeMistralClient(output="\n" + self.LINE + "\n\n")
        assert set(MistralBatch(client=client).collect("h")) == {"job_a"}

    def test_the_error_file_is_collected_too(self):
        client = FakeMistralClient(output=self.LINE, error_file="err_1")
        # Both files resolve through the same fake download, so the point is
        # that a job with an error_file does not skip it.
        assert MistralBatch(client=client).collect("h")


class TestSyncFallback:
    def test_it_runs_the_job_and_reports_usage(self):
        client = FakeMistralClient()
        result = MistralBatch(client=client).run_sync(
            job("mistral-small-latest", "hi", max_tokens=8)
        )
        assert result.text == "blue"
        assert result.raw == {"prompt_tokens": 7, "completion_tokens": 3}
        assert client.sync_call["model"] == "mistral-small-latest"
        assert client.sync_call["max_tokens"] == 8

    def test_a_provider_failure_is_captured_not_raised(self):
        class Boom(FakeMistralClient):
            class _Chat:
                def __init__(self, outer):
                    pass

                def complete(self, **kw):
                    raise RuntimeError("upstream is down")

        client = Boom()
        client.chat = Boom._Chat(client)
        result = MistralBatch(client=client).run_sync(job("mistral-small-latest", "hi"))
        assert result.error is not None and "upstream is down" in result.error
        assert result.text is None


class TestCancel:
    def test_cancel_reaches_the_venue(self):
        client = FakeMistralClient()
        MistralBatch(client=client).cancel("batch_mistral_1")
        assert client.cancelled == ["batch_mistral_1"]

    def test_a_failing_cancel_does_not_raise(self):
        class Boom(FakeMistralClient):
            pass

        client = Boom()

        def explode(*, job_id):
            raise RuntimeError("nope")

        client.batch.jobs.cancel = explode
        MistralBatch(client=client).cancel("h")  # must not raise


class TestPricing:
    def test_a_mistral_quote_captures_a_real_spread(self):
        q = offpeak.quote(
            [job("mistral-small-latest", "hi", max_tokens=64)],
            "48h",
            venues=[MistralBatch(client=object())],
        )
        assert q.unpriced == 0
        assert q.list_usd > 0
        assert q.batch_usd == pytest.approx(q.list_usd * 0.5)


# --------------------------------------------------------------------------- #
# _download — the streaming response that cost a real batch
# --------------------------------------------------------------------------- #


class StreamingResponse:
    """What `mistralai` actually hands back: a stream that must be read first.

    The original fake exposed a plain `.text` attribute, which is why the bug it
    is modelling shipped — the double was more forgiving than the SDK. Touching
    `.text` before `read()` raises, and the raise is a RuntimeError subclass, so
    a `getattr(response, "text", None)` does not shield the caller from it.
    """

    def __init__(self, payload: bytes):
        self._payload = payload
        self._read = False

    @property
    def text(self):
        if not self._read:
            raise RuntimeError(
                "Attempted to access streaming response content, "
                "without having called `read()`."
            )
        return self._payload.decode("utf-8")

    def read(self):
        self._read = True
        return self._payload


def _venue_returning(response):
    class Client:
        class files:  # noqa: N801 — mirrors the SDK's attribute layout
            @staticmethod
            def download(*, file_id):
                return response

    return MistralBatch(client=Client())


def test_download_reads_a_streaming_response():
    """The regression: this raised ResponseNotRead and lost a completed batch."""
    venue = _venue_returning(StreamingResponse(b'{"custom_id": "job_1"}\n'))
    assert venue._download("file_1") == '{"custom_id": "job_1"}\n'


def test_download_still_handles_a_plain_text_response():
    venue = _venue_returning(type("Resp", (), {"text": "hello"})())
    assert venue._download("file_1") == "hello"


def test_download_still_handles_raw_bytes():
    venue = _venue_returning(b"hello")
    assert venue._download("file_1") == "hello"


def test_download_survives_an_already_consumed_stream():
    """read() may raise StreamConsumed; .text works by then and must be used."""

    class Consumed:
        text = "hello"

        def read(self):
            raise RuntimeError("stream consumed")

    assert _venue_returning(Consumed())._download("file_1") == "hello"


def test_collect_returns_results_from_a_streaming_response():
    """End to end: a completed batch must not be lost to the fallback."""
    line = json.dumps(
        {
            "custom_id": "job_1",
            "response": {
                "body": {
                    "choices": [{"message": {"content": "Melancholic"}}],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 4},
                }
            },
        }
    )

    class Client:
        class files:  # noqa: N801
            @staticmethod
            def download(*, file_id):
                return StreamingResponse((line + "\n").encode("utf-8"))

        class batch:  # noqa: N801
            class jobs:  # noqa: N801
                @staticmethod
                def get(*, job_id):
                    return type(
                        "J", (), {"output_file": "f1", "error_file": None, "status": "SUCCESS"}
                    )()

    got = MistralBatch(client=Client()).collect("h1")
    assert set(got) == {"job_1"}
    assert got["job_1"].text == "Melancholic"
