"""submit() / status() / collect() — the run that survives its process.

A provider keeps batch state server-side; a fresh venue instance in a new
process must be able to pick a handle back up. The fake venue here keeps its
batches in a class-level store to model exactly that.
"""

import json

import pytest

import offpeak
from offpeak import Status, Ticket, job
from offpeak.job import Result
from offpeak.venues.base import BatchState, Venue


class ProviderSide(Venue):
    """State lives in ``STORE`` (the "provider"), not on the instance."""

    STORE: dict[str, dict] = {}
    name = "provider:batch"

    def __init__(self, polls_to_complete=0):
        self.polls_to_complete = polls_to_complete
        self.polls = 0
        self.sync_runs = []
        self.cancelled = []

    def supports(self, model):
        return model.startswith("claude")

    def submit(self, jobs):
        handle = f"batch_{len(self.STORE) + 1}"
        self.STORE[handle] = {"jobs": list(jobs), "cancelled": False}
        return handle

    def status(self, handle):
        self.polls += 1
        done = self.polls > self.polls_to_complete
        n = len(self.STORE[handle]["jobs"])
        return BatchState(status="completed" if done else "in_progress", total=n)

    def collect(self, handle):
        return {
            j.id: Result(
                job=None,
                text=f"batch:{j.messages[-1]['content']}",
                raw={"input_tokens": 10, "output_tokens": 5},
            )
            for j in self.STORE[handle]["jobs"]
        }

    def cancel(self, handle):
        self.cancelled.append(handle)
        self.STORE[handle]["cancelled"] = True

    def run_sync(self, j):
        self.sync_runs.append(j.id)
        return Result(job=j, text="sync", raw={"input_tokens": 10, "output_tokens": 5})


@pytest.fixture(autouse=True)
def _clean_store():
    ProviderSide.STORE.clear()
    yield
    ProviderSide.STORE.clear()


def test_submit_returns_immediately_with_a_pending_ticket():
    venue = ProviderSide(polls_to_complete=10_000)
    jobs = [job("claude-haiku-4-5", f"doc {i}") for i in range(3)]
    ticket = offpeak.submit(jobs, "8h", venues=[venue])

    assert ticket.pending
    assert ticket.batches == {"provider:batch": "batch_1"}
    assert set(ticket.assignment.values()) == {"provider:batch"}
    assert all(j.status is Status.SUBMITTED for j in jobs)
    assert venue.polls == 0  # nothing was waited on
    assert "pending" in str(ticket)


def test_collect_without_waiting_returns_none_until_the_batch_lands():
    venue = ProviderSide(polls_to_complete=2)
    jobs = [job("claude-haiku-4-5", "x")]
    ticket = offpeak.submit(jobs, "8h", venues=[venue])

    assert offpeak.collect(ticket, venues=[venue], wait=False) is None
    assert offpeak.collect(ticket, venues=[venue], wait=False) is None
    results = offpeak.collect(ticket, venues=[venue], wait=False)
    assert results is not None and results[0].ok and results[0].text == "batch:x"
    assert not ticket.pending
    assert offpeak.receipt(results).captured_pct == pytest.approx(50.0)


def test_status_peeks_without_collecting():
    venue = ProviderSide()
    ticket = offpeak.submit([job("claude-haiku-4-5", "x")], "8h", venues=[venue])
    states = offpeak.status(ticket, venues=[venue])
    assert states["provider:batch"].status == "completed"
    assert ticket.pending and not ticket.collected  # status() touched nothing


def test_a_ticket_round_trips_through_json_and_resumes_in_a_fresh_process(tmp_path):
    first = ProviderSide(polls_to_complete=10_000)
    jobs = [job("claude-haiku-4-5", f"doc {i}", metadata={"k": i}) for i in range(2)]
    ticket = offpeak.submit(jobs, "8h", venues=[first])
    path = ticket.save(tmp_path / "run.json")
    del first, ticket, jobs  # the process died

    data = json.loads(path.read_text())
    assert data["version"] == 1 and data["batches"] == {"provider:batch": "batch_1"}

    later = Ticket.load(path)  # a different process, a new venue instance
    second = ProviderSide()
    results = offpeak.collect(later, venues=[second], poll_interval=0)

    assert [r.text for r in results] == ["batch:doc 0", "batch:doc 1"]
    assert [r.job.metadata["k"] for r in results] == [0, 1]  # order and metadata survive
    assert all(r.receipt is not None and r.receipt.sla_met for r in results)
    assert not second.sync_runs


def test_partial_collection_survives_a_restart(tmp_path):
    """One venue landed, the other is still open: the landed results ride on the ticket."""

    class Other(ProviderSide):
        name = "other:batch"

        def supports(self, model):
            return model.startswith("gpt")

    fast, slow = ProviderSide(), Other(polls_to_complete=10_000)
    jobs = [job("claude-haiku-4-5", "a"), job("gpt-5-mini", "b")]
    ticket = offpeak.submit(jobs, "8h", venues=[fast, slow])
    assert offpeak.collect(ticket, venues=[fast, slow], wait=False) is None
    assert set(ticket.collected) == {jobs[0].id}
    path = ticket.save(tmp_path / "t.json")

    later = Ticket.load(path)
    assert set(later.collected) == {jobs[0].id} and later.batches == {"other:batch": "batch_2"}
    results = offpeak.collect(later, venues=[ProviderSide(), Other()], poll_interval=0)
    assert [r.text for r in results] == ["batch:a", "batch:b"]


def test_collect_past_the_buffer_settles_through_the_fallback():
    venue = ProviderSide(polls_to_complete=10_000)
    jobs = [job("claude-haiku-4-5", "x"), job("claude-haiku-4-5", "y")]
    ticket = offpeak.submit(jobs, "2h", venues=[venue], risk_buffer=10**9)

    results = offpeak.collect(ticket, venues=[venue], wait=False)  # buffer already reached
    assert results is not None
    assert venue.cancelled == ["batch_1"]
    assert sorted(venue.sync_runs) == sorted(j.id for j in jobs)
    assert all(r.ok and r.receipt.fell_back for r in results)
    assert all(j.status is Status.FELL_BACK for j in jobs)


def test_a_venue_missing_at_collect_time_fails_its_jobs_clearly():
    venue = ProviderSide(polls_to_complete=10_000)
    ticket = offpeak.submit([job("claude-haiku-4-5", "x")], "2h", venues=[venue], risk_buffer=10**9)
    results = offpeak.collect(ticket, venues=[], wait=False)
    assert results is not None and not results[0].ok
    assert "not configured in this process" in results[0].error


def test_run_is_submit_then_collect():
    venue = ProviderSide()
    jobs = [job("claude-haiku-4-5", "x")]
    results = offpeak.run(jobs, "8h", venues=[venue], poll_interval=0)
    assert results[0].ok and results[0].receipt.venue == "provider:batch"


def test_unsupported_ticket_version_is_refused():
    with pytest.raises(ValueError, match="version"):
        Ticket.from_dict({"version": 99, "jobs": []})


def test_an_empty_submit_is_an_empty_settled_ticket():
    ticket = offpeak.submit([], "8h", venues=[ProviderSide()])
    assert not ticket.pending and offpeak.collect(ticket, venues=[]) == []
