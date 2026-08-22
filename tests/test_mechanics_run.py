"""The capped settlement runner — everything that happens before money moves.

These tests never submit anything: `quote()` makes no API calls, and both paths
exercised here stop before the venues are touched.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "mechanics_run", Path(__file__).resolve().parent.parent / "tools" / "mechanics_run.py"
)
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


class TestBook:
    def test_one_lane_per_model_over_the_same_lines(self):
        jobs = mr.build_book(["claude-haiku-4-5", "gpt-5.6-luna"])
        assert len(jobs) == 2 * len(mr.LINES)
        assert {j.model for j in jobs} == {"claude-haiku-4-5", "gpt-5.6-luna"}

    def test_every_job_carries_a_ceiling(self):
        jobs = mr.build_book(["gpt-5.6-luna"], max_tokens=64)
        assert all(j.params["max_tokens"] == 64 for j in jobs)

    def test_the_default_ceiling_leaves_a_reasoning_model_room_to_answer(self):
        # A ceiling smaller than the reasoning buys a bill and an empty string.
        assert mr.DEFAULT_MAX_TOKENS >= 128

    def test_the_lines_are_the_work_not_filler(self):
        jobs = mr.build_book(["gpt-5.6-luna"])
        assert mr.LINES[0] in jobs[0].messages[0]["content"]


class TestCapGate:
    def _out(self, tmp_path):
        return str(tmp_path / "run")

    def test_a_book_under_the_cap_passes_and_still_submits_nothing_on_dry_run(
        self, tmp_path, capsys
    ):
        rc = mr.main(["--out", self._out(tmp_path), "--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "under cap" in out
        assert not (tmp_path / "run" / "handles.jsonl").exists()

    def test_a_book_over_the_cap_aborts_before_submitting(self, tmp_path, capsys):
        rc = mr.main(["--out", self._out(tmp_path), "--cap", "0.0000001"])
        assert rc == 2
        assert "ABORT" in capsys.readouterr().out
        assert not (tmp_path / "run" / "handles.jsonl").exists()

    def test_the_cap_counts_what_the_session_already_exposed(self, tmp_path, capsys):
        # The cap is on the total, so a small book still trips it once enough
        # has been spent around it.
        rc = mr.main(
            ["--out", self._out(tmp_path), "--models", "gpt-5.6-luna",
             "--cap", "0.01", "--already-spent", "0.0099"]
        )
        assert rc == 2
        assert "already exposed" in capsys.readouterr().out

    def test_the_gate_prices_the_ceiling_not_a_hope(self, tmp_path, capsys):
        # The quoted worst case must scale with the ceiling actually set.
        mr.main(["--out", self._out(tmp_path), "--max-tokens", "16", "--dry-run"])
        small = capsys.readouterr().out
        mr.main(["--out", str(tmp_path / "run2"), "--max-tokens", "512", "--dry-run"])
        large = capsys.readouterr().out

        def worst(text):
            line = next(ln for ln in text.splitlines() if ln.startswith("cap check"))
            return float(line.split("$")[1].split()[0])

        assert worst(large) > worst(small)


class TestRefusesToResubmit:
    def test_an_existing_handle_log_stops_the_run(self, tmp_path, capsys):
        out = tmp_path / "run"
        out.mkdir()
        (out / "handles.jsonl").write_text(
            json.dumps({"venue": "openai:batch", "handle": "batch_x", "jobs": 1}) + "\n"
        )
        rc = mr.main(["--out", str(out)])
        assert rc == 3
        assert "refusing to re-submit" in capsys.readouterr().out


class TestCancelPath:
    def test_cancelling_with_no_log_is_not_an_error(self, tmp_path, capsys):
        rc = mr.main(["--out", str(tmp_path / "nothing-here"), "--cancel"])
        assert rc == 0
        assert "no handles recorded" in capsys.readouterr().out

    def test_every_recorded_handle_is_cancelled_at_its_own_venue(self, tmp_path, capsys):
        log = tmp_path / "handles.jsonl"
        log.write_text(
            json.dumps({"venue": "openai:batch", "handle": "batch_x", "jobs": 1}) + "\n"
            + json.dumps({"venue": "anthropic:batch", "handle": "msgbatch_y", "jobs": 1}) + "\n"
        )
        cancelled = []

        class FakeVenue:
            def __init__(self, name):
                self.name = name

            def cancel(self, handle):
                cancelled.append((self.name, handle))

        mr.cancel_all(log, [FakeVenue("openai:batch"), FakeVenue("anthropic:batch")])
        assert cancelled == [("openai:batch", "batch_x"), ("anthropic:batch", "msgbatch_y")]

    def test_a_handle_from_an_unconfigured_venue_is_reported_not_skipped_silently(
        self, tmp_path, capsys
    ):
        log = tmp_path / "handles.jsonl"
        log.write_text(json.dumps({"venue": "groq:batch", "handle": "g_1", "jobs": 1}) + "\n")
        mr.cancel_all(log, [])
        assert "no venue to cancel with" in capsys.readouterr().out


@pytest.mark.parametrize("flag,attr,value", [
    ("--cap", "cap", 0.25),
    ("--max-tokens", "max_tokens", 32),
])
def test_the_guard_rails_are_all_settable(flag, attr, value):
    args = mr.parse_args([flag, str(value)])
    assert getattr(args, attr) == value
