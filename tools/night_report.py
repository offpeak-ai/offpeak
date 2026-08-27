#!/usr/bin/env python3
"""Offpeak Spread Board generator — quotes at the open, marks at the close.

Open data, zero venue spend. GB legs: NESO carbon intensity (forecast at the
open, actual at the close) and Octopus Agile day-ahead power, both keyless. US legs: CAISO
SP15 and ERCOT Houston day-ahead power via gridstatus, keyless, and a derived
carbon intensity from EIA-930's hourly generation mix, which wants a free
EIA_API_KEY in the environment. Every leg is optional; a missing key or a late
feed costs that column and nothing else.

Run as two passes over the same session:

    night_report.py --mode quote   # ~19:00Z, the session ahead, carbon forecast
    night_report.py --mode mark    # ~06:30Z, the session just finished, actuals

A "session" is 16:00Z-07:00Z — it opens with the 17:00 BST evening peak and
closes after the 00-05 BST trough, so a single span carries both windows the
board compares. Anchoring to the clock rather than to "now + 12h" is what keeps
the peak column populated: a quote fired at 19:00Z with a forward-only window
would have already missed the peak it is meant to price against.

Every leg degrades independently. A source that is down, rate-limited or still
publishing nulls costs you that column, never the run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CI_API = "https://api.carbonintensity.org.uk/intensity/{f}/{t}"
EIA_API = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
AGILE = (
    "https://api.octopus.energy/v1/products/AGILE-24-10-01/"
    "electricity-tariffs/E-1R-AGILE-24-10-01-C/standard-unit-rates/"
    "?period_from={f}&period_to={t}&page_size=1500"
)

# Published token spreads. Kept in sync with offpeak.prices by
# test_night_report.py rather than by hand: the Action runs this script without
# installing the SDK, so these are duplicated on purpose, not importable.
BATCH_DISCOUNT = 0.5

# The intra-venue urgency spread: the same model at one venue, priced for haste
# against priced for patience. OpenAI's fast tier over its batch tier on
# gpt-5.6-sol is $8/$40 per 1M tokens against $2/$10 — 4x for the hour alone.
URGENCY_MODEL = "gpt-5.6-sol"
URGENCY_SPREAD = 4.0
URGENCY_LEGS = "$8/$40 vs $2/$10 per 1M"
TOKEN_SOURCE = "developers.openai.com/api/docs/pricing"
PROMO_CAVEAT = (
    "gpt-5.6-sol's standard rate is promotional at least through 2026-11-21; "
    "post-promo list is $5/$30 and both tiers move with it, so the ratio is the "
    "durable figure, not the dollars"
)

NIGHT_START_HOUR_UTC = 16  # 17:00 BST — the evening peak opens
NIGHT_HOURS = 15  # ...through 07:00Z
PEAK_WINDOW_UTC = (16, 20)  # 17-21 BST
OFFPEAK_WINDOW_UTC = (23, 4)  # 00-05 BST, wraps midnight

# US zones price in their own clock, so their windows are local hours rather
# than UTC ones: the evening peak of the session's date, and the trough of
# the morning after — the same shape as the GB legs, read off a different clock.
PEAK_WINDOW_LOCAL = (17, 21)
OFFPEAK_WINDOW_LOCAL = (0, 5)

# (label, gridstatus call) — day-ahead hourly, keyless, no account required.
US_ZONES = {
    "caiso_sp15": {"iso": "CAISO", "node": "TH_SP15_GEN-APND", "unit": "$/MWh"},
    "ercot_houston": {"iso": "Ercot", "node": "HB_HOUSTON", "unit": "$/MWh"},
}

# The balancing authority behind each zone's price node, and the clock it keeps.
# A hub prices congestion at a point; the fuel burned to serve it is the whole
# BA's, so the carbon leg is BA-wide and says so. EIA's local-hourly feed wants
# a UTC offset on the window, which is why the zone carries a timezone name
# rather than a fixed offset — it moves with daylight saving like the grid does.
EIA_BA = {
    "caiso_sp15": {"ba": "CISO", "tz": "America/Los_Angeles"},
    "ercot_houston": {"ba": "ERCO", "tz": "America/Chicago"},
}

# EIA does not publish hourly carbon, so this derives it: EIA's own CO2
# emission coefficients (lb CO2 per MMBtu of fuel) times EIA fleet-average heat
# rates (MMBtu of fuel per MWh generated). Both halves are published and both
# are kept here rather than a single opaque factor, so the number can be
# re-derived instead of trusted.
LB_CO2_PER_MMBTU = {"COL": 205.7, "NG": 117.0, "OIL": 161.3}
HEAT_RATE_MMBTU_PER_MWH = {"COL": 10.0, "NG": 8.0, "OIL": 10.8}
KG_PER_LB = 0.45359237
CO2_KG_PER_MWH = {
    fuel: LB_CO2_PER_MMBTU[fuel] * HEAT_RATE_MMBTU_PER_MWH[fuel] * KG_PER_LB
    for fuel in LB_CO2_PER_MMBTU
}
# Generation that burns nothing. BAT is grid storage: discharging carries the
# carbon of whatever charged it, which this method cannot see, so it counts as
# zero at the margin and the limitation is documented rather than hidden.
ZERO_CARBON_FUELS = frozenset({"NUC", "WAT", "WND", "SUN", "GEO", "BAT"})
# EIA's catch-all bucket. No fuel is named, and it is frequently negative
# (storage charging, net imports), so it is excluded from the intensity and
# reported as a share instead of quietly averaged in.
UNCLASSIFIED_FUELS = frozenset({"OTH"})

CARBON_METHOD = (
    "derived: EIA-930 hourly generation mix x (EIA CO2 coefficients x EIA "
    "fleet heat rates); generation-side only, imports and storage carry-over "
    "not accounted"
)

HALF_HOURS_IN_5H = 10

RETRIES = 3
TIMEOUT_S = 30


class FetchError(RuntimeError):
    """A data leg was unavailable. One leg failing must not sink the report."""


def get_json(url: str, *, retries: int = RETRIES, timeout: int = TIMEOUT_S, sleep=time.sleep):
    """GET and parse JSON, retrying transient failures with a backoff."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last = exc
            if attempt < retries:
                sleep(2**attempt)
    raise FetchError(f"{url.split('?')[0]} failed after {retries} attempts: {last}")


def z(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def night_span(
    now: dt.datetime, mode: str, night: dt.date | None = None
) -> tuple[dt.datetime, dt.datetime]:
    """The (from, to) UTC span of the session this run is about.

    ``mark`` truncates at *now* — actuals do not exist for the future.

    Without *night* the session is inferred from the clock, which is right on
    the cron and wrong off it: the inference flips at 16:00Z, so a mark that
    runs late — a catch-up after GitHub dropped the scheduled one — marks the
    session that has just *started* rather than the one that just finished,
    and writes a row of empty spreads over a night nobody measured. Passing
    *night* states the session instead of guessing it, which is what makes a
    late run a repair rather than a second failure.
    """
    if night is not None:
        start = dt.datetime(
            night.year, night.month, night.day, NIGHT_START_HOUR_UTC, tzinfo=dt.timezone.utc
        )
    else:
        anchor = now.replace(minute=0, second=0, microsecond=0)
        start = anchor.replace(hour=NIGHT_START_HOUR_UTC)
        if anchor.hour < NIGHT_START_HOUR_UTC:
            start -= dt.timedelta(days=1)
    end = start + dt.timedelta(hours=NIGHT_HOURS)
    if mode == "mark":
        end = min(end, now)
    return start, end


def default_night(now: dt.datetime, mode: str) -> dt.date:
    """The session a run started at *now* is about, when nobody said."""
    return night_span(now, mode)[0].date()


def carbon(f: dt.datetime, t: dt.datetime, field: str) -> list[tuple[str, float]]:
    data = get_json(CI_API.format(f=z(f), t=z(t))).get("data") or []
    return [
        (d["from"], d["intensity"][field])
        for d in data
        if isinstance(d.get("intensity"), dict) and d["intensity"].get(field) is not None
    ]


def agile(f: dt.datetime, t: dt.datetime) -> list[tuple[str, float]]:
    data = get_json(AGILE.format(f=z(f), t=z(t))).get("results") or []
    return sorted(
        (r["valid_from"], r["value_inc_vat"]) for r in data if r.get("value_inc_vat") is not None
    )


def _us_rows(zone: str, night: dt.date) -> list[tuple[dt.datetime, float]]:
    """Day-ahead hourly prices for *zone*, covering the session and the morning
    after. Raises :class:`FetchError` when gridstatus is absent or the ISO is
    unreachable — the caller degrades that into a missing column.
    """
    try:
        import gridstatus
    except ImportError as exc:
        raise FetchError(
            "gridstatus is not installed (US zones are an Action-only dependency)"
        ) from exc

    cfg = US_ZONES[zone]
    rows: list[tuple[dt.datetime, float]] = []
    failures: list[str] = []
    # Fetch a day at a time. ERCOT's range form returns a single day, which
    # silently nulls the evening peak and leaves only the following morning.
    for day in (night, night + dt.timedelta(days=1)):
        try:
            iso = getattr(gridstatus, cfg["iso"])()
            if cfg["iso"] == "CAISO":
                df = iso.get_lmp(
                    date=day, market="DAY_AHEAD_HOURLY", locations=[cfg["node"]]
                )
                value_col = "LMP"
            else:
                df = iso.get_spp(
                    date=day, market="DAY_AHEAD_HOURLY", location_type="Trading Hub"
                )
                df = df[df["Location"] == cfg["node"]]
                value_col = "SPP"
        except Exception as exc:  # noqa: BLE001 — any ISO failure is a missing day
            failures.append(f"{day}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        rows += [
            (ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, float(val))
            for ts, val in zip(df["Interval Start"], df[value_col], strict=False)
        ]

    if not rows:
        raise FetchError(f"{zone}: no day-ahead rows ({'; '.join(failures) or 'empty'})")
    return rows


def us_leg(
    rows: list[tuple[dt.datetime, float]], night: dt.date, unit: str = "$/MWh"
) -> dict | None:
    """Peak/offpeak windows for a US zone, in that zone's local clock."""
    following = night + dt.timedelta(days=1)
    peak = [
        v for ts, v in rows
        if ts.date() == night and PEAK_WINDOW_LOCAL[0] <= ts.hour < PEAK_WINDOW_LOCAL[1]
    ]
    offpeak = [
        v for ts, v in rows
        if ts.date() == following
        and OFFPEAK_WINDOW_LOCAL[0] <= ts.hour < OFFPEAK_WINDOW_LOCAL[1]
    ]
    if not peak and not offpeak:
        return None
    peak_mean = round(statistics.mean(peak), 2) if peak else None
    offpeak_mean = round(statistics.mean(offpeak), 2) if offpeak else None
    return {
        "n_hours": len(rows),
        "peak_window_17_21_local": peak_mean,
        "offpeak_window_00_05_local": offpeak_mean,
        "window_spread": (
            round(peak_mean / offpeak_mean, 2) if peak_mean and offpeak_mean else None
        ),
        "unit": unit,
    }


_EIA_PERIOD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})([+-]\d{2})$")


def hour_intensity(mix: dict[str, float]) -> tuple[float | None, float, float]:
    """(kg CO2 per MWh, classified MWh, unclassified MWh) for one hour's mix.

    Negative net generation is storage charging — load wearing a generator's
    name — and is dropped from both sums rather than netted against real
    output. An hour with nothing classified returns ``None``: a grid we cannot
    characterise has no intensity, not an intensity of zero.
    """
    emitted = classified = unclassified = 0.0
    for fuel, mwh in mix.items():
        if mwh <= 0:
            continue
        if fuel in UNCLASSIFIED_FUELS:
            unclassified += mwh
            continue
        if fuel in CO2_KG_PER_MWH:
            emitted += mwh * CO2_KG_PER_MWH[fuel]
            classified += mwh
        elif fuel in ZERO_CARBON_FUELS:
            classified += mwh
        else:  # a fuel code EIA added since this table was written
            unclassified += mwh
    if not classified:
        return None, classified, unclassified
    return emitted / classified, classified, unclassified


def utc_offset(tz_name: str, night: dt.date) -> str:
    """The zone's UTC offset on *night*, as EIA wants it written (``-05:00``).

    Read at local noon so a session that straddles a daylight-saving change is
    stamped with the offset the grid actually ran on, not the one the clock
    happened to show at midnight.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError as exc:  # pragma: no cover - stdlib since 3.9
        raise FetchError(f"no timezone database available: {exc}") from exc
    try:
        noon = dt.datetime(night.year, night.month, night.day, 12, tzinfo=ZoneInfo(tz_name))
    except Exception as exc:  # noqa: BLE001 — a missing tzdata is a dead column
        raise FetchError(f"{tz_name}: {type(exc).__name__}: {exc}") from exc
    stamp = noon.strftime("%z")
    return f"{stamp[:3]}:{stamp[3:]}"


def eia_mix(
    zone: str, night: dt.date, api_key: str
) -> dict[tuple[dt.date, int], dict[str, float]]:
    """Hourly generation by fuel for *zone*'s balancing authority, local clock.

    Asks for the session's evening through the following morning in local hours,
    which is the same span the price legs read. Raises :class:`FetchError` so a
    missing key or a late EIA publication costs the column, not the run.
    """
    cfg = EIA_BA[zone]
    offset = utc_offset(cfg["tz"], night)
    following = night + dt.timedelta(days=1)
    query = urllib.parse.urlencode(
        [
            ("api_key", api_key),
            ("frequency", "local-hourly"),
            ("data[]", "value"),
            ("facets[respondent][]", cfg["ba"]),
            ("start", f"{night}T00{offset}"),
            ("end", f"{following}T23{offset}"),
            ("length", "5000"),
        ]
    )
    payload = get_json(f"{EIA_API}?{query}")
    rows = (payload.get("response") or {}).get("data") or []
    mix: dict[tuple[dt.date, int], dict[str, float]] = {}
    for row in rows:
        stamp = _EIA_PERIOD.match(str(row.get("period", "")))
        if not stamp or row.get("value") is None:
            continue
        y, mo, d, hour, _offset = stamp.groups()
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            continue
        key = (dt.date(int(y), int(mo), int(d)), int(hour))
        mix.setdefault(key, {})[str(row.get("fueltype"))] = value
    if not mix:
        raise FetchError(
            f"{cfg['ba']}: EIA-930 has published no hours for {night} yet "
            "(the feed runs about a day behind)"
        )
    return mix


def us_carbon_leg(
    mix: dict[tuple[dt.date, int], dict[str, float]], night: dt.date
) -> dict | None:
    """The carbon counterpart to :func:`us_leg`, on the same local windows."""
    rows: list[tuple[dt.datetime, float]] = []
    unclassified = classified = 0.0
    for (day, hour), hour_mix in sorted(mix.items()):
        intensity, ok_mwh, other_mwh = hour_intensity(hour_mix)
        classified += ok_mwh
        unclassified += other_mwh
        if intensity is not None:
            rows.append((dt.datetime(day.year, day.month, day.day, hour), intensity))
    leg = us_leg(rows, night, unit="gCO2/kWh")
    if leg is None:
        return None
    total = classified + unclassified
    leg["basis"] = "derived"
    leg["method"] = CARBON_METHOD
    leg["unclassified_share"] = round(unclassified / total, 3) if total else None
    return leg


def best_worst_5h(series: list[tuple[str, float]]):
    """Cleanest and dirtiest rolling 5-hour windows, as ((avg, from), (avg, from))."""
    vals = [v for _, v in series]
    if len(vals) < HALF_HOURS_IN_5H:
        return None
    wins = [
        (statistics.mean(vals[i : i + HALF_HOURS_IN_5H]), series[i][0])
        for i in range(len(vals) - HALF_HOURS_IN_5H + 1)
    ]
    return min(wins), max(wins)


def window(series: list[tuple[str, float]], h0: int, h1: int) -> float | None:
    """Mean over the UTC-hour range [h0, h1); wraps midnight when h0 > h1."""
    sel = []
    for iso, v in series:
        try:
            hour = int(iso[11:13])
        except (ValueError, IndexError):
            continue
        if (h0 <= hour < h1) if h0 < h1 else (hour >= h0 or hour < h1):
            sel.append(v)
    return round(statistics.mean(sel), 2) if sel else None


def _leg(series, unit_extras=None) -> dict:
    """The shared shape of a board leg: both windows, their spread, coverage."""
    peak = window(series, *PEAK_WINDOW_UTC)
    offpeak = window(series, *OFFPEAK_WINDOW_UTC)
    leg = {
        "n_halfhours": len(series),
        "peak_window_17_21_bst": peak,
        "offpeak_window_00_05_bst": offpeak,
        "window_spread": round(peak / offpeak, 2) if peak and offpeak else None,
    }
    if unit_extras:
        leg.update(unit_extras)
    return leg


def build_record(
    *,
    mode: str,
    night: dt.date,
    now: dt.datetime,
    span: tuple[dt.datetime, dt.datetime],
    carbon_series: list | None,
    power_series: list | None,
    errors: dict[str, str],
    us_legs: dict[str, dict] | None = None,
    us_carbon_legs: dict[str, dict] | None = None,
) -> dict:
    """Assemble the session's record. Pure — no network, no clock, no filesystem."""
    rec = {
        "night_of": str(night),
        "mode": mode,
        "generated_utc": now.isoformat(timespec="minutes"),
        "span_utc": {"from": z(span[0]), "to": z(span[1])},
        "tokens": {
            "batch_discount": BATCH_DISCOUNT,
            "spread": round(1 / BATCH_DISCOUNT, 2),
            "note": "published batch tiers, OpenAI + Anthropic (50% off list)",
            "urgency_spread": URGENCY_SPREAD,
            "urgency_note": (
                f"same model, fast tier over batch tier on {URGENCY_MODEL} "
                f"({URGENCY_LEGS})"
            ),
            "source": TOKEN_SOURCE,
            "caveat": PROMO_CAVEAT,
        },
        "sources": {
            "carbon": "api.carbonintensity.org.uk (NESO, keyless)",
            "power": "api.octopus.energy Agile day-ahead (keyless, region C)",
            "carbon_us": "api.eia.gov EIA-930 hourly generation mix (key required)",
        },
    }
    if carbon_series:
        leg = _leg(carbon_series)
        bw = best_worst_5h(carbon_series)
        if bw:
            leg["cleanest_5h"] = {"avg": round(bw[0][0], 1), "from": bw[0][1]}
            leg["dirtiest_5h"] = {"avg": round(bw[1][0], 1), "from": bw[1][1]}
            if bw[0][0]:
                leg["chosen_window_spread"] = round(bw[1][0] / bw[0][0], 2)
        rec["carbon_gb"] = leg
    if power_series:
        vals = [v for _, v in power_series]
        rec["power_gb_agile"] = _leg(
            power_series, {"max_p_kwh": max(vals), "min_p_kwh": min(vals)}
        )
    for zone, leg in (us_legs or {}).items():
        if leg:
            rec[f"power_{zone}"] = leg
    for zone, leg in (us_carbon_legs or {}).items():
        if leg:
            rec[f"carbon_{zone}"] = leg
    if errors:
        rec["unavailable"] = errors
    return rec


BOARD_HEADER = (
    "# Offpeak Spread Board — marked sessions\n\n"
    "Quotes are open-data observation, not trade advice; settlements (real runs)\n"
    "live elsewhere. Generated daily by `tools/night_report.py`.\n\n"
    "Token spreads are published rather than observed: batch tiers are 50% off "
    f"list, a {1 / BATCH_DISCOUNT:.1f}x spread for work that can wait, and the same "
    f"model's fast tier is {URGENCY_SPREAD:.0f}x its batch tier — {URGENCY_MODEL} at "
    f"{URGENCY_LEGS}, per {TOKEN_SOURCE}.\n"
    f"Caveat: {PROMO_CAVEAT}.\n\n"
    "| session | power GB (p/kWh) | GB spread | carbon GB (g/kWh) | carbon spread "
    "| CAISO SP15 | CAISO CO2 | ERCOT HOU | ERCOT CO2 | tokens |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
)


def fmt(x, unit: str = "") -> str:
    return f"{x:.1f}{unit}" if isinstance(x, (int, float)) else "—"


def render_row(rec: dict) -> str:
    pg = rec.get("power_gb_agile", {})
    cg = rec.get("carbon_gb", {})
    sp15 = rec.get("power_caiso_sp15", {})
    hou = rec.get("power_ercot_houston", {})
    sp15_co2 = rec.get("carbon_caiso_sp15", {})
    hou_co2 = rec.get("carbon_ercot_houston", {})
    return (
        f"| {rec['night_of']} "
        f"| {fmt(pg.get('peak_window_17_21_bst'))} / {fmt(pg.get('offpeak_window_00_05_bst'))} "
        f"| {fmt(pg.get('window_spread'), 'x')} "
        f"| {fmt(cg.get('peak_window_17_21_bst'))} / {fmt(cg.get('offpeak_window_00_05_bst'))} "
        f"| {fmt(cg.get('window_spread'), 'x')} "
        f"| {fmt(sp15.get('window_spread'), 'x')} "
        f"| {fmt(sp15_co2.get('window_spread'), 'x')} "
        f"| {fmt(hou.get('window_spread'), 'x')} "
        f"| {fmt(hou_co2.get('window_spread'), 'x')} "
        f"| {rec['tokens']['spread']:.1f}x |\n"
    )


def carbon_is_complete(leg: dict | None) -> bool:
    """Whether a carbon leg has both windows and therefore a spread.

    A leg with a peak but no trough is not a partial answer, it is no answer:
    the board's column *is* the spread. Publishing the half we have would put a
    number in a cell that means something else.
    """
    return bool(leg) and leg.get("window_spread") is not None


def backfill_carbon(records: Path, api_key: str, *, lookback: int = 5, fetch=None) -> list[str]:
    """Re-fetch the US carbon legs for recent sessions that are still missing them.

    **This is the whole reason the carbon columns were empty**, and it is worth
    stating precisely, because the wiring was never wrong.

    A session's carbon spread compares its own evening peak against the trough
    of *the following morning* — 00:00–05:00 local, which falls on the next
    calendar day. So marking the session of day D needs EIA to have published
    through D+1 05:00 local, and EIA runs about a day behind. At 06:30Z on D+1
    the feed has not published D at all, let alone D+1. The column was therefore
    unfillable **at mark time, always** — not occasionally, and not because of
    the balancing-authority codes or the intensity method, both of which
    reproduce their published figures exactly.

    And nothing ever came back for it. ``rebuild_board`` is a projection of the
    *records* (#21), so a record written with a null carbon leg re-renders as a
    null carbon leg forever. The data arrives about two days later and no run
    was ever looking.

    This is the run that looks. Every mark pass re-fetches the sessions inside
    *lookback* whose carbon is still missing, rewrites those records where the
    data has since landed, and lets the projection do the rest. A session that
    is still too recent is left exactly as it was, with its reason intact.
    """
    fetch = fetch or (lambda zone, night: us_carbon_leg(eia_mix(zone, night, api_key), night))
    cutoff = dt.date.today() - dt.timedelta(days=lookback)
    filled: list[str] = []
    for path in sorted(records.glob("*-mark.json")):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        night = _record_date(rec)
        if night is None or night < cutoff:
            continue
        changed = False
        for zone in US_ZONES:
            key = f"carbon_{zone}"
            if carbon_is_complete(rec.get(key)):
                continue
            try:
                leg = fetch(zone, night)
            except FetchError as exc:
                rec.setdefault("unavailable", {})[key] = str(exc)
                changed = True
                continue
            except Exception as exc:  # noqa: BLE001 — a backfill never breaks a mark
                print(f"backfill {night} {zone}: {type(exc).__name__}: {exc}")
                continue
            if not carbon_is_complete(leg):
                rec.setdefault("unavailable", {})[key] = (
                    f"{EIA_BA[zone]['ba']}: EIA-930 has {night} but not yet the "
                    f"00:00-05:00 local hours of {night + dt.timedelta(days=1)}, "
                    "which is the trough this session's spread is measured "
                    "against"
                )
                changed = True
                continue
            rec[key] = leg
            (rec.get("unavailable") or {}).pop(key, None)
            if not rec.get("unavailable"):
                rec.pop("unavailable", None)
            filled.append(f"{night} {zone}")
            changed = True
        if changed:
            path.write_text(json.dumps(rec, indent=2) + "\n")
    return filled


def _record_date(rec: dict) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(rec.get("night_of")))
    except (TypeError, ValueError):
        return None


def rebuild_board(board: Path, records: Path) -> int:
    """Rewrite the whole board from the stored records. Returns rows written.

    The board is a **projection**, not an accumulation: every row is rendered
    fresh from that session's ``<date>-mark.json`` on every run. A re-run
    therefore corrects the session it re-marks rather than appending a second
    opinion, and — the reason this replaced a row-at-a-time upsert — adding a
    column repairs *every* row instead of only the header above them.

    The failure that taught this: a two-column addition rewrote the header and
    left the previous session's eight-cell row beneath it, which silently
    shifted that session's ERCOT price spread into the CAISO carbon column. A
    row that cannot be re-derived from a record is not evidence, so a session
    whose record
    has gone missing loses its row rather than keeping a number nobody can
    check.
    """
    rows = []
    for path in sorted(records.glob("*-mark.json")):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            print(f"skipping unreadable record {path.name}: {exc}")
            continue
        rows.append(render_row(rec))
    board.write_text(BOARD_HEADER + "".join(rows))
    return len(rows)


def _night_arg(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:  # argparse renders this as the usage error
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value!r}") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=["quote", "mark"], required=True)
    ap.add_argument("--outdir", default="nightly")
    ap.add_argument(
        "--backfill-days",
        type=int,
        default=5,
        help="how many recent sessions a mark re-checks for late-arriving carbon",
    )
    ap.add_argument(
        "--us-zones",
        action="store_true",
        help="also price CAISO SP15 and ERCOT Houston (needs gridstatus)",
    )
    ap.add_argument(
        "--night",
        type=_night_arg,
        default=None,
        help="the session to run against (YYYY-MM-DD, the date its 16:00Z open "
        "falls on). Default: inferred from the clock. A catch-up run must say "
        "which session it is for — the inference is only right on the cron.",
    )
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if a.night is not None and a.night > now.date():
        print(f"ABORT: {a.night} has not opened yet", file=sys.stderr)
        return 2
    span = night_span(now, a.mode, a.night)
    if span[1] <= span[0]:
        print(
            f"ABORT: the {span[0].date()} session has not opened yet",
            file=sys.stderr,
        )
        return 2
    field = "forecast" if a.mode == "quote" else "actual"

    errors: dict[str, str] = {}
    carbon_series = power_series = None
    try:
        carbon_series = carbon(*span, field)
    except FetchError as exc:
        errors["carbon"] = str(exc)
    try:
        power_series = agile(*span)
    except FetchError as exc:
        errors["power"] = str(exc)

    us_legs: dict[str, dict] = {}
    us_carbon_legs: dict[str, dict] = {}
    if a.us_zones:
        night = span[0].date()
        eia_key = os.environ.get("EIA_API_KEY")
        for zone in US_ZONES:
            try:
                us_legs[zone] = us_leg(_us_rows(zone, night), night)
            except FetchError as exc:
                errors[zone] = str(exc)
            if not eia_key:
                errors[f"carbon_{zone}"] = "EIA_API_KEY is not set"
                continue
            try:
                us_carbon_legs[zone] = us_carbon_leg(
                    eia_mix(zone, night, eia_key), night
                )
            except FetchError as exc:
                errors[f"carbon_{zone}"] = str(exc)

    rec = build_record(
        mode=a.mode,
        night=span[0].date(),
        now=now,
        span=span,
        carbon_series=carbon_series,
        power_series=power_series,
        errors=errors,
        us_legs=us_legs,
        us_carbon_legs=us_carbon_legs,
    )

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rec['night_of']}-{a.mode}.json").write_text(json.dumps(rec, indent=2) + "\n")
    if a.mode == "mark":
        eia_key = os.environ.get("EIA_API_KEY")
        if eia_key:
            # Before the projection re-renders: the carbon a session needs
            # lands about two days after it, so every mark goes back for the
            # ones that were too early last time.
            filled = backfill_carbon(out, eia_key, lookback=a.backfill_days)
            print(f"carbon backfill: filled {len(filled)} leg(s) {filled}")
        written = rebuild_board(out / "BOARD.md", out)
        print(f"board rebuilt from {written} record(s)")

    print(json.dumps(rec, indent=2))
    if not carbon_series and not power_series:
        print("\nboth legs unavailable — nothing marked", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
