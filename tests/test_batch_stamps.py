"""The provider's own clock — both ends of it.

``completed_at_utc`` (0.2.5) told a once-a-day poller when a batch actually
finished. ``created_at_utc`` (0.2.6) is the other end: when the venue says it
accepted the work. Turnaround taken between the two is the venue's own elapsed
time, free of the gap between our clock and theirs — which is the whole reason
an hourly canary can report an exact number without polling every second.

Each venue spells it differently and the drivers translate. What they must not
do is invent one: a batch object without the field yields None, never now().
"""

from __future__ import annotations

import pytest

from offpeak.venues.anthropic_batch import AnthropicBatch
from offpeak.venues.base import BatchState, iso_utc
from offpeak.venues.gemini_batch import GeminiBatch
from offpeak.venues.groq_batch import GroqBatch
from offpeak.venues.mistral_batch import MistralBatch
from offpeak.venues.openai_batch import OpenAIBatch


def obj(**fields):
    """A stand-in for a provider's batch object — attributes, nothing else."""
    return type("Batch", (), fields)()


class StampedOpenAIClient:
    """OpenAI and Groq share this surface; Groq's driver inherits status()."""

    def __init__(self, **fields):
        self.batches = self._Batches(fields)

    class _Batches:
        def __init__(self, fields):
            self.fields = fields

        def retrieve(self, handle):
            base = {
                "status": "completed",
                "request_counts": obj(completed=2, failed=0, total=2),
            }
            return obj(**{**base, **self.fields})


class StampedAnthropicClient:
    def __init__(self, **fields):
        self.messages = obj(batches=self._Batches(fields))

    class _Batches:
        def __init__(self, fields):
            self.fields = fields

        def retrieve(self, handle):
            base = {
                "processing_status": "ended",
                "request_counts": obj(succeeded=2, errored=0, expired=0,
                                      processing=0, canceled=0),
            }
            return obj(**{**base, **self.fields})


class StampedGeminiClient:
    def __init__(self, **fields):
        self.batches = self._Batches(fields)

    class _Batches:
        def __init__(self, fields):
            self.fields = fields

        def get(self, *, name):
            base = {
                "state": obj(name="JOB_STATE_SUCCEEDED"),
                "completion_stats": obj(successful_count=2, failed_count=0,
                                        incomplete_count=0),
            }
            return obj(**{**base, **self.fields})


class StampedMistralClient:
    def __init__(self, **fields):
        self.batch = obj(jobs=self._Jobs(fields))

    class _Jobs:
        def __init__(self, fields):
            self.fields = fields

        def get(self, *, job_id, **kw):
            base = {
                "status": "SUCCESS",
                "total_requests": 2,
                "succeeded_requests": 2,
                "failed_requests": 0,
            }
            return obj(**{**base, **self.fields})


class TestTheNormaliserAcceptsWhatProvidersActuallySend:
    """iso_utc already ships; these are the shapes created_at arrives in."""

    def test_unix_seconds_the_openai_and_mistral_spelling(self):
        assert iso_utc(1787925600) == "2026-08-28T14:00:00+00:00"

    def test_an_iso_string_with_a_zulu_suffix_the_gemini_spelling(self):
        assert iso_utc("2026-08-28T14:00:00Z") == "2026-08-28T14:00:00+00:00"

    def test_a_naive_string_is_read_as_utc_rather_than_local(self):
        assert iso_utc("2026-08-28T14:00:00") == "2026-08-28T14:00:00+00:00"

    @pytest.mark.parametrize("junk", [None, "", 0, -1, "not a time", object()])
    def test_anything_it_cannot_read_is_none_never_a_guess(self, junk):
        assert iso_utc(junk) is None


class TestEachDriverReadsItsOwnSpelling:
    def test_openai_reads_created_at(self):
        state = OpenAIBatch(client=StampedOpenAIClient(created_at=1787925600)).status("h")
        assert state.created_at_utc == "2026-08-28T14:00:00+00:00"

    def test_groq_inherits_the_openai_reading(self):
        state = GroqBatch(client=StampedOpenAIClient(created_at=1787925600)).status("h")
        assert state.created_at_utc == "2026-08-28T14:00:00+00:00"

    def test_anthropic_reads_created_at(self):
        client = StampedAnthropicClient(created_at="2026-08-28T14:00:00Z")
        assert AnthropicBatch(client=client).status("h").created_at_utc == (
            "2026-08-28T14:00:00+00:00"
        )

    def test_gemini_reads_create_time(self):
        client = StampedGeminiClient(create_time="2026-08-28T14:00:00Z")
        assert GeminiBatch(client=client).status("h").created_at_utc == (
            "2026-08-28T14:00:00+00:00"
        )

    def test_mistral_reads_created_at(self):
        client = StampedMistralClient(created_at=1787925600)
        assert MistralBatch(client=client).status("h").created_at_utc == (
            "2026-08-28T14:00:00+00:00"
        )

    def test_gemini_does_not_borrow_update_time_the_way_completion_does(self):
        """end_time falls back to update_time; a submit stamp has no such twin,
        and update_time on a running job is the last poll, not the accept."""
        client = StampedGeminiClient(update_time="2026-08-28T14:00:00Z")
        assert GeminiBatch(client=client).status("h").created_at_utc is None


class TestAVenueThatSaysNothingIsNotGuessedAt:
    @pytest.mark.parametrize(
        "venue, client",
        [
            (OpenAIBatch, StampedOpenAIClient()),
            (GroqBatch, StampedOpenAIClient()),
            (AnthropicBatch, StampedAnthropicClient()),
            (GeminiBatch, StampedGeminiClient()),
            (MistralBatch, StampedMistralClient()),
        ],
    )
    def test_no_stamp_on_the_batch_object_is_none(self, venue, client):
        assert venue(client=client).status("h").created_at_utc is None

    @pytest.mark.parametrize(
        "venue, client",
        [
            (OpenAIBatch, StampedOpenAIClient(created_at="wat")),
            (AnthropicBatch, StampedAnthropicClient(created_at="wat")),
            (GeminiBatch, StampedGeminiClient(create_time="wat")),
            (MistralBatch, StampedMistralClient(created_at="wat")),
        ],
    )
    def test_an_unreadable_stamp_is_none_rather_than_a_bad_number(self, venue, client):
        assert venue(client=client).status("h").created_at_utc is None


class TestTheFieldIsAdditive:
    def test_it_defaults_to_none_so_an_old_driver_still_constructs(self):
        assert BatchState(status="in_progress").created_at_utc is None

    def test_it_does_not_disturb_the_completion_stamp(self):
        client = StampedOpenAIClient(created_at=1787925600, completed_at=1787929200)
        state = OpenAIBatch(client=client).status("h")
        assert state.created_at_utc == "2026-08-28T14:00:00+00:00"
        assert state.completed_at_utc == "2026-08-28T15:00:00+00:00"

    def test_both_stamps_give_the_venues_own_elapsed_time(self):
        """The 0.2.6 point: an hour of turnaround, measured without our clock."""
        client = StampedOpenAIClient(created_at=1787925600, completed_at=1787929200)
        state = OpenAIBatch(client=client).status("h")
        from datetime import datetime

        elapsed = (
            datetime.fromisoformat(state.completed_at_utc)
            - datetime.fromisoformat(state.created_at_utc)
        ).total_seconds()
        assert elapsed == 3600.0
