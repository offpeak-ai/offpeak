"""Night-board tests — pure functions only, no network."""

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from offpeak.prices import BATCH_DISCOUNT as SDK_BATCH_DISCOUNT

_spec = importlib.util.spec_from_file_location(
    "night_report", Path(__file__).resolve().parent.parent / "tools" / "night_report.py"
)
nr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nr)


def utc(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc)


def series(*pairs):
    return [(f"2026-08-20T{h:02d}:{mi:02d}Z", v) for h, mi, v in pairs]


def test_board_discount_tracks_the_sdk_price_sheet():
    # The board prints a token spread; it must not drift from what offpeak bills.
    assert nr.BATCH_DISCOUNT == SDK_BATCH_DISCOUNT


class TestNightSpan:
    def test_quote_at_dusk_covers_peak_through_dawn(self):
        f, t = nr.night_span(utc(2026, 8, 20, 19), "quote")
        assert (nr.z(f), nr.z(t)) == ("2026-08-20T16:00Z", "2026-08-21T07:00Z")

    def test_span_covers_both_windows_the_board_compares(self):
        # The bug this replaced: a forward-only window from 19:00Z missed the
        # 16-20Z peak entirely, so the peak column was null every night.
        f, t = nr.night_span(utc(2026, 8, 20, 19), "quote")
        span_hours = int((t - f).total_seconds() // 3600)
        hours = {(f + dt.timedelta(hours=i)).hour for i in range(span_hours)}
        assert hours & set(range(*nr.PEAK_WINDOW_UTC))
        assert hours & ({23} | set(range(0, nr.OFFPEAK_WINDOW_UTC[1])))

    def test_mark_at_dawn_looks_back_at_the_finished_night(self):
        f, t = nr.night_span(utc(2026, 8, 21, 6, 30), "mark")
        assert nr.z(f) == "2026-08-20T16:00Z"
        assert f.date() == dt.date(2026, 8, 20)  # the night is named for its dusk

    def test_mark_never_reaches_into_the_future(self):
        now = utc(2026, 8, 21, 6, 30)
        _, t = nr.night_span(now, "mark")
        assert t <= now  # actuals do not exist yet

    def test_a_run_before_dusk_still_belongs_to_the_previous_night(self):
        f, _ = nr.night_span(utc(2026, 8, 21, 4), "quote")
        assert nr.z(f) == "2026-08-20T16:00Z"


class TestWindow:
    def test_plain_range_is_half_open(self):
        assert nr.window(series((16, 0, 10.0), (19, 30, 20.0), (20, 0, 999.0)), 16, 20) == 15.0

    def test_range_wraps_midnight(self):
        s = series((23, 0, 10.0), (0, 30, 20.0), (3, 30, 30.0), (12, 0, 999.0))
        assert nr.window(s, 23, 4) == 20.0

    def test_empty_selection_is_none_not_zero(self):
        assert nr.window(series((12, 0, 5.0)), 16, 20) is None

    def test_malformed_timestamps_are_skipped_not_fatal(self):
        assert nr.window([("garbage", 1.0), ("2026-08-20T17:00Z", 8.0)], 16, 20) == 8.0


class TestBestWorst5h:
    def test_needs_a_full_five_hours(self):
        assert nr.best_worst_5h(series(*[(h, 0, 1.0) for h in range(9)])) is None

    def test_finds_cleanest_and_dirtiest_windows(self):
        vals = [10.0] * 10 + [90.0] * 10
        s = [(f"2026-08-20T{i // 2:02d}:{(i % 2) * 30:02d}Z", v) for i, v in enumerate(vals)]
        (best_avg, best_from), (worst_avg, worst_from) = nr.best_worst_5h(s)
        assert best_avg == 10.0 and worst_avg == 90.0
        assert best_from < worst_from


class TestGetJsonRetries:
    def test_a_blip_is_retried_rather_than_fatal(self, monkeypatch):
        calls, slept = [], []

        def flaky(url, timeout=None):
            calls.append(url)
            raise OSError("connection reset")

        monkeypatch.setattr(nr.urllib.request, "urlopen", flaky)
        with pytest.raises(nr.FetchError):
            nr.get_json("https://example.test/x", retries=3, sleep=slept.append)
        assert len(calls) == 3  # did not give up on the first blip
        assert len(slept) == 2  # backed off between attempts, not after the last

    def test_failure_is_a_fetcherror_not_a_raw_socket_error(self, monkeypatch):
        def down(url, timeout=None):
            raise OSError("down")

        monkeypatch.setattr(nr.urllib.request, "urlopen", down)
        with pytest.raises(nr.FetchError, match="failed after"):
            nr.get_json("https://example.test/y?secret=1", retries=1, sleep=lambda _: None)

    def test_query_string_is_not_echoed_into_the_error(self, monkeypatch):
        # Board URLs are keyless today, but an error string can end up in a
        # public Action log — do not start the habit of pasting query params.
        def down(url, timeout=None):
            raise OSError("down")

        monkeypatch.setattr(nr.urllib.request, "urlopen", down)
        with pytest.raises(nr.FetchError) as excinfo:
            nr.get_json("https://example.test/y?secret=hunter2", retries=1, sleep=lambda _: None)
        assert "hunter2" not in str(excinfo.value)


class TestBuildRecord:
    def _rec(self, **kw):
        base = dict(
            mode="mark",
            night=dt.date(2026, 8, 20),
            now=utc(2026, 8, 21, 6, 30),
            span=(utc(2026, 8, 20, 16), utc(2026, 8, 21, 7)),
            carbon_series=None,
            power_series=None,
            errors={},
        )
        base.update(kw)
        return nr.build_record(**base)

    def test_one_dead_leg_does_not_sink_the_other(self):
        rec = self._rec(
            power_series=series((17, 0, 30.0), (1, 0, 15.0)),
            errors={"carbon": "api down"},
        )
        assert rec["power_gb_agile"]["window_spread"] == 2.0
        assert "carbon_gb" not in rec
        assert rec["unavailable"] == {"carbon": "api down"}

    def test_record_is_json_serializable(self):
        rec = self._rec(power_series=series((17, 0, 30.0), (1, 0, 15.0)))
        assert json.loads(json.dumps(rec))["night_of"] == "2026-08-20"

    def test_spread_is_none_rather_than_dividing_by_a_missing_window(self):
        rec = self._rec(power_series=series((17, 0, 30.0)))  # peak only, no offpeak
        assert rec["power_gb_agile"]["window_spread"] is None

    def test_token_spread_is_derived_from_the_discount(self):
        assert self._rec()["tokens"]["spread"] == 2.0


class TestBoard:
    def test_creates_header_then_appends(self, tmp_path):
        board = tmp_path / "BOARD.md"
        nr.upsert_board_row(board, "2026-08-19", "| 2026-08-19 | a |\n")
        nr.upsert_board_row(board, "2026-08-20", "| 2026-08-20 | b |\n")
        text = board.read_text()
        assert text.startswith("# Offpeak night board")
        assert text.count("| 2026-08-19 |") == 1
        assert text.rstrip().endswith("| 2026-08-20 | b |")

    def test_re_marking_a_night_corrects_it_instead_of_duplicating(self, tmp_path):
        board = tmp_path / "BOARD.md"
        nr.upsert_board_row(board, "2026-08-20", "| 2026-08-20 | first |\n")
        nr.upsert_board_row(board, "2026-08-20", "| 2026-08-20 | corrected |\n")
        text = board.read_text()
        assert text.count("| 2026-08-20 |") == 1
        assert "corrected" in text and "first" not in text

    def test_missing_values_render_as_a_dash_not_a_crash(self):
        row = nr.render_row({"night_of": "2026-08-20", "tokens": {"spread": 2.0}})
        assert row.count("—") == 8  # 2 windows x 2 GB legs, 2 GB spreads, 2 US spreads
        assert row.endswith("2.0x |\n")


class TestUsZones:
    def _rows(self, night, peak_vals, offpeak_vals, tz_hours=-7):
        tz = dt.timezone(dt.timedelta(hours=tz_hours))
        rows = []
        for i, v in enumerate(peak_vals):  # evening of the night's own date
            rows.append((dt.datetime(night.year, night.month, night.day, 17 + i, tzinfo=tz), v))
        nxt = night + dt.timedelta(days=1)
        for i, v in enumerate(offpeak_vals):  # small hours of the morning after
            rows.append((dt.datetime(nxt.year, nxt.month, nxt.day, i, tzinfo=tz), v))
        return rows

    def test_windows_are_read_off_the_zones_own_clock(self):
        night = dt.date(2026, 8, 20)
        leg = nr.us_leg(self._rows(night, [100.0, 120.0], [20.0, 30.0]), night)
        assert leg["peak_window_17_21_local"] == 110.0
        assert leg["offpeak_window_00_05_local"] == 25.0
        assert leg["window_spread"] == 4.4

    def test_offpeak_is_the_morning_after_not_the_same_morning(self):
        # The whole point of the board: the evening peak against the trough
        # that follows it, not the one twelve hours before it.
        night = dt.date(2026, 8, 20)
        rows = self._rows(night, [100.0], [20.0])
        tz = dt.timezone(dt.timedelta(hours=-7))
        rows.append((dt.datetime(2026, 8, 20, 2, tzinfo=tz), 999.0))  # same-day trough
        leg = nr.us_leg(rows, night)
        assert leg["offpeak_window_00_05_local"] == 20.0  # 999 ignored

    def test_a_zone_with_no_usable_hours_is_no_leg_at_all(self):
        assert nr.us_leg([], dt.date(2026, 8, 20)) is None

    def test_half_a_zone_still_reports_what_it_has(self):
        night = dt.date(2026, 8, 20)
        leg = nr.us_leg(self._rows(night, [100.0], []), night)
        assert leg["peak_window_17_21_local"] == 100.0
        assert leg["offpeak_window_00_05_local"] is None
        assert leg["window_spread"] is None

    def test_missing_gridstatus_is_a_fetcherror_not_an_importerror(self, monkeypatch):
        # US zones are an Action-only dependency; absence must cost the column,
        # not the run.
        import builtins

        real_import = builtins.__import__

        def no_gridstatus(name, *a, **k):
            if name == "gridstatus":
                raise ImportError("no module named gridstatus")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_gridstatus)
        with pytest.raises(nr.FetchError, match="gridstatus is not installed"):
            nr._us_rows("caiso_sp15", dt.date(2026, 8, 20))

    def test_us_legs_land_in_the_record_and_the_row(self):
        night = dt.date(2026, 8, 20)
        rec = nr.build_record(
            mode="mark", night=night, now=utc(2026, 8, 21, 6, 30),
            span=(utc(2026, 8, 20, 16), utc(2026, 8, 21, 7)),
            carbon_series=None, power_series=None, errors={},
            us_legs={"caiso_sp15": nr.us_leg(self._rows(night, [100.0], [25.0]), night)},
        )
        assert rec["power_caiso_sp15"]["window_spread"] == 4.0
        assert "4.0x" in nr.render_row(rec)


class TestBoardHeaderHealing:
    def test_a_stale_header_is_repaired_and_rows_are_kept(self, tmp_path):
        # Adding a column to the generator must repair an existing board, not
        # leave rows that no longer line up with the header above them.
        board = tmp_path / "BOARD.md"
        board.write_text(
            "# Offpeak night board — marked nights\n\n"
            "| night | old | columns |\n|---|---|---|\n"
            "| 2026-08-19 | a | b |\n"
        )
        nr.upsert_board_row(board, "2026-08-20", "| 2026-08-20 | new |\n")
        text = board.read_text()
        assert text.startswith(nr.BOARD_HEADER)
        assert "| old | columns |" not in text
        assert "| 2026-08-19 | a | b |" in text  # history survives the repair
        assert "| 2026-08-20 | new |" in text

    def test_repair_is_idempotent(self, tmp_path):
        board = tmp_path / "BOARD.md"
        for _ in range(3):
            nr.upsert_board_row(board, "2026-08-20", "| 2026-08-20 | x |\n")
        assert board.read_text().count("| 2026-08-20 |") == 1
        assert board.read_text().count("# Offpeak night board") == 1
