#!/usr/bin/env python3
"""Offpeak night board generator — quotes at dusk, marks at dawn.

Open, keyless data only; zero venue spend. GB legs v1: NESO carbon intensity
(forecast at dusk, actual at dawn) and Octopus Agile day-ahead power. US zones
(CAISO/ERCOT via gridstatus) and EIA carbon: TODO next rev.

Run as two passes over the same night:

    night_report.py --mode quote   # ~19:00Z, the night ahead, carbon forecast
    night_report.py --mode mark    # ~06:30Z, the night just finished, actuals

A "night" is 16:00Z-07:00Z — it opens with the 17:00 BST evening peak and
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
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

CI_API = "https://api.carbonintensity.org.uk/intensity/{f}/{t}"
AGILE = (
    "https://api.octopus.energy/v1/products/AGILE-24-10-01/"
    "electricity-tariffs/E-1R-AGILE-24-10-01-C/standard-unit-rates/"
    "?period_from={f}&period_to={t}&page_size=1500"
)

# Published batch discount, both venues. Kept in sync with offpeak.prices by
# test_night_report.py rather than by hand.
BATCH_DISCOUNT = 0.5

NIGHT_START_HOUR_UTC = 16  # 17:00 BST — the evening peak opens
NIGHT_HOURS = 15  # ...through 07:00Z
PEAK_WINDOW_UTC = (16, 20)  # 17-21 BST
OFFPEAK_WINDOW_UTC = (23, 4)  # 00-05 BST, wraps midnight
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


def night_span(now: dt.datetime, mode: str) -> tuple[dt.datetime, dt.datetime]:
    """The (from, to) UTC span of the night this run is about.

    ``mark`` truncates at *now* — actuals do not exist for the future.
    """
    anchor = now.replace(minute=0, second=0, microsecond=0)
    start = anchor.replace(hour=NIGHT_START_HOUR_UTC)
    if anchor.hour < NIGHT_START_HOUR_UTC:
        start -= dt.timedelta(days=1)
    end = start + dt.timedelta(hours=NIGHT_HOURS)
    if mode == "mark":
        end = min(end, now)
    return start, end


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
) -> dict:
    """Assemble the night's record. Pure — no network, no clock, no filesystem."""
    rec = {
        "night_of": str(night),
        "mode": mode,
        "generated_utc": now.isoformat(timespec="minutes"),
        "span_utc": {"from": z(span[0]), "to": z(span[1])},
        "tokens": {
            "batch_discount": BATCH_DISCOUNT,
            "spread": round(1 / BATCH_DISCOUNT, 2),
            "note": "published batch tiers, OpenAI + Anthropic (50% of list)",
        },
        "sources": {
            "carbon": "api.carbonintensity.org.uk (NESO, keyless)",
            "power": "api.octopus.energy Agile day-ahead (keyless, region C)",
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
    if errors:
        rec["unavailable"] = errors
    return rec


BOARD_HEADER = (
    "# Offpeak night board — marked nights\n\n"
    "Quotes are open-data observation, not trade advice; settlements (real runs)\n"
    "live elsewhere. Generated nightly by `tools/night_report.py`.\n\n"
    "| night | power GB peak/offpeak (p/kWh) | spread | carbon GB peak/offpeak (g/kWh) "
    "| spread | tokens |\n"
    "|---|---|---|---|---|---|\n"
)


def fmt(x, unit: str = "") -> str:
    return f"{x:.1f}{unit}" if isinstance(x, (int, float)) else "—"


def render_row(rec: dict) -> str:
    pg = rec.get("power_gb_agile", {})
    cg = rec.get("carbon_gb", {})
    return (
        f"| {rec['night_of']} "
        f"| {fmt(pg.get('peak_window_17_21_bst'))} / {fmt(pg.get('offpeak_window_00_05_bst'))} "
        f"| {fmt(pg.get('window_spread'), 'x')} "
        f"| {fmt(cg.get('peak_window_17_21_bst'))} / {fmt(cg.get('offpeak_window_00_05_bst'))} "
        f"| {fmt(cg.get('window_spread'), 'x')} "
        f"| {rec['tokens']['spread']:.1f}x |\n"
    )


def upsert_board_row(board: Path, night: str, row: str) -> None:
    """Write *row* for *night*, replacing any existing row for the same night.

    A re-run must correct the night it re-marks, not append a second opinion.
    """
    if not board.exists():
        board.write_text(BOARD_HEADER + row)
        return
    lines = board.read_text().splitlines(keepends=True)
    marker = f"| {night} |"
    for i, line in enumerate(lines):
        if line.startswith(marker):
            lines[i] = row
            board.write_text("".join(lines))
            return
    board.write_text("".join(lines) + row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=["quote", "mark"], required=True)
    ap.add_argument("--outdir", default="nightly")
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    span = night_span(now, a.mode)
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

    rec = build_record(
        mode=a.mode,
        night=span[0].date(),
        now=now,
        span=span,
        carbon_series=carbon_series,
        power_series=power_series,
        errors=errors,
    )

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rec['night_of']}-{a.mode}.json").write_text(json.dumps(rec, indent=2) + "\n")
    if a.mode == "mark":
        upsert_board_row(out / "BOARD.md", rec["night_of"], render_row(rec))

    print(json.dumps(rec, indent=2))
    if not carbon_series and not power_series:
        print("\nboth legs unavailable — nothing marked", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
