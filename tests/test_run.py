"""End-to-end run() tests against a fake venue — no network, no keys."""

import pytest

import offpeak
from offpeak import Status, job
from offpeak.job import Result
from offpeak.venues.anthropic_batch import build_requests
from offpeak.venues.base import BatchState, Venue
from offpeak.venues.openai_batch import OpenAIBatch, build_jsonl, parse_output_line


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


class TestOpenAIMaxTokensSpelling:
    """The venue speaks the provider's dialect so the caller does not have to.

    Found by a real batch: every job in it came back HTTP 400 with "Unsupported
    parameter: 'max_tokens' is not supported with this model. Use
    'max_completion_tokens' instead." — hours after submission, one rejection
    per job, nothing settled.
    """

    def _body(self, j):
        import json

        return json.loads(build_jsonl([j]).decode().strip())["body"]

    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-sol", "o1", "o3-mini", "o4"])
    def test_the_newer_families_get_max_completion_tokens(self, model):
        body = self._body(job(model, "hi", max_tokens=16))
        assert body["max_completion_tokens"] == 16
        assert "max_tokens" not in body

    @pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-4.1", "chatgpt-4o-latest"])
    def test_the_older_families_keep_max_tokens(self, model):
        body = self._body(job(model, "hi", max_tokens=16))
        assert body["max_tokens"] == 16
        assert "max_completion_tokens" not in body

    def test_an_explicit_provider_spelling_wins_and_is_never_doubled(self):
        # Sending both is itself a 400, so the caller who used the provider's
        # own name for the field keeps it.
        j = job("gpt-5.6-luna", "hi", max_tokens=16, max_completion_tokens=99)
        body = self._body(j)
        assert body["max_completion_tokens"] == 99
        assert "max_tokens" not in body

    def test_other_params_are_untouched(self):
        body = self._body(job("gpt-5.6-luna", "hi", temperature=0.2, max_tokens=8))
        assert body["temperature"] == 0.2

    def test_a_job_without_a_ceiling_is_left_alone(self):
        body = self._body(job("gpt-5.6-luna", "hi"))
        assert "max_tokens" not in body and "max_completion_tokens" not in body

    def test_the_job_itself_is_not_mutated(self):
        # The translation is a rendering, not a rewrite: the caller's Job still
        # says what the caller said, and a retry through another venue is safe.
        j = job("gpt-5.6-luna", "hi", max_tokens=16)
        self._body(j)
        assert j.params == {"max_tokens": 16}

    def test_the_sync_fallback_speaks_the_same_dialect(self):
        # The rescue path must not fail the way the batch path just did.
        seen = {}

        class FakeCompletions:
            def create(self, **kwargs):
                seen.update(kwargs)
                raise RuntimeError("stop here — we only need the params")

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        venue = OpenAIBatch(client=FakeClient())
        venue.run_sync(job("gpt-5.6-luna", "hi", max_tokens=16))
        assert seen["max_completion_tokens"] == 16
        assert "max_tokens" not in seen


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


class BrokenVenue(FakeVenue):
    """A venue whose provider is having a bad day.

    ``submit_error`` fires at submit time (auth, billing, rate limit, bad model
    id); ``sync_error`` additionally kills the rescue path.
    """

    def __init__(self, *, sync_error=False, **kwargs):
        super().__init__(**kwargs)
        self.sync_error = sync_error
        self.submit_attempts = 0

    def submit(self, jobs):
        self.submit_attempts += 1
        raise RuntimeError("credit balance is too low")

    def run_sync(self, j):
        self.sync_runs.append(j.id)
        if self.sync_error:
            raise RuntimeError("sync is down too")
        return Result(job=j, text="sync", raw={"input_tokens": 100, "output_tokens": 10})


def test_submit_failure_falls_back_to_sync():
    venue = BrokenVenue()
    jobs = [job("claude-haiku-4-5", "x"), job("claude-haiku-4-5", "y")]
    results = offpeak.run(jobs, "2h", venues=[venue], poll_interval=0)

    assert venue.submit_attempts == 1
    assert sorted(venue.sync_runs) == sorted(j.id for j in jobs)
    assert all(r.ok for r in results)
    assert all(r.job.status is Status.FELL_BACK for r in results)
    assert all(r.receipt.fell_back for r in results)

    settlement = offpeak.receipt(results)
    assert (settlement.ok, settlement.fell_back, settlement.failed) == (2, 2, 0)


def test_submit_and_sync_both_failing_report_failed_results_without_raising():
    venue = BrokenVenue(sync_error=True)
    jobs = [job("claude-haiku-4-5", "x"), job("claude-haiku-4-5", "y")]

    results = offpeak.run(jobs, "2h", venues=[venue], poll_interval=0)  # must not raise

    assert len(results) == len(jobs)  # a Result for every job
    assert all(not r.ok for r in results)
    assert all(r.job.status is Status.FAILED for r in results)
    # Both failures are on the record, not just the last one.
    assert all("credit balance is too low" in r.error for r in results)
    assert all("sync is down too" in r.error for r in results)
    assert all(not r.receipt.sla_met for r in results)

    settlement = offpeak.receipt(results)
    assert (settlement.ok, settlement.failed) == (0, 2)


def test_one_venue_failing_at_submit_leaves_the_other_untouched():
    broken = BrokenVenue(prefix="claude", name="fake:broken")
    healthy = FakeVenue(prefix="gpt-", name="fake:healthy")
    doomed = [job("claude-haiku-4-5", "a")]
    fine = [job("gpt-5.6-luna", "b"), job("gpt-5.6-luna", "c")]

    results = offpeak.run(doomed + fine, "2h", venues=[broken, healthy], poll_interval=0)

    by_id = {r.job.id: r for r in results}
    # The healthy venue settled on its batch tier — no rescue, no cancellation.
    assert not healthy.sync_runs and not healthy.cancelled
    for j in fine:
        assert by_id[j.id].job.status is Status.SUCCEEDED
        assert by_id[j.id].text == f"batch:{j.messages[-1]['content']}"
        assert not by_id[j.id].receipt.fell_back
        assert by_id[j.id].receipt.venue == "fake:healthy"
    # The broken venue's job was rescued on its own venue only.
    assert broken.sync_runs == [doomed[0].id]
    assert by_id[doomed[0].id].job.status is Status.FELL_BACK

    settlement = offpeak.receipt(results)
    assert (settlement.total, settlement.ok, settlement.fell_back) == (3, 3, 1)
    assert settlement.by_venue == {"fake:broken": 1, "fake:healthy": 2}


def test_submit_failure_with_fallback_none_reports_the_provider_error():
    venue = BrokenVenue()
    results = offpeak.run(
        [job("claude-haiku-4-5", "x")], "2h", venues=[venue], poll_interval=0, fallback="none"
    )
    assert not venue.sync_runs
    assert results[0].job.status is Status.FAILED
    assert "credit balance is too low" in results[0].error


def test_sub_cent_totals_keep_significant_digits():
    settlement = offpeak.Settlement(
        total=3, ok=3, list_usd=0.0000238, paid_usd=0.0000119, by_venue={"fake:batch": 3}
    )
    rendered = str(settlement)
    assert "list      $0.0000238" in rendered
    assert "paid      $0.0000119" in rendered
    assert "captured  $0.0000119" in rendered
    assert "$0.00\n" not in rendered  # the bug this replaced


def test_dollar_totals_keep_two_decimals():
    settlement = offpeak.Settlement(total=1, ok=1, list_usd=2469.0, paid_usd=1234.5)
    rendered = str(settlement)
    assert "list      $2,469.00" in rendered
    assert "paid      $1,234.50" in rendered
    assert "captured  $1,234.50" in rendered


@pytest.mark.parametrize(
    "amount,expected",
    [
        (0.0, "0.00"),
        (0.0000119, "0.0000119"),
        (0.004, "0.00400"),
        (0.005, "0.01"),  # 2dp already shows something
        (0.5, "0.50"),
        (1.0, "1.00"),
        (1234.5, "1,234.50"),
    ],
)
def test_usd_formatting_boundaries(amount, expected):
    from offpeak.client import _usd

    assert _usd(amount) == expected


def test_settlement_line_reports_failed_count():
    venue = BrokenVenue(sync_error=True)
    results = offpeak.run([job("claude-haiku-4-5", "x")], "2h", venues=[venue], poll_interval=0)
    assert "1 failed" in str(offpeak.receipt(results))


def test_settlement_reports_what_the_fallback_left_on_the_table():
    venue = FakeVenue(polls_to_complete=10_000)  # batch never lands
    jobs = [job("claude-haiku-4-5", "x"), job("claude-haiku-4-5", "y")]
    results = offpeak.run(jobs, "2h", venues=[venue], poll_interval=0, risk_buffer=10**9)

    settlement = offpeak.receipt(results)
    # Both fell back and paid list; the spread the batch tier would have given
    # is exactly half of list, and that is what the desk left behind.
    assert settlement.fell_back == 2
    assert settlement.left_on_table_usd == pytest.approx(settlement.list_usd * 0.5)
    assert "left      $" in str(settlement)
    assert "2 job(s) missed the batch tier" in str(settlement)


def test_a_clean_batch_run_leaves_nothing_on_the_table():
    venue = FakeVenue()
    results = offpeak.run([job("claude-haiku-4-5", "x")], "2h", venues=[venue], poll_interval=0)
    settlement = offpeak.receipt(results)
    assert settlement.left_on_table_usd == 0.0
    assert "left      $" not in str(settlement)  # no line when nothing was missed


def test_receipt_renders_sub_cent_costs_per_job():
    venue = FakeVenue()
    results = offpeak.run([job("claude-haiku-4-5", "x")], "2h", venues=[venue], poll_interval=0)
    rendered = str(results[0].receipt)
    assert "fake:batch claude-haiku-4-5" in rendered
    assert "$0.0000750" in rendered  # 100 in @ $1/M + 10 out @ $5/M, batch = half
    assert "$0.00 " not in rendered  # the whole point: not rounded away


def test_receipt_render_marks_a_fallback():
    venue = FakeVenue(polls_to_complete=10_000)
    results = offpeak.run(
        [job("claude-haiku-4-5", "x")], "2h", venues=[venue], poll_interval=0, risk_buffer=10**9
    )
    assert "(sync fallback)" in str(results[0].receipt)


def test_receipt_render_shows_a_dash_for_an_unpriced_model():
    venue = FakeVenue(prefix="mystery")
    results = offpeak.run([job("mystery-model", "x")], "2h", venues=[venue], poll_interval=0)
    assert "list $—" in str(results[0].receipt)
