"""The queue-latency probe — everything that happens without touching a venue.

Nothing here submits anything. The venue is stubbed, the clock is injected and
`sleep` is a no-op, so the tests measure the instrument rather than a queue.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "queue_probe", Path(__file__).resolve().parent.parent / "tools" / "queue_probe.py"
)
qp = importlib.util.module_from_spec(_spec)
# Registered before exec: the tool's dataclasses carry string annotations (it
# imports `annotations` from __future__), and dataclasses resolves those through
# sys.modules. Loading it by path without this raises at class-creation time.
sys.modules["queue_probe"] = qp
_spec.loader.exec_module(qp)


class Boom(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class TestWindowClassification:
    def test_a_plan_refusal_is_unknown_not_a_rejected_window(self):
        # The distinction is the whole probe. A 403 is an answer about the
        # account; it says nothing about whether the string was ever valid, and
        # recording it as "rejected" would delete a window that exists.
        exc = Boom("Not available for your plan ... not_available_for_plan", 403)
        accepted, detail = qp._classify(exc)
        assert accepted is None
        assert "refused the caller" in detail

    def test_a_complaint_about_the_window_is_a_rejection(self):
        exc = Boom("Invalid value for 'completion_window': must be 24h..7d", 400)
        accepted, detail = qp._classify(exc)
        assert accepted is False
        assert "rejected" in detail

    def test_a_400_about_something_else_is_not_evidence_about_the_window(self):
        exc = Boom("Invalid value for 'endpoint'", 400)
        assert qp._classify(exc)[0] is None

    def test_an_unrecognised_failure_is_unknown_rather_than_assumed(self):
        assert qp._classify(Boom("connection reset"))[0] is None

    def test_accepted_windows_are_only_the_confirmed_ones(self):
        probe = [
            qp.WindowProbe("24h", True, ""),
            qp.WindowProbe("48h", None, ""),
            qp.WindowProbe("72h", False, ""),
        ]
        assert qp.accepted_from_probe(probe) == ["24h"]


class TestTheProbeIsFree:
    def test_an_accepted_window_is_cancelled_on_the_spot(self, monkeypatch):
        # A created batch runs, and a running batch bills. The probe is only
        # free if it never leaves one behind.
        cancelled = []

        class Stub:
            def submit(self, jobs):
                return "batch_1"

            def cancel(self, handle):
                cancelled.append(handle)

        monkeypatch.setattr(qp, "_venue", lambda name, window: Stub())
        probe = qp.probe_completion_windows(windows=("24h", "48h"))
        assert [p.accepted for p in probe] == [True, True]
        assert cancelled == ["batch_1", "batch_1"]

    def test_a_failed_cancel_is_shouted_about_not_swallowed(self, monkeypatch):
        class Stub:
            def submit(self, jobs):
                return "batch_1"

            def cancel(self, handle):
                raise Boom("nope")

        monkeypatch.setattr(qp, "_venue", lambda name, window: Stub())
        probe = qp.probe_completion_windows(windows=("24h",))
        assert "CANCEL FAILED" in probe[0].detail
        assert "may bill" in probe[0].detail


class TestMeasurement:
    def _clock(self, *offsets):
        base = datetime(2026, 8, 23, 3, 0, 0, tzinfo=timezone.utc)
        times = iter([base + timedelta(seconds=o) for o in offsets])
        return lambda: next(times)

    def _stub_venue(self, monkeypatch, states, usage=None, status="completed"):
        class State:
            def __init__(self, s):
                self.status = s
                self.failed = 0

            @property
            def done(self):
                return self.status in ("completed", "failed", "cancelled")

        seq = iter([State(s) for s in states])

        class Result:
            raw = usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 4}

        class Stub:
            cancelled = []

            def submit(self, jobs):
                return "batch_1"

            def status(self, handle):
                return next(seq)

            def collect(self, handle):
                return {"a": Result()}

            def cancel(self, handle):
                Stub.cancelled.append(handle)

        Stub.cancelled = []
        monkeypatch.setattr(qp, "_venue", lambda name, window: Stub())
        return Stub

    def test_it_records_the_fraction_of_the_declared_window_actually_used(
        self, monkeypatch
    ):
        self._stub_venue(monkeypatch, ["in_progress", "completed"])
        leg = qp.measure_leg(
            "openai", "24h", max_wait=600, poll=0, now=self._clock(0, 864), sleep=lambda s: None
        )
        assert leg.status == "completed"
        assert leg.elapsed_seconds == 864.0
        # 864s of a 24h window is exactly 1%.
        assert leg.fraction_of_window_used == pytest.approx(0.01)

    def test_a_batch_still_open_at_the_cutoff_is_left_running_not_cancelled(
        self, monkeypatch
    ):
        # The old design cancelled here and wrote "censored" — destroying
        # exactly the slow observations the map exists for. Now the leg is
        # recorded open, the handle is kept, and a later run resolves it.
        stub = self._stub_venue(monkeypatch, ["in_progress"] * 50)
        leg = qp.measure_leg(
            "openai", "24h", max_wait=0, poll=0, now=self._clock(0, 1), sleep=lambda s: None
        )
        assert leg.status == "open"
        assert leg.elapsed_seconds is None
        assert leg.fraction_of_window_used is None
        assert leg.handle == "batch_1"
        assert "left running" in leg.note
        assert stub.cancelled == [], "the tail must not be cancelled — it is the product"

    def test_a_poll_failure_leaves_the_batch_running_too(self, monkeypatch):
        # A network blip on our side says nothing about the batch. Cancelling
        # would have converted our failure into the venue's.
        class State:
            status = "in_progress"
            failed = 0
            done = False

        class Stub:
            cancelled = []

            def submit(self, jobs):
                return "batch_1"

            def status(self, handle):
                raise Boom("connection reset")

            def cancel(self, handle):
                Stub.cancelled.append(handle)

        Stub.cancelled = []
        monkeypatch.setattr(qp, "_venue", lambda name, window: Stub())
        leg = qp.measure_leg(
            "openai", "24h", max_wait=60, poll=0, now=self._clock(0, 1), sleep=lambda s: None
        )
        assert leg.status == "open"
        assert leg.handle == "batch_1"
        assert "poll failed" in leg.note
        assert Stub.cancelled == []

    def test_a_failed_submit_is_recorded_rather_than_raised(self, monkeypatch):
        class Stub:
            def submit(self, jobs):
                raise Boom("Not available for your plan", 403)

        monkeypatch.setattr(qp, "_venue", lambda name, window: Stub())
        leg = qp.measure_leg("groq", "24h", max_wait=1, poll=0, now=self._clock(0))
        assert leg.status == "submit_failed"
        assert "Not available for your plan" in leg.note

    def test_it_prices_what_it_measured(self, monkeypatch):
        self._stub_venue(
            monkeypatch, ["completed"], usage={"prompt_tokens": 1_000_000, "completion_tokens": 0}
        )
        leg = qp.measure_leg(
            "openai", "24h", max_wait=600, poll=0, now=self._clock(0, 10), sleep=lambda s: None
        )
        # gpt-5.6-luna is $0.20/1M in, batched at half.
        assert leg.paid_usd == pytest.approx(0.10)


class TestTheCap:
    def _measure(self, calls):
        def fake(venue_name, window, **kw):
            calls.append((venue_name, window))
            leg = qp.Leg(
                venue=venue_name,
                model=qp.VENUE_SPECS[venue_name]["model"],
                declared_window=window,
                declared_window_seconds=qp.WINDOW_SECONDS[window],
                jobs=2,
                status="completed",
            )
            leg.paid_usd = 0.001
            return leg

        return fake

    def test_a_leg_that_would_break_the_cap_is_skipped_and_says_so(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setattr(qp, "_quote_list_usd", lambda *a, **k: 0.008)
        calls = []
        session = qp.run_series(
            ["anthropic", "openai"],
            {"anthropic": ["24h"], "openai": ["24h"]},
            cap_usd=0.01,
            max_wait=1,
            poll=0,
            measure=self._measure(calls),
        )
        assert calls == [("anthropic", "24h")], "the second leg must not have run"
        skipped = session.legs[1]
        assert skipped.status == "skipped"
        assert "over the $0.0100 cap" in skipped.skipped_reason
        assert "0.006000" in skipped.skipped_reason, "it should say by how much"

    def test_the_cap_counts_the_run_not_each_leg(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setattr(qp, "_quote_list_usd", lambda *a, **k: 0.004)
        calls = []
        qp.run_series(
            ["anthropic", "openai"],
            {"anthropic": ["24h"], "openai": ["24h"]},
            cap_usd=0.01,
            max_wait=1,
            poll=0,
            measure=self._measure(calls),
        )
        assert len(calls) == 2, "two legs at 0.004 fit under 0.01"

    def test_a_missing_key_skips_that_venue_and_not_the_run(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setattr(qp, "_quote_list_usd", lambda *a, **k: 0.0001)
        calls = []
        session = qp.run_series(
            ["anthropic", "openai"],
            {"anthropic": ["24h"], "openai": ["24h"]},
            cap_usd=0.01,
            max_wait=1,
            poll=0,
            measure=self._measure(calls),
        )
        assert calls == [("openai", "24h")]
        assert session.legs[0].skipped_reason == "no ANTHROPIC_API_KEY in the environment"

    def test_the_default_cap_is_under_a_cent(self):
        assert qp.DEFAULT_CAP_USD <= 0.01


class TestTheTable:
    def _record(self, **over):
        leg = {
            "venue": "openai",
            "model": "gpt-5.6-luna",
            "declared_window": "24h",
            "jobs": 2,
            "elapsed_seconds": 146.2,
            "fraction_of_window_used": 0.001692,
            "status": "completed",
            "paid_usd": 7.8e-06,
        }
        leg.update(over)
        return {"date": "2026-08-23", "legs": [leg]}

    def test_a_row_reports_the_observation_it_made(self):
        row = qp.render_rows(self._record())
        assert "| 2026-08-23 |" in row
        assert "2m26s" in row
        assert "0.169%" in row

    def test_a_skipped_leg_says_why_on_the_row(self):
        row = qp.render_rows(
            self._record(status="skipped", skipped_reason="no OPENAI_API_KEY", paid_usd=None)
        )
        assert "skipped — no OPENAI_API_KEY" in row
        assert "| — |" in row

    def test_the_table_is_a_projection_of_its_records(self, tmp_path):
        # #21's lesson, applied here before it can bite: a re-render corrects a
        # session rather than appending a second opinion, and a new column
        # repairs every row instead of orphaning the ones beneath it.
        (tmp_path / "2026-08-23-queue.json").write_text(json.dumps(self._record()))
        (tmp_path / "QUEUE.md").write_text("# stale\n| session | old |\n| 2026-08-01 | x |\n")
        qp.rebuild_table(tmp_path / "QUEUE.md", tmp_path)
        text = (tmp_path / "QUEUE.md").read_text()
        assert text.startswith(qp.QUEUE_HEADER)
        assert "2026-08-01" not in text
        assert "| old |" not in text

    def test_a_session_whose_record_vanished_loses_its_rows(self, tmp_path):
        (tmp_path / "QUEUE.md").write_text("x")
        assert qp.rebuild_table(tmp_path / "QUEUE.md", tmp_path) == 0

    def test_it_ignores_the_boards_own_records(self, tmp_path):
        # QUEUE.md must not absorb the quote/mark records sharing the directory.
        (tmp_path / "2026-08-23-mark.json").write_text(json.dumps({"night_of": "x"}))
        assert qp.rebuild_table(tmp_path / "QUEUE.md", tmp_path) == 0

    def test_the_header_refuses_to_imply_a_curve(self):
        assert "no percentile" in qp.QUEUE_HEADER
        assert "one submission" in qp.QUEUE_HEADER


class TestPlanning:
    def test_groq_contributes_no_legs_until_a_window_is_confirmed(self):
        # None of the seven candidate strings has been confirmed to exist, so
        # the honest default is to measure none of them.
        assert qp.plan_legs(["groq"], {"groq": []}) == []

    def test_the_single_window_venues_get_exactly_one_leg_each(self):
        plan = qp.plan_legs(["anthropic", "openai"], {"anthropic": ["24h"], "openai": ["24h"]})
        assert plan == [("anthropic", "24h"), ("openai", "24h")]

    def test_a_ceiling_is_sized_for_the_model_that_reasons(self):
        # gpt-oss burns hundreds of tokens before it answers; an empty
        # completion still bills and still takes queue time to produce.
        assert qp.CEILINGS["openai/gpt-oss-20b"] >= 512


class TestOpenLegResolution:
    """The park-and-resolve loop: how the probe observes the tail."""

    def _record(self, tmp_path, date="2026-08-26", status="open", handle="batch_9"):
        record = {
            "date": date,
            "started_utc": f"{date}T08:00:00+00:00",
            "cap_usd": 0.01,
            "spent_usd": 0.0,
            "legs": [
                {
                    "venue": "openai",
                    "model": "gpt-5.6-luna",
                    "declared_window": "24h",
                    "declared_window_seconds": 86400,
                    "jobs": 2,
                    "submitted_utc": f"{date}T08:00:00+00:00",
                    "completed_utc": None,
                    "elapsed_seconds": None,
                    "fraction_of_window_used": None,
                    "status": status,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "paid_usd": None,
                    "quoted_list_usd": 0.001,
                    "skipped_reason": None,
                    "note": "still open",
                    "handle": handle,
                }
            ],
        }
        path = tmp_path / f"{date}-queue.json"
        path.write_text(json.dumps(record))
        qp.save_open(
            tmp_path,
            [
                {
                    "record": path.name,
                    "leg_index": 0,
                    "venue": "openai",
                    "model": "gpt-5.6-luna",
                    "declared_window": "24h",
                    "declared_window_seconds": 86400,
                    "handle": handle,
                    "submitted_utc": f"{date}T08:00:00+00:00",
                    "last_checked_utc": f"{date}T08:30:00+00:00",
                }
            ],
        )
        return path

    def _state(self, status, raw_status=None, completed_at_utc=None, completed=2, total=2):
        class State:
            pass

        s = State()
        s.status = status
        s.raw_status = raw_status
        s.completed_at_utc = completed_at_utc
        s.completed = completed
        s.failed = 0
        s.total = total
        s.done = status in ("completed", "failed", "cancelled")
        return s

    def _venue_stub(self, monkeypatch, state, usage=None):
        class Result:
            raw = usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 4}

        class Stub:
            cancelled = []

            def status(self, handle):
                return state

            def collect(self, handle):
                return {"a": Result()}

            def cancel(self, handle):
                Stub.cancelled.append(handle)

        Stub.cancelled = []
        monkeypatch.setattr(qp, "_venue", lambda name, window: Stub())
        return Stub

    def _now(self, iso):
        fixed = datetime.fromisoformat(iso)
        return lambda: fixed

    def test_a_completed_leg_gets_the_providers_own_timestamp(
        self, tmp_path, monkeypatch
    ):
        # Submitted 08:00, provider says it finished 14:00 — the row must say
        # six hours even though we only looked the next morning.
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        path = self._record(tmp_path)
        self._venue_stub(
            monkeypatch,
            self._state("completed", completed_at_utc="2026-08-26T14:00:00+00:00"),
        )
        qp.resolve_open(tmp_path, now=self._now("2026-08-27T06:30:00+00:00"))

        leg = json.loads(path.read_text())["legs"][0]
        assert leg["status"] == "completed"
        assert leg["elapsed_seconds"] == 6 * 3600
        assert leg["fraction_of_window_used"] == pytest.approx(0.25)
        assert qp.load_open(tmp_path) == []

    def test_without_a_provider_timestamp_the_row_admits_the_bound(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        path = self._record(tmp_path)
        self._venue_stub(monkeypatch, self._state("completed"))
        qp.resolve_open(tmp_path, now=self._now("2026-08-27T06:30:00+00:00"))

        leg = json.loads(path.read_text())["legs"][0]
        assert leg["status"] == "completed"
        assert "upper bound" in leg["note"]

    def test_an_expiry_is_recorded_as_the_finding_it_is(self, tmp_path, monkeypatch):
        # OpenAI folds "expired" into failed for run()'s purposes; the probe
        # must keep the word, because expiry-at-window IS the product's risk.
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        path = self._record(tmp_path)
        self._venue_stub(
            monkeypatch,
            self._state(
                "failed",
                raw_status="expired",
                completed_at_utc="2026-08-27T08:00:00+00:00",
                completed=1,
                total=2,
            ),
        )
        qp.resolve_open(tmp_path, now=self._now("2026-08-27T08:30:00+00:00"))

        leg = json.loads(path.read_text())["legs"][0]
        assert leg["status"] == "expired"
        assert "partial-completion" in leg["note"]
        assert qp.load_open(tmp_path) == []

    def test_a_batch_far_past_its_window_is_cancelled_and_the_overrun_recorded(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        path = self._record(tmp_path)
        stub = self._venue_stub(monkeypatch, self._state("in_progress"))
        # 24h window + 48h grace = 72h; check at 80h after submission.
        qp.resolve_open(tmp_path, now=self._now("2026-08-29T16:30:00+00:00"))

        leg = json.loads(path.read_text())["legs"][0]
        assert leg["status"] == "overran_window"
        assert stub.cancelled == ["batch_9"]
        assert qp.load_open(tmp_path) == []

    def test_a_still_running_leg_inside_its_window_stays_parked(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        self._record(tmp_path)
        stub = self._venue_stub(monkeypatch, self._state("in_progress"))
        qp.resolve_open(tmp_path, now=self._now("2026-08-26T12:00:00+00:00"))

        assert len(qp.load_open(tmp_path)) == 1
        assert stub.cancelled == []

    def test_a_missing_key_parks_the_leg_rather_than_losing_it(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        self._record(tmp_path)
        qp.resolve_open(tmp_path, now=self._now("2026-08-27T06:30:00+00:00"))
        assert len(qp.load_open(tmp_path)) == 1

    def test_open_legs_are_registered_from_a_session(self, tmp_path):
        session = qp.Session(
            date="2026-08-26", started_utc="2026-08-26T08:00:00+00:00", cap_usd=0.01
        )
        session.legs.append(
            qp.Leg(
                venue="openai",
                model="gpt-5.6-luna",
                declared_window="24h",
                declared_window_seconds=86400,
                jobs=2,
                submitted_utc="2026-08-26T08:00:00+00:00",
                status="open",
                handle="batch_7",
            )
        )
        session.legs.append(
            qp.Leg(
                venue="anthropic",
                model="claude-haiku-4-5",
                declared_window="24h",
                declared_window_seconds=86400,
                jobs=2,
                status="completed",
            )
        )
        added = qp.register_open_legs(tmp_path, "2026-08-26-queue.json", session)
        assert added == 1
        assert qp.load_open(tmp_path)[0]["handle"] == "batch_7"

    def test_resolution_updates_the_sessions_spend(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        path = self._record(tmp_path)
        self._venue_stub(
            monkeypatch,
            self._state("completed", completed_at_utc="2026-08-26T14:00:00+00:00"),
            usage={"prompt_tokens": 1000, "completion_tokens": 100},
        )
        qp.resolve_open(tmp_path, now=self._now("2026-08-27T06:30:00+00:00"))
        record = json.loads(path.read_text())
        assert record["legs"][0]["paid_usd"] == record["spent_usd"]
        assert record["spent_usd"] > 0


# --- --resolve-only: what a catch-up run does when nothing was dropped ------


def test_resolve_only_spends_nothing_and_submits_nothing(tmp_path, monkeypatch):
    # The catch-up crons exist to notice a dropped run. On the days nothing was
    # dropped they must not buy a second session.
    def explode(*a, **k):  # pragma: no cover — the point is that it is not called
        raise AssertionError("resolve-only must not submit")

    monkeypatch.setattr(qp, "run_series", explode)
    rc = qp.main(["--resolve-only", "--outdir", str(tmp_path)])
    assert rc == 0
    assert not list(tmp_path.glob("*-queue.json"))


def test_resolve_only_still_resolves_and_rebuilds(tmp_path, monkeypatch):
    seen = {}

    def fake_resolve(outdir, **k):
        seen["resolved"] = Path(outdir)
        return ["  mistral @ 24h (r.json): resolved completed in 4h2m"]

    monkeypatch.setattr(qp, "resolve_open", fake_resolve)
    monkeypatch.setattr(qp, "run_series", lambda *a, **k: pytest.fail("must not submit"))
    rc = qp.main(["--resolve-only", "--outdir", str(tmp_path)])
    assert rc == 0
    assert seen["resolved"] == tmp_path
    assert (tmp_path / "QUEUE.md").exists()  # the table is rebuilt from records
