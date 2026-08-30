"""DeepSeek venue — network-free. There is no live receipt yet; see the module
warning in ``offpeak.venues.deepseek_clock``.

The clock is the whole venue, so the clock is most of the test: fixed instants
on both sides of every boundary the schedule has, including the Friday 10:00
UTC to Monday 01:00 UTC stretch that a naive "hours only" reading gets wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import offpeak
from offpeak import job
from offpeak.venues.base import BatchState
from offpeak.venues.deepseek_clock import (
    BASE_URL,
    DeepSeekClock,
    is_peak,
    next_offpeak_start,
    offpeak_until,
    paid_fraction,
    rate_multiplier,
)

UTC = timezone.utc


def at(y, m, d, hh, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


# 2026-08-24 is a Monday; 2026-08-28 a Friday; 29/30 the weekend.
MON, TUE, WED, THU, FRI, SAT, SUN = 24, 25, 26, 27, 28, 29, 30


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now = self.now + timedelta(**delta)


class FakeDeepSeekClient:
    """The one OpenAI-shaped surface the driver touches: chat.completions.create."""

    def __init__(self, fail_ids=()):
        self.calls = []
        self.fail_ids = set(fail_ids)
        self.chat = type("Chat", (), {})()
        self.chat.completions = self

    def create(self, *, model, messages, **params):
        self.calls.append({"model": model, "messages": messages, **params})
        prompt = messages[-1]["content"]
        if prompt in self.fail_ids:
            raise RuntimeError("upstream 503")
        usage = type(
            "Usage",
            (),
            {"prompt_tokens": 100, "completion_tokens": 10, "prompt_cache_hit_tokens": 40},
        )()
        message = type("Message", (), {"content": f"echo:{prompt}"})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice], "usage": usage})()


class TestClockHelpers:
    @pytest.mark.parametrize(
        "instant,peak",
        [
            (at(2026, 8, MON, 0, 59, 59), False),  # a second before the first block
            (at(2026, 8, MON, 1, 0, 0), True),  # Monday 01:00 — the week's first peak
            (at(2026, 8, MON, 3, 59, 59), True),
            (at(2026, 8, MON, 4, 0, 0), False),  # block end is exclusive
            (at(2026, 8, MON, 5, 30), False),  # the gap between the blocks
            (at(2026, 8, MON, 6, 0, 0), True),
            (at(2026, 8, WED, 9, 59, 59), True),
            (at(2026, 8, WED, 10, 0, 0), False),
            (at(2026, 8, THU, 23, 0), False),
            (at(2026, 8, FRI, 9, 30), True),  # Friday's last peak hour
            (at(2026, 8, FRI, 10, 0, 0), False),  # and the weekend stretch begins
            (at(2026, 8, SAT, 2, 0), False),  # a weekday-peak hour, on a Saturday
            (at(2026, 8, SAT, 7, 0), False),
            (at(2026, 8, SUN, 8, 0), False),
            (at(2026, 8, SUN, 23, 59, 59), False),
            (at(2026, 8, 31, 1, 0, 0), True),  # the following Monday, again
        ],
    )
    def test_is_peak_on_both_sides_of_every_boundary(self, instant, peak):
        assert is_peak(instant) is peak

    def test_the_schedule_is_read_in_utc_whatever_zone_the_instant_is_in(self):
        # 09:00 Beijing (UTC+8) on a Monday is 01:00 UTC: peak.
        beijing = timezone(timedelta(hours=8))
        assert is_peak(datetime(2026, 8, MON, 9, 0, tzinfo=beijing))
        assert not is_peak(datetime(2026, 8, MON, 12, 0, tzinfo=beijing))  # 04:00 UTC

    def test_a_naive_instant_is_taken_as_utc(self):
        assert is_peak(datetime(2026, 8, MON, 2, 0))
        assert not is_peak(datetime(2026, 8, SAT, 2, 0))

    @pytest.mark.parametrize(
        "instant,release",
        [
            (at(2026, 8, MON, 2, 30), at(2026, 8, MON, 4, 0)),
            (at(2026, 8, MON, 1, 0, 0), at(2026, 8, MON, 4, 0)),
            (at(2026, 8, FRI, 9, 59, 59), at(2026, 8, FRI, 10, 0)),
            (at(2026, 8, MON, 6, 0, 0), at(2026, 8, MON, 10, 0)),
        ],
    )
    def test_next_offpeak_start_is_the_end_of_the_current_block(self, instant, release):
        assert next_offpeak_start(instant) == release

    @pytest.mark.parametrize(
        "instant",
        [
            at(2026, 8, MON, 4, 0, 0),
            at(2026, 8, MON, 5, 15),
            at(2026, 8, TUE, 12, 0),
            at(2026, 8, SAT, 2, 0),
            at(2026, 8, SUN, 7, 0),
        ],
    )
    def test_next_offpeak_start_is_now_when_now_is_off_peak(self, instant):
        assert next_offpeak_start(instant) == instant

    @pytest.mark.parametrize(
        "instant,until",
        [
            (at(2026, 8, MON, 4, 0, 0), at(2026, 8, MON, 6, 0)),  # the gap
            (at(2026, 8, MON, 5, 59, 59), at(2026, 8, MON, 6, 0)),
            (at(2026, 8, MON, 10, 0, 0), at(2026, 8, TUE, 1, 0)),  # the evening
            (at(2026, 8, MON, 23, 30), at(2026, 8, TUE, 1, 0)),
            (at(2026, 8, TUE, 0, 30), at(2026, 8, TUE, 1, 0)),
            (at(2026, 8, FRI, 10, 0, 0), at(2026, 8, 31, 1, 0)),  # the weekend, whole
            (at(2026, 8, FRI, 18, 0), at(2026, 8, 31, 1, 0)),
            (at(2026, 8, SAT, 2, 0), at(2026, 8, 31, 1, 0)),
            (at(2026, 8, SUN, 23, 59, 59), at(2026, 8, 31, 1, 0)),
            (at(2026, 8, 31, 0, 0), at(2026, 8, 31, 1, 0)),  # Monday, before the block
        ],
    )
    def test_offpeak_until_is_the_next_peak_block_start(self, instant, until):
        assert offpeak_until(instant) == until

    @pytest.mark.parametrize("instant", [at(2026, 8, MON, 2, 0), at(2026, 8, FRI, 7, 0)])
    def test_offpeak_until_is_none_at_peak(self, instant):
        # No off-peak stretch contains a peak instant; None, not a guess.
        assert offpeak_until(instant) is None

    def test_the_weekend_stretch_is_one_stretch(self):
        # Friday 10:00 to Monday 01:00: 63 hours of off-peak, unbroken.
        start = at(2026, 8, FRI, 10, 0)
        assert offpeak_until(start) - start == timedelta(hours=63)

    @pytest.mark.parametrize(
        "instant,multiplier,fraction",
        [
            (at(2026, 8, MON, 2, 0), 2.0, 1.0),
            (at(2026, 8, MON, 4, 0), 1.0, 0.5),
            (at(2026, 8, SAT, 2, 0), 1.0, 0.5),
        ],
    )
    def test_rate_multiplier_and_paid_fraction(self, instant, multiplier, fraction):
        assert rate_multiplier(instant) == multiplier
        assert paid_fraction(instant) == fraction

    def test_the_off_peak_fraction_is_the_batch_rule(self):
        # The same constant on purpose: the sheet stores the peak rate as
        # standard, and BATCH_DISCOUNT reproduces the published off-peak row.
        assert paid_fraction(at(2026, 8, SAT, 12, 0)) == offpeak.prices.BATCH_DISCOUNT


class TestRouting:
    @pytest.mark.parametrize(
        "model", ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"]
    )
    def test_claims_the_deepseek_family(self, model):
        assert DeepSeekClock(client=object()).supports(model)

    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "claude-haiku-4-5", "qwen3.7-max"])
    def test_does_not_poach_other_venues_models(self, model):
        assert not DeepSeekClock(client=object()).supports(model)

    def test_is_not_in_default_venues(self):
        # Opt-in like Groq, Mistral and Gemini: its own key, its own extra.
        assert "deepseek:clock" not in {v.name for v in offpeak.default_venues()}

    def test_base_url_is_deepseeks(self):
        assert BASE_URL == "https://api.deepseek.com"

    def test_refuses_to_build_a_client_without_the_key(self, monkeypatch):
        # Never let the OpenAI SDK fall back to OPENAI_API_KEY for a DeepSeek
        # request.
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        pytest.importorskip("openai")
        with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
            _ = DeepSeekClock().client


class TestHold:
    def test_submit_sends_nothing_and_releases_at_the_boundary(self):
        clock = FakeClock(at(2026, 8, MON, 2, 30))  # peak
        client = FakeDeepSeekClient()
        venue = DeepSeekClock(client=client, clock=clock)
        handle = venue.submit([job("deepseek-v4-flash", "a"), job("deepseek-v4-flash", "b")])

        assert handle.startswith("hold_")
        assert client.calls == [], "a hold must not touch the venue"
        state = venue.status(handle)
        assert state.status == "in_progress"
        assert state.total == 2
        assert state.raw_status == "held_for_off_peak"
        assert state.created_at_utc == "2026-08-24T02:30:00+00:00"
        assert client.calls == []

    def test_status_executes_once_released(self):
        clock = FakeClock(at(2026, 8, MON, 3, 59, 59))
        client = FakeDeepSeekClient()
        venue = DeepSeekClock(client=client, clock=clock, max_workers=1)
        jobs = [job("deepseek-v4-flash", "a"), job("deepseek-v4-flash", "b")]
        handle = venue.submit(jobs)
        assert venue.status(handle).status == "in_progress"

        clock.advance(seconds=1)  # 04:00:00 — the block ends
        state = venue.status(handle)
        assert isinstance(state, BatchState)
        assert state.status == "completed"
        assert (state.completed, state.failed, state.total) == (2, 0, 2)
        assert state.raw_status == "off_peak_executed"
        assert state.created_at_utc == "2026-08-24T03:59:59+00:00"
        assert state.completed_at_utc == "2026-08-24T04:00:00+00:00"
        assert len(client.calls) == 2

        results = venue.collect(handle)
        assert set(results) == {j.id for j in jobs}
        assert results[jobs[0].id].text == "echo:a"
        assert results[jobs[0].id].raw["prompt_tokens"] == 100
        assert results[jobs[0].id].raw["completion_tokens"] == 10
        assert results[jobs[0].id].raw["prompt_cache_hit_tokens"] == 40
        assert results[jobs[0].id].raw["regime"] == "off_peak"
        assert results[jobs[0].id].raw["paid_fraction"] == 0.5
        assert results[jobs[0].id].raw["executed_at_utc"] == "2026-08-24T04:00:00+00:00"

    def test_an_off_peak_submit_releases_immediately(self):
        clock = FakeClock(at(2026, 8, SAT, 12, 0))
        client = FakeDeepSeekClient()
        venue = DeepSeekClock(client=client, clock=clock)
        handle = venue.submit([job("deepseek-v4-pro", "a")])
        assert client.calls == []  # still nothing at submit
        assert venue.status(handle).status == "completed"
        assert len(client.calls) == 1

    def test_executes_once_and_repolls_from_memory(self):
        clock = FakeClock(at(2026, 8, SAT, 12, 0))
        client = FakeDeepSeekClient()
        venue = DeepSeekClock(client=client, clock=clock)
        handle = venue.submit([job("deepseek-v4-pro", "a")])
        venue.status(handle)
        venue.status(handle)
        assert len(client.calls) == 1

    def test_a_failed_request_is_a_failed_row_not_a_failed_hold(self):
        clock = FakeClock(at(2026, 8, SUN, 3, 0))
        client = FakeDeepSeekClient(fail_ids={"b"})
        venue = DeepSeekClock(client=client, clock=clock)
        jobs = [job("deepseek-v4-flash", "a"), job("deepseek-v4-flash", "b")]
        handle = venue.submit(jobs)
        state = venue.status(handle)
        assert state.status == "completed"
        assert (state.completed, state.failed) == (1, 1)
        results = venue.collect(handle)
        assert results[jobs[1].id].error == "upstream 503"
        assert results[jobs[1].id].raw["regime"] == "off_peak"  # stamped even on failure

    def test_the_regime_is_stamped_per_request_not_per_hold(self):
        # A hold released at 00:59:59 whose second request lands at 01:00:00
        # has one off-peak job and one peak job, and says so.
        clock = FakeClock(at(2026, 8, TUE, 0, 59, 59))
        client = FakeDeepSeekClient()
        venue = DeepSeekClock(client=client, clock=clock, max_workers=1)
        original = client.create

        def create_and_tick(**kwargs):
            response = original(**kwargs)
            clock.advance(seconds=1)
            return response

        client.create = create_and_tick
        jobs = [job("deepseek-v4-flash", "a"), job("deepseek-v4-flash", "b")]
        handle = venue.submit(jobs)
        venue.status(handle)
        results = venue.collect(handle)
        assert results[jobs[0].id].raw["regime"] == "off_peak"
        assert results[jobs[1].id].raw["regime"] == "peak"
        assert results[jobs[1].id].raw["paid_fraction"] == 1.0

    def test_cancel_drops_a_hold_before_release(self):
        clock = FakeClock(at(2026, 8, MON, 2, 0))
        client = FakeDeepSeekClient()
        venue = DeepSeekClock(client=client, clock=clock)
        handle = venue.submit([job("deepseek-v4-flash", "a")])
        venue.cancel(handle)
        clock.advance(hours=3)
        with pytest.raises(KeyError):
            venue.status(handle)
        assert client.calls == []

    def test_cancel_of_an_unknown_handle_is_quiet(self):
        DeepSeekClock(client=object(), clock=FakeClock(at(2026, 8, MON, 2, 0))).cancel("nope")

    def test_collect_before_release_is_an_error(self):
        clock = FakeClock(at(2026, 8, MON, 2, 0))
        venue = DeepSeekClock(client=FakeDeepSeekClient(), clock=clock)
        handle = venue.submit([job("deepseek-v4-flash", "a")])
        with pytest.raises(RuntimeError, match="not been released"):
            venue.collect(handle)

    def test_params_pass_through_untranslated(self):
        # max_tokens is DeepSeek's own spelling; no max_completion_tokens
        # rewrite, and nothing added about thinking — the venue default stands.
        clock = FakeClock(at(2026, 8, SAT, 1, 0))
        client = FakeDeepSeekClient()
        venue = DeepSeekClock(client=client, clock=clock)
        venue.status(venue.submit([job("deepseek-v4-flash", "a", max_tokens=2048, temperature=0)]))
        call = client.calls[0]
        assert call["max_tokens"] == 2048
        assert call["temperature"] == 0
        assert "max_completion_tokens" not in call
        assert "thinking" not in call


class TestRunSync:
    def test_runs_now_at_peak_and_says_so(self):
        clock = FakeClock(at(2026, 8, WED, 7, 0))
        client = FakeDeepSeekClient()
        result = DeepSeekClock(client=client, clock=clock).run_sync(job("deepseek-v4-flash", "a"))
        assert result.ok
        assert result.raw["regime"] == "peak"
        assert result.raw["rate_multiplier"] == 2.0
        assert result.raw["paid_fraction"] == 1.0

    def test_runs_now_off_peak_at_half(self):
        clock = FakeClock(at(2026, 8, WED, 11, 0))
        client = FakeDeepSeekClient()
        result = DeepSeekClock(client=client, clock=clock).run_sync(job("deepseek-v4-flash", "a"))
        assert result.raw["regime"] == "off_peak"
        assert result.raw["rate_multiplier"] == 1.0
        assert result.raw["paid_fraction"] == 0.5

    def test_a_provider_error_is_a_result_not_an_exception(self):
        clock = FakeClock(at(2026, 8, WED, 11, 0))
        client = FakeDeepSeekClient(fail_ids={"a"})
        result = DeepSeekClock(client=client, clock=clock).run_sync(job("deepseek-v4-flash", "a"))
        assert not result.ok
        assert result.error == "upstream 503"


class TestSettlement:
    """The cheap lane's cost, through run() and receipt(), both outcomes."""

    def _run(self, clock, client, deadline="6h", **kwargs):
        venue = DeepSeekClock(client=client, clock=clock, max_workers=1)
        jobs = [job("deepseek-v4-flash", "a"), job("deepseek-v4-flash", "b")]
        return offpeak.run(jobs, deadline, venues=[venue], poll_interval=0, **kwargs)

    def test_off_peak_execution_pays_half_of_list(self):
        results = self._run(FakeClock(at(2026, 8, SAT, 12, 0)), FakeDeepSeekClient())
        assert all(r.ok for r in results)
        assert all(r.job.status is offpeak.Status.SUCCEEDED for r in results)
        r = results[0].receipt
        # 100 in at $0.44/M, 10 out at $1.32/M: list $0.0000572, paid half.
        assert r.list_usd == pytest.approx(0.0000572)
        assert r.paid_usd == pytest.approx(0.0000286)
        assert r.paid_fraction == 0.5
        settlement = offpeak.receipt(results)
        assert settlement.captured_pct == pytest.approx(50.0)
        assert settlement.by_venue == {"deepseek:clock": 2}
        assert settlement.left_on_table_usd == 0.0

    def test_a_deadline_inside_the_peak_block_falls_back_at_list(self):
        # 02:00 Monday, two hours of peak ahead, a deadline that cannot wait:
        # run() cancels the hold under the risk buffer and runs sync, at peak.
        clock = FakeClock(at(2026, 8, MON, 2, 0))
        client = FakeDeepSeekClient()
        results = self._run(clock, client, deadline="30m", risk_buffer=10**9)
        assert all(r.ok for r in results)
        assert all(r.job.status is offpeak.Status.FELL_BACK for r in results)
        r = results[0].receipt
        assert r.fell_back
        assert r.paid_fraction == 1.0
        assert r.paid_usd == r.list_usd
        assert r.spread_usd == 0.0
        settlement = offpeak.receipt(results)
        assert settlement.captured_pct == pytest.approx(0.0)
        assert settlement.fell_back == 2
        assert settlement.left_on_table_usd == pytest.approx(settlement.list_usd * 0.5)
        assert "(sync fallback)" in str(r) and "paid 1x list" in str(r)

    def test_a_fallback_that_ran_off_peak_still_pays_half(self):
        # The row the tier rule would get wrong: the hold returned a failed
        # request, run() rescued it through run_sync while the clock was still
        # cheap, and the receipt must book half of list, not list.
        clock = FakeClock(at(2026, 8, SAT, 12, 0))
        client = FakeDeepSeekClient(fail_ids={"b"})
        venue = DeepSeekClock(client=client, clock=clock, max_workers=1)
        jobs = [job("deepseek-v4-flash", "a"), job("deepseek-v4-flash", "b")]
        # The rescue request must succeed: stop failing "b" after the hold.
        original = client.create

        def create(**kwargs):
            if len(client.calls) >= 2:
                client.fail_ids.clear()
            return original(**kwargs)

        client.create = create
        results = offpeak.run(jobs, "6h", venues=[venue], poll_interval=0)
        rescued = results[1]
        assert rescued.ok
        assert rescued.job.status is offpeak.Status.FELL_BACK
        assert rescued.receipt.fell_back
        assert rescued.receipt.paid_fraction == 0.5
        assert rescued.receipt.paid_usd == pytest.approx(rescued.receipt.list_usd * 0.5)
        settlement = offpeak.receipt(results)
        assert settlement.fell_back == 1
        assert settlement.captured_pct == pytest.approx(50.0)
        assert settlement.left_on_table_usd == 0.0
        assert "paid 0.5x list" in str(rescued.receipt)

    def test_a_batch_venue_receipt_is_unchanged(self):
        # No paid_fraction on the raw usage: the tier rule prices the job
        # exactly as before this field existed.
        from tests.test_run import FakeVenue

        results = offpeak.run(
            [job("claude-haiku-4-5", "x")], "8h", venues=[FakeVenue()], poll_interval=0
        )
        assert results[0].receipt.paid_fraction is None
        assert results[0].receipt.paid_usd == pytest.approx(results[0].receipt.list_usd * 0.5)


class TestPricing:
    def test_the_peak_rate_is_the_standard_row(self):
        assert offpeak.prices.get_price("deepseek-v4-flash") == (0.44, 1.32)
        assert offpeak.prices.get_price("deepseek-v4-pro") == (1.32, 3.96)
        assert offpeak.prices.get_price("deepseek-v4-flash-vision-exp") == (0.44, 1.32)

    @pytest.mark.parametrize(
        "model,off_peak",
        [
            ("deepseek-v4-flash", (0.22, 0.66)),
            ("deepseek-v4-pro", (0.66, 1.98)),
        ],
    )
    def test_the_batch_rule_reproduces_the_published_off_peak_column(self, model, off_peak):
        assert offpeak.prices.batch_cost_usd(model, 1_000_000, 0) == pytest.approx(off_peak[0])
        assert offpeak.prices.batch_cost_usd(model, 0, 1_000_000) == pytest.approx(off_peak[1])

    def test_the_lane_is_a_clock_not_a_batch(self):
        assert offpeak.prices.lane_for("deepseek-v4-flash") == "clock"
        assert offpeak.prices.lane_for("deepseek-v4-pro-2026-09-01") == "clock"
        assert offpeak.prices.lane_for("gpt-5.6-luna") == "batch"
        assert offpeak.prices.lane_for("qwen3.7-max") == "batch"
        assert offpeak.prices.lane_for("not-a-model") is None

    def test_no_fast_tier_is_implied(self):
        assert offpeak.prices.get_fast_price("deepseek-v4-flash") is None
        assert offpeak.prices.urgency_spread("deepseek-v4-flash") is None

    def test_a_quote_prices_the_clock_lane_like_a_batch(self):
        q = offpeak.quote(
            [job("deepseek-v4-pro", "hi", max_tokens=256)],
            "6h",
            venues=[DeepSeekClock(client=object())],
        )
        assert q.unpriced == 0
        assert q.batch_usd == pytest.approx(q.list_usd * 0.5)
