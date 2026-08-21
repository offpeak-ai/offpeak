"""Deadline parsing — how software says "this can wait".

Accepted forms:

- ``datetime`` — aware or naive; a naive datetime is assumed to be local time.
- ``timedelta`` — relative to now.
- ``int`` / ``float`` — seconds from now.
- ``"06:00"`` — the next occurrence of that wall-clock time (today if it is
  still ahead, otherwise tomorrow). This is the canonical overnight form.
- ``"6h"``, ``"90m"``, ``"45s"``, ``"2d"`` — relative to now.
- ISO 8601 strings — ``"2026-08-21T06:00:00-07:00"``, including the
  ``Z`` (UTC) suffix on every supported Python.

All deadlines resolve to an aware :class:`datetime.datetime`. See SPEC.md for
the full semantics.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

__all__ = ["parse_deadline", "seconds_until"]

_REL = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?)\s*$",
    re.IGNORECASE,
)
_WALL = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")
_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def _local_now() -> datetime:
    return datetime.now().astimezone()


def parse_deadline(value: object, *, now: datetime | None = None) -> datetime:
    """Resolve *value* to an aware datetime.

    Raises ``ValueError`` if the form is unrecognized or the resolved deadline
    is not in the future, and ``TypeError`` for unsupported types.
    """
    if now is None:
        now = _local_now()
    elif now.tzinfo is None:
        now = now.astimezone()
    deadline = _parse(value, now)
    if deadline <= now:
        raise ValueError(
            f"deadline {deadline.isoformat()} is not in the future (now: {now.isoformat()})"
        )
    return deadline


def seconds_until(deadline: datetime, *, now: datetime | None = None) -> float:
    """Seconds remaining until *deadline* (negative if it has passed)."""
    if now is None:
        now = _local_now()
    return (deadline - now).total_seconds()


def _parse(value: object, now: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    if isinstance(value, timedelta):
        return now + value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return now + timedelta(seconds=float(value))
    if isinstance(value, str):
        if m := _REL.match(value):
            qty = float(m.group(1))
            unit = m.group(2)[0].lower()
            return now + timedelta(seconds=qty * _UNIT_SECONDS[unit])
        if m := _WALL.match(value):
            hour, minute = int(m.group(1)), int(m.group(2))
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate
        text = value.strip()
        # Python 3.11 taught fromisoformat to read a trailing "Z"; 3.10 did not,
        # and Z is the ISO form most timestamps in the wild actually use.
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"unrecognized deadline: {value!r}") from None
        return parsed if parsed.tzinfo else parsed.astimezone()
    raise TypeError(f"unsupported deadline type: {type(value).__name__}")
