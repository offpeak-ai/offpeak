"""The published price sheet — export, load, and the guards on loading.

No network. `load_sheet` is driven from dicts and files here; the one thing that
would need a socket (an https fetch) is exercised through its refusal path
instead, which is the branch that actually protects anybody.
"""

import json

import pytest

import offpeak
from offpeak import prices


def sheet(**over):
    doc = {
        "schema": prices.SHEET_SCHEMA,
        "sheet_date": "2099-01-01",
        "batch_discount": prices.BATCH_DISCOUNT,
        "prices": {
            "claude-haiku-4-5": {"input_per_m": 9.0, "output_per_m": 9.0},
            "brand-new-model": {"input_per_m": 1.0, "output_per_m": 2.0},
        },
        "fast_prices": {"gpt-5.6-sol": {"input_per_m": 80.0, "output_per_m": 400.0}},
        "promo_notes": {
            "brand-new-model": {
                "through": "2099-12-31",
                "post_promo": [2.0, 4.0],
                "source": "example.invalid/pricing",
                "note": "promotional",
            }
        },
    }
    doc.update(over)
    return doc


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #


def test_export_round_trips_through_load():
    exported = prices.export_sheet()
    prices.load_sheet(exported, replace=True)
    assert prices.sheet_date() == prices.PRICE_SHEET_DATE
    assert prices.get_price("claude-haiku-4-5") == (1.00, 5.00)


def test_export_is_json_serializable():
    json.dumps(prices.export_sheet())


def test_export_carries_schema_date_and_discount():
    doc = prices.export_sheet()
    assert doc["schema"] == prices.SHEET_SCHEMA
    assert doc["sheet_date"] == prices.sheet_date()
    assert doc["batch_discount"] == prices.BATCH_DISCOUNT
    assert doc["prices"]["gpt-5.6-luna"] == {"input_per_m": 0.20, "output_per_m": 1.20}


def test_export_includes_fast_rows_and_promo_notes():
    doc = prices.export_sheet()
    assert doc["fast_prices"]["gpt-5.6-sol"] == {"input_per_m": 8.00, "output_per_m": 40.00}
    assert doc["promo_notes"]["gpt-5.6-sol"]["post_promo"] == [5.00, 30.00]


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #


def test_load_applies_prices_and_moves_the_date():
    load = prices.load_sheet(sheet())
    assert prices.sheet_date() == "2099-01-01"
    assert prices.get_price("claude-haiku-4-5") == (9.0, 9.0)
    assert prices.get_price("brand-new-model") == (1.0, 2.0)
    assert load.added == 1 and load.changed == 1


def test_load_merges_by_default_keeping_models_it_omits():
    prices.load_sheet(sheet())
    assert prices.get_price("gpt-5.6-luna") is not None


def test_load_replace_makes_the_sheet_the_whole_truth():
    prices.load_sheet(sheet(), replace=True)
    assert prices.get_price("gpt-5.6-luna") is None
    assert prices.get_price("claude-haiku-4-5") == (9.0, 9.0)


def test_load_from_a_file(tmp_path):
    path = tmp_path / "sheet.json"
    path.write_text(json.dumps(sheet()))
    assert prices.load_sheet(path).sheet_date == "2099-01-01"


def test_load_reports_what_it_did():
    load = prices.load_sheet(sheet())
    assert load.models == 2
    assert load.fast_models == 1
    assert load.promo_notes == 1
    assert "2099-01-01" in str(load)


def test_load_applies_fast_rows_and_promo_notes():
    prices.load_sheet(sheet())
    assert prices.get_fast_price("gpt-5.6-sol") == (80.0, 400.0)
    assert prices.get_promo_note("brand-new-model").post_promo == (2.0, 4.0)


def test_reset_restores_the_bundled_sheet():
    prices.load_sheet(sheet(), replace=True)
    assert prices.reset_sheet() == prices.PRICE_SHEET_DATE
    assert prices.sheet_date() == prices.PRICE_SHEET_DATE
    assert prices.get_price("claude-haiku-4-5") == (1.00, 5.00)
    assert prices.get_price("brand-new-model") is None
    assert prices.get_fast_price("gpt-5.6-sol") == (8.00, 40.00)


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #


def test_plain_http_is_refused():
    with pytest.raises(ValueError, match="plain http"):
        prices.load_sheet("http://example.invalid/sheet.json")


def test_unknown_schema_is_refused():
    with pytest.raises(ValueError, match="unsupported price-sheet schema"):
        prices.load_sheet(sheet(schema="offpeak.price-sheet/2"))


def test_missing_schema_is_refused():
    doc = sheet()
    del doc["schema"]
    with pytest.raises(ValueError, match="unsupported price-sheet schema"):
        prices.load_sheet(doc)


def test_undated_sheet_is_refused():
    doc = sheet()
    del doc["sheet_date"]
    with pytest.raises(ValueError, match="no sheet_date"):
        prices.load_sheet(doc)


def test_a_different_batch_discount_is_refused_not_applied():
    """The discount is a published rule, and client/quote bound it at import."""
    with pytest.raises(ValueError, match="batch_discount"):
        prices.load_sheet(sheet(batch_discount=0.4))
    assert prices.BATCH_DISCOUNT == 0.5


def test_empty_sheet_is_refused():
    with pytest.raises(ValueError, match="no prices"):
        prices.load_sheet(sheet(prices={}))


def test_malformed_row_is_refused_and_names_the_model():
    with pytest.raises(ValueError, match="bad-row"):
        prices.load_sheet(sheet(prices={"bad-row": {"input_per_m": 1.0}}))


def test_a_refused_sheet_leaves_the_table_untouched():
    """A half-applied sheet would price some jobs new and some old."""
    before = dict(prices._PRICES)
    with pytest.raises(ValueError):
        prices.load_sheet(sheet(batch_discount=0.4))
    assert prices._PRICES == before
    assert prices.sheet_date() == prices.PRICE_SHEET_DATE


# --------------------------------------------------------------------------- #
# The import-binding trap this API exists to avoid
# --------------------------------------------------------------------------- #


def test_a_settlement_prints_the_loaded_sheet_date_not_the_bundled_one():
    """client.py did `from .prices import PRICE_SHEET_DATE` — a bound copy."""
    prices.load_sheet(sheet())
    rendered = str(offpeak.receipt([]))
    assert "2099-01-01" in rendered
    assert prices.PRICE_SHEET_DATE not in rendered


def test_a_quote_prints_the_loaded_sheet_date_not_the_bundled_one():
    prices.load_sheet(sheet())
    jobs = [offpeak.job("claude-haiku-4-5", "hi", max_tokens=10)]
    assert "2099-01-01" in str(offpeak.quote(jobs, "6h"))


def test_bundled_constant_never_moves():
    """A receipt naming it must stay checkable against the same numbers."""
    prices.load_sheet(sheet())
    assert prices.PRICE_SHEET_DATE == "2026-08-30"


def test_a_loaded_sheet_actually_changes_the_arithmetic():
    prices.load_sheet(sheet())
    # 9.00/1M in, 9.00/1M out under the loaded sheet.
    assert prices.list_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(18.0)
    assert prices.batch_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(9.0)


# --------------------------------------------------------------------------- #
# lanes
# --------------------------------------------------------------------------- #


def test_export_carries_lanes_without_moving_the_schema():
    # Additive: every key an older reader knows keeps its shape and the major
    # does not move, so a sheet published by this build still loads there.
    doc = prices.export_sheet()
    assert doc["schema"] == "offpeak.price-sheet/1"
    assert set(doc) >= {
        "schema",
        "sheet_date",
        "generated_utc",
        "batch_discount",
        "prices",
        "fast_prices",
        "promo_notes",
    }
    assert doc["lanes"] == {"deepseek-": "clock"}
    assert doc["prices"]["deepseek-v4-flash"] == {"input_per_m": 0.44, "output_per_m": 1.32}


def test_an_old_reader_of_the_export_sees_the_same_price_rows():
    # What a pre-lanes consumer does with the document: read the four tables
    # it knows and ignore the rest. Nothing it reads changed shape.
    doc = prices.export_sheet()
    legacy = {
        k: doc[k]
        for k in ("schema", "sheet_date", "batch_discount", "prices", "fast_prices", "promo_notes")
    }
    prices.load_sheet(legacy, replace=True)
    assert prices.get_price("deepseek-v4-pro") == (1.32, 3.96)
    # A sheet that says nothing about lanes retracts nothing.
    assert prices.lane_for("deepseek-v4-pro") == "clock"


def test_a_sheet_with_lanes_is_honoured_and_reset_puts_the_bundled_ones_back():
    prices.load_sheet(sheet(lanes={"brand-new-model": "clock"}))
    assert prices.lane_for("brand-new-model") == "clock"
    assert prices.lane_for("deepseek-v4-flash") == "clock"  # merged, not replaced
    prices.load_sheet(sheet(lanes={"brand-new-model": "clock"}), replace=True)
    assert prices.lane_for("deepseek-v4-flash") is None  # its price row is gone
    prices.reset_sheet()
    assert prices.lane_for("deepseek-v4-flash") == "clock"
    assert prices.lane_for("brand-new-model") is None


def test_an_unknown_lane_is_refused():
    with pytest.raises(ValueError, match="not batch or clock"):
        prices.load_sheet(sheet(lanes={"brand-new-model": "spot"}))
