"""Night-board tests — pure functions only, no network."""

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from offpeak.prices import BATCH_DISCOUNT as SDK_BATCH_DISCOUNT
from offpeak.prices import urgency_spread

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


def test_board_urgency_spread_tracks_the_sdk_price_sheet():
    # The 4x is a citable claim on a public page. It is duplicated here because
    # the Action does not install the SDK — so it has to be pinned to the sheet
    # the SDK settles receipts against, or the board could publish a stale one.
    assert nr.URGENCY_SPREAD == urgency_spread(nr.URGENCY_MODEL)


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

    def test_the_urgency_spread_is_recorded_with_its_source_and_caveat(self):
        # A published figure on the board has to carry where it came from and
        # what will move it, or the record is a number without provenance.
        tokens = self._rec()["tokens"]
        assert tokens["urgency_spread"] == 4.0
        assert nr.URGENCY_MODEL in tokens["urgency_note"]
        assert tokens["source"] == "developers.openai.com/api/docs/pricing"
        assert "2026-11-21" in tokens["caveat"]

    def test_the_board_header_cites_the_spread_it_prints(self):
        assert "4x its batch tier" in nr.BOARD_HEADER
        assert "developers.openai.com/api/docs/pricing" in nr.BOARD_HEADER
        assert "promotional at least through 2026-11-21" in nr.BOARD_HEADER


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
        # 2 windows x 2 GB legs, 2 GB spreads, 2 US price spreads, 2 US carbon
        assert row.count("—") == 10
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


class TestHourIntensity:
    def test_a_gas_only_hour_is_the_gas_factor(self):
        intensity, classified, unclassified = nr.hour_intensity({"NG": 1000.0})
        assert intensity == pytest.approx(nr.CO2_KG_PER_MWH["NG"])
        assert (classified, unclassified) == (1000.0, 0.0)

    def test_zero_carbon_generation_dilutes_it(self):
        # Half gas, half wind: the fleet factor, halved.
        intensity, _, _ = nr.hour_intensity({"NG": 500.0, "WND": 500.0})
        assert intensity == pytest.approx(nr.CO2_KG_PER_MWH["NG"] / 2)

    def test_a_carbon_free_hour_is_zero_not_none(self):
        intensity, _, _ = nr.hour_intensity({"WND": 100.0, "SUN": 50.0})
        assert intensity == 0.0

    def test_an_hour_with_nothing_classified_has_no_intensity(self):
        # A grid we cannot characterise has no intensity, not one of zero.
        intensity, classified, unclassified = nr.hour_intensity({"OTH": 900.0})
        assert intensity is None
        assert (classified, unclassified) == (0.0, 900.0)

    def test_storage_charging_is_load_not_negative_generation(self):
        # BAT at -400 is demand wearing a generator's name. Netting it against
        # gas would invent carbon-free MWh that nobody generated.
        with_charge, _, _ = nr.hour_intensity({"NG": 1000.0, "BAT": -400.0})
        assert with_charge == pytest.approx(nr.CO2_KG_PER_MWH["NG"])

    def test_an_unknown_fuel_code_is_unclassified_not_assumed_clean(self):
        _, classified, unclassified = nr.hour_intensity({"NG": 100.0, "XYZ": 900.0})
        assert (classified, unclassified) == (100.0, 900.0)

    def test_the_factors_are_derived_from_their_published_halves(self):
        # Coal: 205.7 lb CO2/MMBtu x 10 MMBtu/MWh, in kg. Roughly 933.
        assert nr.CO2_KG_PER_MWH["COL"] == pytest.approx(933.0, abs=1.0)
        assert nr.CO2_KG_PER_MWH["NG"] == pytest.approx(424.6, abs=1.0)
        assert nr.CO2_KG_PER_MWH["COL"] > nr.CO2_KG_PER_MWH["NG"]


class TestUsCarbonLeg:
    def _mix(self, night, peak_ng, offpeak_ng):
        nxt = night + dt.timedelta(days=1)
        mix = {}
        for i, ng in enumerate(peak_ng):
            mix[(night, 17 + i)] = {"NG": ng, "WND": 1000.0 - ng}
        for i, ng in enumerate(offpeak_ng):
            mix[(nxt, i)] = {"NG": ng, "WND": 1000.0 - ng}
        return mix

    def test_windows_match_the_price_legs_and_carry_their_unit(self):
        night = dt.date(2026, 8, 20)
        leg = nr.us_carbon_leg(self._mix(night, [800.0], [200.0]), night)
        assert leg["unit"] == "gCO2/kWh"
        assert leg["window_spread"] == 4.0  # 0.8 gas vs 0.2 gas
        assert leg["basis"] == "derived"
        assert "EIA-930" in leg["method"]

    def test_the_unclassified_share_is_reported_not_buried(self):
        night = dt.date(2026, 8, 20)
        mix = {(night, 17): {"NG": 750.0, "OTH": 250.0}}
        leg = nr.us_carbon_leg(mix, night)
        assert leg["unclassified_share"] == 0.25

    def test_a_night_with_no_usable_hours_is_no_leg(self):
        assert nr.us_carbon_leg({}, dt.date(2026, 8, 20)) is None

    def test_the_leg_lands_in_the_record_and_the_row(self):
        night = dt.date(2026, 8, 20)
        rec = nr.build_record(
            mode="mark", night=night, now=utc(2026, 8, 21, 6, 30),
            span=(utc(2026, 8, 20, 16), utc(2026, 8, 21, 7)),
            carbon_series=None, power_series=None, errors={},
            us_carbon_legs={
                "ercot_houston": nr.us_carbon_leg(self._mix(night, [900.0], [300.0]), night)
            },
        )
        assert rec["carbon_ercot_houston"]["window_spread"] == 3.0
        assert "3.0x" in nr.render_row(rec)
        assert "EIA-930" in rec["sources"]["carbon_us"]


class TestEiaMix:
    def test_periods_are_parsed_on_the_zones_own_clock(self, monkeypatch):
        payload = {"response": {"data": [
            {"period": "2026-08-20T17-05", "fueltype": "NG", "value": "500"},
            {"period": "2026-08-20T17-05", "fueltype": "WND", "value": "500"},
            {"period": "2026-08-21T02-05", "fueltype": "NG", "value": "100"},
        ]}}
        monkeypatch.setattr(nr, "get_json", lambda url, **kw: payload)
        mix = nr.eia_mix("ercot_houston", dt.date(2026, 8, 20), "k")
        assert mix[(dt.date(2026, 8, 20), 17)] == {"NG": 500.0, "WND": 500.0}
        assert mix[(dt.date(2026, 8, 21), 2)] == {"NG": 100.0}

    def test_null_and_malformed_rows_are_skipped_not_fatal(self, monkeypatch):
        payload = {"response": {"data": [
            {"period": "2026-08-20T17-05", "fueltype": "NG", "value": None},
            {"period": "garbage", "fueltype": "NG", "value": "5"},
            {"period": "2026-08-20T17-05", "fueltype": "COL", "value": "7"},
        ]}}
        monkeypatch.setattr(nr, "get_json", lambda url, **kw: payload)
        assert nr.eia_mix("ercot_houston", dt.date(2026, 8, 20), "k") == {
            (dt.date(2026, 8, 20), 17): {"COL": 7.0}
        }

    def test_an_empty_feed_says_it_runs_behind_rather_than_marking_zero(self, monkeypatch):
        monkeypatch.setattr(nr, "get_json", lambda url, **kw: {"response": {"data": []}})
        with pytest.raises(nr.FetchError, match="about a day behind"):
            nr.eia_mix("caiso_sp15", dt.date(2026, 8, 20), "k")

    def test_the_api_key_never_reaches_an_error_message(self, monkeypatch):
        # get_json already strips query strings; this pins that the key rides
        # in one, so a failed EIA call cannot leak it into a public Action log.
        seen = {}

        def capture(url, **kw):
            seen["url"] = url
            raise nr.FetchError("boom")

        monkeypatch.setattr(nr, "get_json", capture)
        with pytest.raises(nr.FetchError):
            nr.eia_mix("caiso_sp15", dt.date(2026, 8, 20), "hunter2")
        assert "hunter2" in seen["url"].split("?", 1)[1]
        assert "hunter2" not in seen["url"].split("?", 1)[0]


class TestUtcOffset:
    def test_summer_and_winter_offsets_differ_like_the_grid_does(self):
        assert nr.utc_offset("America/Chicago", dt.date(2026, 8, 20)) == "-05:00"
        assert nr.utc_offset("America/Chicago", dt.date(2026, 1, 20)) == "-06:00"
        assert nr.utc_offset("America/Los_Angeles", dt.date(2026, 8, 20)) == "-07:00"

    def test_an_unknown_zone_costs_the_column_not_the_run(self):
        with pytest.raises(nr.FetchError):
            nr.utc_offset("Mars/Olympus_Mons", dt.date(2026, 8, 20))


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
