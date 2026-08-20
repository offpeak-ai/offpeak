from datetime import datetime, timedelta, timezone

import pytest

from offpeak import parse_deadline, seconds_until

NOW = datetime(2026, 8, 20, 22, 0, 0, tzinfo=timezone.utc)


def test_wall_clock_rolls_to_tomorrow():
    deadline = parse_deadline("06:00", now=NOW)
    assert deadline == datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)


def test_wall_clock_later_today():
    now = datetime(2026, 8, 20, 5, 0, 0, tzinfo=timezone.utc)
    assert parse_deadline("06:00", now=now) == datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("45s", 45),
        ("90m", 5400),
        ("90 min", 5400),
        ("4h", 14400),
        ("4 hours", 14400),
        ("2d", 172800),
        ("1.5h", 5400),
    ],
)
def test_relative_forms(text, seconds):
    assert parse_deadline(text, now=NOW) == NOW + timedelta(seconds=seconds)


def test_native_forms():
    assert parse_deadline(3600, now=NOW) == NOW + timedelta(hours=1)
    assert parse_deadline(timedelta(hours=2), now=NOW) == NOW + timedelta(hours=2)
    absolute = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)
    assert parse_deadline(absolute, now=NOW) == absolute


def test_iso_string_with_offset():
    deadline = parse_deadline("2026-08-21T06:00:00-07:00", now=NOW)
    assert deadline == datetime.fromisoformat("2026-08-21T06:00:00-07:00")


def test_result_is_always_aware():
    for value in ("06:00", "4h", 60, timedelta(minutes=5)):
        assert parse_deadline(value, now=NOW).tzinfo is not None


def test_past_deadline_rejected():
    with pytest.raises(ValueError, match="not in the future"):
        parse_deadline(datetime(2020, 1, 1, tzinfo=timezone.utc), now=NOW)


def test_unrecognized_rejected():
    with pytest.raises(ValueError, match="unrecognized"):
        parse_deadline("whenever", now=NOW)
    with pytest.raises(ValueError):
        parse_deadline("25:00", now=NOW)
    with pytest.raises(TypeError):
        parse_deadline(True, now=NOW)


def test_seconds_until():
    deadline = NOW + timedelta(minutes=10)
    assert seconds_until(deadline, now=NOW) == 600.0
