"""The settled-runs ledger — real money, kept separate from open-data quotes."""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "settle_report", Path(__file__).resolve().parent.parent / "tools" / "settle_report.py"
)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def record(**kw):
    base = {
        "run_id": "2026-08-22-mechanics-1",
        "scale": "mechanics proof (48 jobs)",
        "settled_utc": "2026-08-22T05:00:00+00:00",
        "jobs": 48,
        "ok": 48,
        "failed": 0,
        "fell_back": 0,
        "sla_met": 48,
        "input_tokens": 1300,
        "output_tokens": 400,
        "list_usd": 0.00317,
        "paid_usd": 0.00158,
        "captured_usd": 0.00159,
        "captured_pct": 50.1,
        "by_venue": {"anthropic:batch": 24, "openai:batch": 24},
    }
    base.update(kw)
    return base


class TestLoadReceipt:
    def test_a_complete_receipt_loads(self, tmp_path):
        path = tmp_path / "r.json"
        path.write_text(json.dumps(record()))
        assert sr.load_receipt(path)["jobs"] == 48

    @pytest.mark.parametrize("missing", ["run_id", "scale", "jobs", "list_usd", "paid_usd"])
    def test_an_incomplete_receipt_is_refused_not_rendered_with_holes(self, tmp_path, missing):
        path = tmp_path / "r.json"
        path.write_text(json.dumps(record(**{missing: None})))
        with pytest.raises(ValueError, match=missing):
            sr.load_receipt(path)

    def test_a_run_id_must_open_with_its_settlement_date(self, tmp_path):
        # Which is what makes a data row recognisable under a stale header.
        path = tmp_path / "r.json"
        path.write_text(json.dumps(record(run_id="mechanics-proof")))
        with pytest.raises(ValueError, match="run_id must open"):
            sr.load_receipt(path)

    def test_a_blank_scale_is_refused(self, tmp_path):
        # The whole point of the column: a run that will not say how big it was
        # does not belong in a ledger people read for evidence.
        path = tmp_path / "r.json"
        path.write_text(json.dumps(record(scale="   ")))
        with pytest.raises(ValueError, match="scale"):
            sr.load_receipt(path)


class TestRenderRow:
    def test_the_row_leads_with_the_run_and_its_scale(self):
        row = sr.render_row(record())
        assert row.startswith("| 2026-08-22-mechanics-1 | mechanics proof (48 jobs) |")

    def test_sub_cent_money_keeps_its_significant_digits(self):
        # A sub-cent settlement that renders as $0.00 is not a receipt.
        row = sr.render_row(record())
        assert "$0.00317" in row and "$0.00158" in row
        assert "| $0.00 " not in row

    def test_captured_is_derived_when_absent(self):
        row = sr.render_row(record(captured_usd=None, captured_pct=None))
        assert "$0.00159" in row and "(50.2%)" in row

    def test_a_fallback_is_shown_in_the_sla_column_not_hidden(self):
        row = sr.render_row(record(fell_back=3, sla_met=48))
        assert "48/48 (3 fell back)" in row

    def test_a_run_with_no_venue_map_still_renders(self):
        assert "| — |" in sr.render_row(record(by_venue={}))


class TestUpsert:
    def test_creates_the_header_then_appends(self, tmp_path):
        board = tmp_path / "SETTLED.md"
        sr.upsert_settled_row(board, "2026-08-22-a", "| 2026-08-22-a | x |\n")
        sr.upsert_settled_row(board, "2026-08-23-b", "| 2026-08-23-b | y |\n")
        text = board.read_text()
        assert text.startswith("# Offpeak settled runs")
        assert text.count("| 2026-08-22-a |") == 1
        assert text.rstrip().endswith("| 2026-08-23-b | y |")

    def test_re_rendering_a_run_corrects_it_instead_of_duplicating(self, tmp_path):
        board = tmp_path / "SETTLED.md"
        sr.upsert_settled_row(board, "2026-08-22-a", "| 2026-08-22-a | first |\n")
        sr.upsert_settled_row(board, "2026-08-22-a", "| 2026-08-22-a | corrected |\n")
        text = board.read_text()
        assert text.count("| 2026-08-22-a |") == 1
        assert "corrected" in text and "first" not in text

    def test_a_stale_header_is_repaired_and_rows_are_kept(self, tmp_path):
        board = tmp_path / "SETTLED.md"
        board.write_text(
            "# Offpeak settled runs\n\n| old | columns |\n|---|---|\n"
            "| 2026-08-21-a | a |\n"
        )
        sr.upsert_settled_row(board, "2026-08-22-b", "| 2026-08-22-b | b |\n")
        text = board.read_text()
        assert text.startswith(sr.SETTLED_HEADER)
        assert "| old | columns |" not in text
        assert "| 2026-08-21-a | a |" in text


class TestSeparationFromTheQuoteBoard:
    def test_the_header_says_what_this_ledger_is_not(self):
        assert "BOARD.md" in sr.SETTLED_HEADER
        assert "spends nothing" in sr.SETTLED_HEADER

    def test_the_header_tells_the_reader_to_read_scale_first(self):
        assert "Scale is on every row on purpose" in sr.SETTLED_HEADER


# --------------------------------------------------------------------------- #
# SETTLED.json — the machine-readable ledger the website reads
# --------------------------------------------------------------------------- #


def _write_receipts(tmp_path, records):
    d = tmp_path / "receipts"
    d.mkdir()
    for r in records:
        (d / f"{r['run_id']}.json").write_text(json.dumps(r))
    return d


def _run(tmp_path, records, monkeypatch):
    rec = _write_receipts(tmp_path, records)
    out = tmp_path / "board"
    monkeypatch.setattr(
        "sys.argv", ["settle_report.py", "--receipts", str(rec), "--outdir", str(out)]
    )
    sr.main()
    return json.loads((out / "SETTLED.json").read_text())


def test_writes_a_machine_readable_ledger(tmp_path, monkeypatch):
    doc = _run(tmp_path, [record()], monkeypatch)
    assert doc["schema"] == sr.SETTLED_SCHEMA
    assert doc["runs"][0]["run_id"] == "2026-08-22-mechanics-1"
    assert doc["summary"]["runs"] == 1


def test_ledger_carries_notes_so_the_site_need_not_restate_them(tmp_path, monkeypatch):
    doc = _run(tmp_path, [record(notes=["the venue was gated"])], monkeypatch)
    assert doc["runs"][0]["notes"] == ["the venue was gated"]


def test_summary_totals_the_money(tmp_path, monkeypatch):
    doc = _run(
        tmp_path,
        [
            record(run_id="2026-08-22-a", list_usd=1.0, paid_usd=0.5, captured_usd=0.5),
            record(run_id="2026-08-22-b", list_usd=3.0, paid_usd=1.5, captured_usd=1.5),
        ],
        monkeypatch,
    )
    assert doc["summary"]["list_usd"] == 4.0
    assert doc["summary"]["paid_usd"] == 2.0
    assert doc["summary"]["captured_usd"] == 2.0
    assert doc["summary"]["runs"] == 2


def test_venues_capturing_excludes_a_venue_that_fell_back(tmp_path, monkeypatch):
    """A batch reached and then lost is not a capturing venue."""
    doc = _run(
        tmp_path,
        [
            record(run_id="2026-08-22-a", by_venue={"openai:batch": 4}, captured_usd=0.5),
            record(
                run_id="2026-08-22-b",
                by_venue={"mistral:batch": 4},
                captured_usd=0.0,
                fell_back=4,
            ),
        ],
        monkeypatch,
    )
    assert doc["summary"]["venues_capturing"] == ["openai:batch"]
    assert doc["summary"]["runs_capturing"] == 1
    assert doc["summary"]["runs_capturing_nothing"] == 1


def test_captured_is_derived_when_a_receipt_omits_it(tmp_path, monkeypatch):
    r = record(list_usd=2.0, paid_usd=0.5)
    r.pop("captured_usd", None)
    r.pop("captured_pct", None)
    doc = _run(tmp_path, [r], monkeypatch)
    assert doc["runs"][0]["captured_usd"] == 1.5
    assert doc["runs"][0]["captured_pct"] == 75.0


def test_ledger_is_sorted_by_run_id(tmp_path, monkeypatch):
    doc = _run(
        tmp_path,
        [record(run_id="2026-08-24-z"), record(run_id="2026-08-22-a")],
        monkeypatch,
    )
    assert [r["run_id"] for r in doc["runs"]] == ["2026-08-22-a", "2026-08-24-z"]


# --------------------------------------------------------------------------- #
# Receipt identity — derived, not minted
# --------------------------------------------------------------------------- #


def test_receipt_uuid_is_derived_from_the_run_it_names():
    """Anyone holding the receipt can recompute it. That is the whole point."""
    import uuid as _uuid

    r = record()
    expected = str(
        _uuid.uuid5(sr.RECEIPT_NAMESPACE, f"{r['run_id']}|{r['settled_utc']}")
    )
    assert sr.receipt_uuid(r) == expected


def test_receipt_uuid_is_stable_across_calls():
    r = record()
    assert sr.receipt_uuid(r) == sr.receipt_uuid(r)


def test_receipt_uuid_moves_when_the_run_does():
    a = sr.receipt_uuid(record(run_id="2026-08-22-a"))
    b = sr.receipt_uuid(record(run_id="2026-08-22-b"))
    c = sr.receipt_uuid(record(settled_utc="2026-08-23T05:00:00+00:00"))
    assert len({a, b, c}) == 3


def test_a_stated_uuid_that_disagrees_is_refused(tmp_path):
    """An id that can drift from its run is worse than no id."""
    d = _write_receipts(tmp_path, [record(receipt_uuid="00000000-0000-0000-0000-000000000000")])
    with pytest.raises(ValueError, match="does not match the id derived"):
        sr.load_receipt(next(d.glob("*.json")))


def test_a_stated_uuid_that_agrees_is_accepted(tmp_path):
    r = record()
    r["receipt_uuid"] = sr.receipt_uuid(r)
    d = _write_receipts(tmp_path, [r])
    assert sr.load_receipt(next(d.glob("*.json")))["receipt_uuid"] == sr.receipt_uuid(r)


def test_ledger_carries_the_uuid_and_the_handles(tmp_path, monkeypatch):
    doc = _run(
        tmp_path,
        [record(venue_handles={"openai:batch": ["batch_abc123"]})],
        monkeypatch,
    )
    assert doc["runs"][0]["receipt_uuid"] == sr.receipt_uuid(record())
    assert doc["runs"][0]["venue_handles"] == {"openai:batch": ["batch_abc123"]}


# --------------------------------------------------------------------------- #
# Collision — used to be silently destructive
# --------------------------------------------------------------------------- #


def test_two_receipts_claiming_one_run_id_are_refused(tmp_path, monkeypatch):
    """SETTLED.md dropped one and SETTLED.json double-counted the other."""
    rec = _write_receipts(
        tmp_path, [record(run_id="2026-08-22-x", list_usd=1.0)]
    )
    (rec / "second.json").write_text(
        json.dumps(record(run_id="2026-08-22-x", list_usd=99.0))
    )
    monkeypatch.setattr(
        "sys.argv",
        ["settle_report.py", "--receipts", str(rec), "--outdir", str(tmp_path / "b")],
    )
    with pytest.raises(ValueError, match="already claimed by"):
        sr.main()


# --------------------------------------------------------------------------- #
# What must never be published
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field", ["per_job", "results", "messages", "prompts", "raw"]
)
def test_artifact_only_fields_are_refused(tmp_path, field):
    """A receipt is aggregate by construction; per-job rows carry model output."""
    d = _write_receipts(tmp_path, [record(**{field: [{"text": "hello"}]})])
    with pytest.raises(ValueError, match=field):
        sr.load_receipt(next(d.glob("*.json")))


@pytest.mark.parametrize(
    "leak",
    [
        "sk-abcdefghijklmnop",
        "Bearer abcdefghijklmnop",
        "org-abcdefghijkl",
        "AKIAABCDEFGHIJKLMNOP",
        "AIzaSyAbcdefghijklmnopqrstuvwxyz012",
        'api_key: "abcdefghijklmnop"',
    ],
)
def test_secret_shaped_content_is_refused(tmp_path, leak):
    """Provider error text is not safe to copy verbatim into a public file."""
    d = _write_receipts(tmp_path, [record(notes=[f"the venue said: {leak}"])])
    with pytest.raises(ValueError, match="secret-shaped"):
        sr.load_receipt(next(d.glob("*.json")))


def test_a_batch_handle_is_not_mistaken_for_a_secret(tmp_path):
    """Handles are opaque and account-scoped — publishing one grants nothing."""
    d = _write_receipts(
        tmp_path,
        [
            record(
                venue_handles={
                    "openai:batch": ["batch_6a8e6b8ef08c8190bd5cac0858f9789c"],
                    "anthropic:batch": ["msgbatch_01BHiETAX1Hprbvq4rFnj6PS"],
                    "mistral:batch": ["4a7ccbb3-6581-412d-88c9-8e4a2af3109b"],
                    "gemini:batch": ["batches/zrdgw9dep0lx52gnog4y5z78jld998p0p8va"],
                }
            )
        ],
    )
    assert sr.load_receipt(next(d.glob("*.json")))["venue_handles"]


def test_every_committed_receipt_passes_the_guards():
    """The ledger in this repo must satisfy the rules it enforces on others."""
    for path in sorted(Path(__file__).resolve().parent.parent.glob("receipts/*.json")):
        sr.load_receipt(path)
