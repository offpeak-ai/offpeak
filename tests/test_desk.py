"""``desk=`` — optional, off by default, and never on the hot path.

Every test here fakes the network at ``urllib.request.urlopen``; none needs a
real desk or a real venue key. The one thing every test in ``TestNoPayload``
protects is the contract the brief promises: the plan request carries
metadata only, never a prompt, a message, or a provider key.
"""

from __future__ import annotations

import json

import pytest

import offpeak
from offpeak import desk as deskmod
from offpeak.job import Result
from offpeak.venues.base import BatchState, Venue


class FakeVenue(Venue):
    def __init__(self, prefix="claude", polls_to_complete=0, name="fake:batch"):
        self.name = name
        self.prefix = prefix
        self.polls_to_complete = polls_to_complete
        self.polls = 0
        self.jobs = []
        self.cancelled = []
        self.sync_runs = []

    def supports(self, model):
        return model.startswith(self.prefix)

    def submit(self, jobs):
        self.jobs = list(jobs)
        return "batch_1"

    def status(self, handle):
        self.polls += 1
        done = self.polls > self.polls_to_complete
        return BatchState(
            status="completed" if done else "in_progress",
            completed=len(self.jobs) if done else 0,
            total=len(self.jobs),
        )

    def collect(self, handle):
        return {
            j.id: Result(job=None, text="ok", raw={"input_tokens": 100, "output_tokens": 10})
            for j in self.jobs
        }

    def cancel(self, handle):
        self.cancelled.append(handle)

    def run_sync(self, j):
        self.sync_runs.append(j.id)
        return Result(job=j, text="sync", raw={"input_tokens": 100, "output_tokens": 10})


class FakeResponse:
    def __init__(self, status: int, body: object):
        self.status = status
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class BoomOnUse:
    """Raises if the SDK ever tries to reach the network with desk=None."""

    def __call__(self, *args, **kwargs):
        raise AssertionError("desk=None must never touch the network")


def install_desk(monkeypatch, handler):
    """*handler(request) -> FakeResponse* stands in for the desk server."""

    def fake_urlopen(request, timeout=None):
        return handler(request)

    monkeypatch.setattr(deskmod.urllib.request, "urlopen", fake_urlopen)


class TestOffByDefault:
    def test_no_desk_kwarg_touches_no_network(self, monkeypatch):
        monkeypatch.setattr(deskmod.urllib.request, "urlopen", BoomOnUse())
        venue = FakeVenue()
        results = offpeak.run(
            [offpeak.job("claude-haiku-4-5", "x")], "8h", venues=[venue], poll_interval=0
        )
        assert results[0].ok
        assert results[0].receipt.desk_reachable is None
        assert "desk" not in str(results[0].receipt)

    def test_desk_false_is_also_off(self, monkeypatch):
        monkeypatch.setattr(deskmod.urllib.request, "urlopen", BoomOnUse())
        ticket = offpeak.submit(
            [offpeak.job("claude-haiku-4-5", "x")], "8h", venues=[FakeVenue()], desk=False
        )
        assert ticket.desk_url is None


class TestResolveDesk:
    def test_true_reads_the_env_var(self, monkeypatch):
        monkeypatch.setenv("OFFPEAK_DESK", "https://desk.example.test")
        monkeypatch.setenv("OFFPEAK_KEY", "sekret")
        assert deskmod.resolve_desk(True) == ("https://desk.example.test", "sekret")

    def test_true_without_the_env_var_is_off(self, monkeypatch):
        monkeypatch.delenv("OFFPEAK_DESK", raising=False)
        assert deskmod.resolve_desk(True) == (None, None)

    def test_a_url_string_is_used_directly_and_ignores_the_env(self, monkeypatch):
        monkeypatch.setenv("OFFPEAK_DESK", "https://ignored.test")
        url, _ = deskmod.resolve_desk("https://desk.example.test/")
        assert url == "https://desk.example.test"  # trailing slash trimmed

    def test_none_is_off(self):
        assert deskmod.resolve_desk(None) == (None, None)


class TestVenueKeySignals:
    def test_reports_booleans_never_values(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        signals = deskmod.venue_key_signals(["anthropic:batch", "openai:batch"])
        assert signals == {"anthropic:batch": True, "openai:batch": False}
        assert "sk-super-secret" not in json.dumps(signals)


class TestNoPayload:
    """The plan request is test-enforced keyless, the same way quote() is."""

    def test_plan_request_carries_metadata_only(self, monkeypatch):
        captured = {}

        def handler(request):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(200, {"plan_id": "plan_1"})

        install_desk(monkeypatch, handler)
        monkeypatch.setenv("OFFPEAK_KEY", "sk-live-secret")
        venue = FakeVenue()
        jobs = [offpeak.job("claude-haiku-4-5", "the actual prompt text", metadata={})]
        offpeak.submit(jobs, "8h", venues=[venue], desk="https://desk.example.test")

        assert captured["url"] == "https://desk.example.test/v1/plan"
        assert captured["headers"]["Authorization"] == "Bearer sk-live-secret"
        allowed = {
            "deadline",
            "models",
            "job_count",
            "estimated_input_tokens",
            "estimated_output_tokens",
            "venue_keys",
            "sdk_version",
        }
        assert set(captured["body"]) == allowed
        blob = json.dumps(captured["body"])
        assert "the actual prompt text" not in blob
        assert "sk-live-secret" not in blob
        assert captured["body"]["job_count"] == 1
        assert captured["body"]["models"] == ["claude-haiku-4-5"]


class TestPlanApplied:
    def test_rescue_at_overrides_the_risk_buffer(self, monkeypatch):
        def handler(request):
            return FakeResponse(
                200,
                {
                    "plan_id": "plan_42",
                    "rescue_at": "2099-01-01T05:00:00+00:00",
                },
            )

        install_desk(monkeypatch, handler)
        venue = FakeVenue()
        ticket = offpeak.submit(
            [offpeak.job("claude-haiku-4-5", "x")],
            "2099-01-01T06:00:00+00:00",
            venues=[venue],
            desk="https://desk.example.test",
        )
        assert ticket.desk_plan_id == "plan_42"
        assert ticket.risk_buffer == pytest.approx(3600.0)

    def test_recommended_venue_is_tried_first(self, monkeypatch):
        def handler(request):
            return FakeResponse(200, {"plan_id": "plan_1", "venue": "second:batch"})

        install_desk(monkeypatch, handler)
        first = FakeVenue(name="first:batch")
        second = FakeVenue(name="second:batch")
        ticket = offpeak.submit(
            [offpeak.job("claude-haiku-4-5", "x")],
            "8h",
            venues=[first, second],
            desk="https://desk.example.test",
        )
        assert ticket.assignment[ticket.jobs[0].id] == "second:batch"
        assert second.jobs and not first.jobs

    def test_venue_the_caller_never_configured_is_ignored(self, monkeypatch):
        def handler(request):
            return FakeResponse(200, {"plan_id": "plan_1", "venue": "nonexistent:batch"})

        install_desk(monkeypatch, handler)
        venue = FakeVenue()
        ticket = offpeak.submit(
            [offpeak.job("claude-haiku-4-5", "x")], "8h", venues=[venue], desk="https://desk.example.test"
        )
        assert ticket.assignment[ticket.jobs[0].id] == "fake:batch"


class TestDeskOptional:
    """Any failure mode degrades to local behaviour -- never an exception,
    never a blocked or failed job."""

    @pytest.mark.parametrize(
        "handler",
        [
            pytest.param(lambda request: (_ for _ in ()).throw(OSError("down")), id="unreachable"),
            pytest.param(lambda request: FakeResponse(500, {"plan_id": "x"}), id="non_2xx"),
            pytest.param(lambda request: FakeResponse(200, {"oops": "no plan_id"}), id="malformed"),
            pytest.param(lambda request: FakeResponse(200, "not even an object"), id="wrong_shape"),
        ],
    )
    def test_a_broken_desk_never_raises_and_never_blocks(self, monkeypatch, handler):
        install_desk(monkeypatch, handler)
        venue = FakeVenue()
        results = offpeak.run(
            [offpeak.job("claude-haiku-4-5", "x")],
            "8h",
            venues=[venue],
            poll_interval=0,
            desk="https://desk.example.test",
        )
        assert results[0].ok
        assert results[0].receipt.desk_reachable is False
        assert "desk  unreachable — local plan" in str(results[0].receipt)

    def test_receipt_post_failure_still_settles_locally(self, monkeypatch):
        calls = []

        def handler(request):
            calls.append(request.full_url)
            if request.full_url.endswith("/v1/plan"):
                return FakeResponse(200, {"plan_id": "plan_1"})
            raise OSError("receipts endpoint is down")

        install_desk(monkeypatch, handler)
        venue = FakeVenue()
        results = offpeak.run(
            [offpeak.job("claude-haiku-4-5", "x")],
            "8h",
            venues=[venue],
            poll_interval=0,
            desk="https://desk.example.test",
        )
        assert results[0].ok  # the job itself is untouched by the desk failure
        assert results[0].receipt.desk_reachable is False
        assert calls[-1].endswith("/v1/receipts")


class TestDeskLine:
    def test_a_reachable_desk_stamps_host_and_plan_on_the_receipt(self, monkeypatch):
        def handler(request):
            if request.full_url.endswith("/v1/plan"):
                return FakeResponse(200, {"plan_id": "plan_7"})
            return FakeResponse(200, {"accepted": True})

        install_desk(monkeypatch, handler)
        venue = FakeVenue()
        results = offpeak.run(
            [offpeak.job("claude-haiku-4-5", "x")],
            "8h",
            venues=[venue],
            poll_interval=0,
            desk="https://desk.example.test",
        )
        receipt = results[0].receipt
        assert receipt.desk_reachable is True
        assert receipt.desk_host == "desk.example.test"
        assert receipt.desk_plan_id == "plan_7"
        assert "desk  desk.example.test · plan plan_7" in str(receipt)


class TestTicketRoundTrip:
    def test_desk_url_and_plan_id_survive_save_and_load_not_the_key(self, tmp_path, monkeypatch):
        def handler(request):
            return FakeResponse(200, {"plan_id": "plan_9"})

        install_desk(monkeypatch, handler)
        monkeypatch.setenv("OFFPEAK_KEY", "sk-should-not-be-persisted")
        venue = FakeVenue(polls_to_complete=10_000)
        ticket = offpeak.submit(
            [offpeak.job("claude-haiku-4-5", "x")],
            "8h",
            venues=[venue],
            desk="https://desk.example.test",
        )
        path = ticket.save(tmp_path / "ticket.json")
        assert "sk-should-not-be-persisted" not in path.read_text()

        reloaded = offpeak.Ticket.load(path)
        assert reloaded.desk_url == "https://desk.example.test"
        assert reloaded.desk_plan_id == "plan_9"
