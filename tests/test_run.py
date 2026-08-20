"""End-to-end run() tests against a fake venue — no network, no keys."""

import pytest

import offpeak
from offpeak import Status, job
from offpeak.job import Result
from offpeak.venues.anthropic_batch import build_requests
from offpeak.venues.base import BatchState, Venue
from offpeak.venues.openai_batch import build_jsonl, parse_output_line


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
            j.id: Result(
                job=None,
                text=f"batch:{j.messages[-1]['content']}",
                raw={"input_tokens": 100, "output_tokens": 10},
            )
            for j in self.jobs
        }

    def cancel(self, handle):
        self.cancelled.append(handle)

    def run_sync(self, j):
        self.sync_runs.append(j.id)
        return Result(job=j, text="sync", raw={"input_tokens": 100, "output_tokens": 10})


def test_happy_path_settles_at_batch_price():
    venue = FakeVenue()
    jobs = [job("claude-haiku-4-5", f"doc {i}") for i in range(3)]
    results = offpeak.run(jobs, "8h", venues=[venue], poll_interval=0)

    assert [r.job.id for r in results] == [j.id for j in jobs]  # input order
    assert all(r.ok for r in results)
    assert all(r.job.status is Status.SUCCEEDED for r in results)
    assert results[0].text == "batch:doc 0"
    assert not venue.sync_runs and not venue.cancelled

    settlement = offpeak.receipt(results)
    assert settlement.sla_met == 3
    assert settlement.captured_pct == pytest.approx(50.0)


def test_deadline_risk_falls_back_to_sync():
    venue = FakeVenue(polls_to_complete=10_000)  # batch never completes
    jobs = [job("claude-haiku-4-5", "x"), job("claude-haiku-4-5", "y")]
    results = offpeak.run(jobs, "2h", venues=[venue], poll_interval=0, risk_buffer=10**9)

    assert venue.cancelled == ["batch_1"]
    assert sorted(venue.sync_runs) == sorted(j.id for j in jobs)
    assert all(r.ok for r in results)
    assert all(r.job.status is Status.FELL_BACK for r in results)
    assert all(r.receipt.fell_back and r.receipt.sla_met for r in results)
    assert offpeak.receipt(results).captured_usd == pytest.approx(0.0)


def test_fallback_none_reports_failures():
    venue = FakeVenue(polls_to_complete=10_000)
    results = offpeak.run(
        [job("claude-haiku-4-5", "x")],
        "2h",
        venues=[venue],
        poll_interval=0,
        risk_buffer=10**9,
        fallback="none",
    )
    assert not venue.sync_runs
    assert results[0].error is not None
    assert results[0].job.status is Status.FAILED
    assert not results[0].receipt.sla_met


def test_jobs_route_to_their_venue():
    anthropic = FakeVenue(prefix="claude", name="fake:anthropic")
    openai = FakeVenue(prefix="gpt-", name="fake:openai")
    jobs = [job("claude-haiku-4-5", "a"), job("gpt-5.1", "b")]
    results = offpeak.run(jobs, "1h", venues=[anthropic, openai], poll_interval=0)
    assert results[0].receipt.venue == "fake:anthropic"
    assert results[1].receipt.venue == "fake:openai"


def test_unsupported_model_is_an_error():
    with pytest.raises(ValueError, match="no venue supports"):
        offpeak.run([job("mistral-large", "x")], "1h", venues=[FakeVenue()])


def test_openai_jsonl_round_trip():
    jobs = [job("gpt-5.1", "hello", temperature=0.1)]
    lines = build_jsonl(jobs).decode().strip().splitlines()
    assert len(lines) == 1
    import json

    record = json.loads(lines[0])
    assert record["custom_id"] == jobs[0].id
    assert record["url"] == "/v1/chat/completions"
    assert record["body"]["model"] == "gpt-5.1"
    assert record["body"]["temperature"] == 0.1

    ok_line = json.dumps(
        {
            "custom_id": "job_1",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                },
            },
        }
    )
    job_id, text, usage, error = parse_output_line(ok_line)
    assert (job_id, text, error) == ("job_1", "hi", None)
    assert usage["prompt_tokens"] == 5

    err_line = json.dumps({"custom_id": "job_2", "error": {"message": "boom"}})
    job_id, text, usage, error = parse_output_line(err_line)
    assert job_id == "job_2" and text is None and "boom" in error


def test_anthropic_requests_hoist_system_and_default_max_tokens():
    j = job("claude-haiku-4-5", "hello", system="be terse")
    (request,) = build_requests([j])
    assert request["custom_id"] == j.id
    assert request["params"]["system"] == "be terse"
    assert request["params"]["max_tokens"] == 4096
    assert all(m["role"] != "system" for m in request["params"]["messages"])

    j2 = job("claude-haiku-4-5", "hi", max_tokens=128)
    (request2,) = build_requests([j2])
    assert request2["params"]["max_tokens"] == 128
