"""Publishing the price sheet as dated, immutable JSON."""

import importlib.util
import json
import sys
from pathlib import Path

from offpeak import prices

_spec = importlib.util.spec_from_file_location(
    "publish_sheet", Path(__file__).resolve().parent.parent / "tools" / "publish_sheet.py"
)
ps = importlib.util.module_from_spec(_spec)
sys.modules["publish_sheet"] = ps
_spec.loader.exec_module(ps)


def test_writes_dated_latest_and_index(tmp_path):
    assert ps.main(["--outdir", str(tmp_path)]) == 0
    dated = tmp_path / f"{prices.PRICE_SHEET_DATE}.json"
    assert dated.exists()
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "index.json").exists()


def test_published_sheet_loads_back(tmp_path):
    ps.main(["--outdir", str(tmp_path)])
    load = prices.load_sheet(tmp_path / "latest.json", replace=True)
    assert load.sheet_date == prices.PRICE_SHEET_DATE
    assert prices.get_price("claude-haiku-4-5") == (1.00, 5.00)


def test_latest_names_its_own_date_so_a_caller_can_pin_it(tmp_path):
    ps.main(["--outdir", str(tmp_path)])
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert latest["sheet_date"] == prices.PRICE_SHEET_DATE
    assert (tmp_path / f"{latest['sheet_date']}.json").exists()


def test_index_lists_every_sheet_newest_first(tmp_path):
    (tmp_path / "2020-01-01.json").write_text("{}")
    ps.main(["--outdir", str(tmp_path)])
    index = json.loads((tmp_path / "index.json").read_text())
    dates = [s["sheet_date"] for s in index["sheets"]]
    assert dates == sorted(dates, reverse=True)
    assert index["latest"] == prices.PRICE_SHEET_DATE
    assert "2020-01-01" in dates


def test_republishing_an_identical_sheet_is_a_noop(tmp_path, capsys):
    ps.main(["--outdir", str(tmp_path)])
    assert ps.main(["--outdir", str(tmp_path)]) == 0
    assert "already published and identical" in capsys.readouterr().out


def test_a_dated_sheet_is_immutable(tmp_path, capsys):
    """Editing a sheet without moving its date changes what old receipts mean."""
    ps.main(["--outdir", str(tmp_path)])
    prices.register_price("claude-haiku-4-5", 99.0, 99.0)
    assert ps.main(["--outdir", str(tmp_path)]) == 1
    assert "REFUSING" in capsys.readouterr().err
    # and the published file still says what it said
    published = json.loads((tmp_path / f"{prices.PRICE_SHEET_DATE}.json").read_text())
    assert published["prices"]["claude-haiku-4-5"]["input_per_m"] == 1.00


def test_force_overrides_the_immutability_guard(tmp_path):
    ps.main(["--outdir", str(tmp_path)])
    prices.register_price("claude-haiku-4-5", 99.0, 99.0)
    assert ps.main(["--outdir", str(tmp_path), "--force"]) == 0
    published = json.loads((tmp_path / f"{prices.PRICE_SHEET_DATE}.json").read_text())
    assert published["prices"]["claude-haiku-4-5"]["input_per_m"] == 99.0


def test_a_moving_timestamp_alone_is_not_a_difference(tmp_path):
    """generated_utc changes every run and must not trip the guard."""
    ps.main(["--outdir", str(tmp_path)])
    first = json.loads((tmp_path / f"{prices.PRICE_SHEET_DATE}.json").read_text())
    assert ps.main(["--outdir", str(tmp_path)]) == 0
    assert ps._comparable(first) == ps._comparable(prices.export_sheet())
