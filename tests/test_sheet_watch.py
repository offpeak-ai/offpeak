"""The sheet watch — everything that happens without touching the network.

Nothing here fetches a page or submits a job. The fetcher is injected and the
classifier's runner is stubbed, so these tests measure the instrument.

The load-bearing property is the one at the bottom: **rows publish even when the
classifier does not**. Every way the classification can fail — no key, a runner
that raises, a cap too tight, a deadline past the board mark — is asserted to
leave the hash-diff rows on disk anyway.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from offpeak import Job, Receipt, Result, prices

_spec = importlib.util.spec_from_file_location(
    "sheet_watch", Path(__file__).resolve().parent.parent / "tools" / "sheet_watch.py"
)
sw = importlib.util.module_from_spec(_spec)
# Same reason as test_queue_probe: the tool's dataclasses carry string
# annotations, and dataclasses resolves those through sys.modules.
sys.modules["sheet_watch"] = sw
_spec.loader.exec_module(sw)


PAGE_V1 = "<html><head><style>b{}</style></head><body><h1>Prices</h1><p>Luna $0.20 / 1M</p></body>"
PAGE_V2 = "<html><head><style>b{}</style></head><body><h1>Prices</h1><p>Luna $0.30 / 1M</p></body>"

ONE = (sw.Source("openai:pricing", "https://example.invalid/openai", True),)


def fetcher_for(page):
    return lambda url: page


# --------------------------------------------------------------------------- #
# Normalization and hashing
# --------------------------------------------------------------------------- #


def test_html_to_text_drops_script_and_style():
    text = sw.html_to_text("<style>a{color:red}</style><p>Hello</p><script>x=1</script>")
    assert "color" not in text
    assert "x=1" not in text
    assert "Hello" in text


def test_html_to_text_collapses_whitespace_and_blank_lines():
    text = sw.html_to_text("<p>a\t\t  b</p>\n\n\n<p>   </p><p>c</p>")
    assert text == "a b\nc"


def test_html_to_text_survives_malformed_markup():
    assert "Hello" in sw.html_to_text("<p>Hello<<<>")


def test_digest_is_stable_and_sensitive():
    assert sw.digest("a") == sw.digest("a")
    assert sw.digest("a") != sw.digest("b")


def test_whitespace_churn_alone_is_not_a_change():
    """The most common way a page 'changes' without changing."""
    a = sw.html_to_text("<p>Luna  $0.20</p>")
    b = sw.html_to_text("<p>Luna\t$0.20</p>")
    assert sw.digest(a) == sw.digest(b)


# --------------------------------------------------------------------------- #
# Diffing
# --------------------------------------------------------------------------- #


def test_first_run_is_a_baseline_not_a_change(tmp_path):
    changes, snapshots, pages = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(PAGE_V1))
    assert [c.status for c in changes] == ["baseline"]
    assert snapshots["openai:pricing"]["sha256"]
    assert "openai:pricing" in pages


def test_unchanged_page_reports_nothing(tmp_path):
    _, snapshots, pages = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(PAGE_V1))
    sw._write(tmp_path, snapshots, pages, [])

    changes, _, _ = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(PAGE_V1))
    assert [c.status for c in changes] == ["unchanged"]
    assert not changes[0].reportable


def test_changed_page_reports_a_diff(tmp_path):
    _, snapshots, pages = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(PAGE_V1))
    sw._write(tmp_path, snapshots, pages, [])

    changes, _, _ = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(PAGE_V2))
    (change,) = changes
    assert change.status == "changed"
    assert change.added and change.removed
    assert "0.30" in change.diff
    assert change.reportable


def test_unreachable_source_keeps_the_previous_snapshot(tmp_path):
    """A timeout today must not read as a change tomorrow."""
    _, snapshots, pages = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(PAGE_V1))
    sw._write(tmp_path, snapshots, pages, [])
    before = json.loads((tmp_path / "snapshots.json").read_text())

    def boom(url):
        raise OSError("connection reset")

    changes, after, _ = sw.detect_changes(ONE, tmp_path, fetcher=boom)
    assert changes[0].status == "unreachable"
    assert "connection reset" in changes[0].detail
    assert after == before


def test_page_that_strips_to_nothing_is_unreachable_not_changed(tmp_path):
    changes, _, _ = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for("<script>x</script>"))
    assert changes[0].status == "unreachable"


def test_corrupt_snapshot_file_falls_back_to_baseline(tmp_path):
    (tmp_path / "snapshots.json").write_text("{not json")
    changes, _, _ = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(PAGE_V1))
    assert changes[0].status == "baseline"


# --------------------------------------------------------------------------- #
# Model selection
# --------------------------------------------------------------------------- #


def test_pick_model_takes_the_cheapest_key_present():
    model, why = sw.pick_model(env={"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y"})
    assert model == "gpt-5.6-luna"  # cheaper than claude-haiku-4-5 on the sheet
    assert why == ""


def test_pick_model_falls_back_to_the_key_that_exists():
    model, _ = sw.pick_model(env={"ANTHROPIC_API_KEY": "y"})
    assert model == "claude-haiku-4-5"


def test_pick_model_reports_when_no_key_is_present():
    model, why = sw.pick_model(env={})
    assert model is None
    assert "OPENAI_API_KEY" in why and "ANTHROPIC_API_KEY" in why


def test_pick_model_skips_a_model_with_no_price(monkeypatch):
    monkeypatch.delitem(prices._PRICES, "gpt-5.6-luna")
    model, _ = sw.pick_model(env={"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y"})
    assert model == "claude-haiku-4-5"


# --------------------------------------------------------------------------- #
# Label parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("LABEL: price change\nWHY: luna went up", "price change"),
        ("label: noise\nwhy: banner", "noise"),
        ("  LABEL:  Copy Change  \n", "copy change"),
        ("I think this is a price change, really", "price change"),
        ("no idea", ""),
        ("", ""),
    ],
)
def test_parse_label(reply, expected):
    assert sw._parse_label(reply)[0] == expected


def test_parse_label_extracts_the_why():
    assert sw._parse_label("LABEL: noise\nWHY: just a banner")[1] == "just a banner"


# --------------------------------------------------------------------------- #
# Classification — the fail-open contract
# --------------------------------------------------------------------------- #


def changed_fixture():
    change = sw.Change(ONE[0], "changed", added=2, removed=1, diff="-old\n+new")
    return [change]


def soon():
    """A deadline that is in the future and before the next board mark."""
    return sw.next_utc(*sw.DEFAULT_DEADLINE_UTC)


def ok_result(job, text):
    now = datetime.now(timezone.utc)
    return Result(
        job=job,
        text=text,
        receipt=Receipt(
            venue="openai:batch",
            model=job.model,
            deadline=now + timedelta(hours=1),
            submitted_at=now,
            completed_at=now,
            input_tokens=1_500,
            output_tokens=40,
        ),
    )


def test_classify_labels_and_prices_a_diff():
    changes = changed_fixture()

    def runner(jobs, deadline):
        return [ok_result(j, "LABEL: price change\nWHY: luna moved") for j in jobs]

    note = sw.classify(
        changes,
        deadline=soon(),
        cap_usd=0.01,
        env={"OPENAI_API_KEY": "x"},
        runner=runner,
    )
    assert changes[0].label == "price change"
    assert changes[0].reason == "luna moved"
    assert changes[0].model == "gpt-5.6-luna"
    assert changes[0].paid_usd is not None and changes[0].paid_usd > 0
    assert "classified" in note and "paid $" in note


def test_classify_reports_a_sub_cent_amount():
    """The demo has to say what it cost, and it has to be small."""
    changes = changed_fixture()
    sw.classify(
        changes,
        deadline=soon(),
        cap_usd=0.01,
        env={"OPENAI_API_KEY": "x"},
        runner=lambda jobs, dl: [ok_result(j, "LABEL: noise\nWHY: x") for j in jobs],
    )
    assert 0 < changes[0].paid_usd < 0.01


def test_classify_without_keys_fails_open():
    changes = changed_fixture()
    note = sw.classify(changes, deadline=soon(), cap_usd=0.01, env={})
    assert changes[0].label == ""
    assert "no classifier key" in changes[0].reason
    assert "no classifier key" in note


def test_classify_survives_a_runner_that_raises():
    changes = changed_fixture()

    def boom(jobs, deadline):
        raise RuntimeError("provider on fire")

    note = sw.classify(
        changes, deadline=soon(), cap_usd=0.01, env={"OPENAI_API_KEY": "x"}, runner=boom
    )
    assert changes[0].label == ""
    assert "provider on fire" in changes[0].reason
    assert "provider on fire" in note


def test_classify_refuses_a_deadline_after_the_board_mark():
    changes = changed_fixture()
    after_mark = sw.next_utc(*sw.BOARD_MARK_UTC) + timedelta(hours=1)
    note = sw.classify(
        changes, deadline=after_mark, cap_usd=0.01, env={"OPENAI_API_KEY": "x"}, runner=None
    )
    assert "board mark" in note
    assert "board mark" in changes[0].reason
    assert changes[0].label == ""


def test_classify_respects_the_cap():
    changes = changed_fixture()

    def never(jobs, deadline):
        raise AssertionError("must not run when the quote is over the cap")

    note = sw.classify(
        changes,
        deadline=soon(),
        cap_usd=0.0,
        env={"OPENAI_API_KEY": "x"},
        runner=never,
    )
    assert "cap" in note
    assert changes[0].label == ""


def test_classify_marks_a_failed_result_without_losing_the_row():
    changes = changed_fixture()

    def failing(jobs, deadline):
        return [Result(job=j, error="rate limited") for j in jobs]

    sw.classify(
        changes, deadline=soon(), cap_usd=0.01, env={"OPENAI_API_KEY": "x"}, runner=failing
    )
    assert changes[0].label == ""
    assert changes[0].reason == "rate limited"


def test_classify_marks_an_unparseable_reply():
    changes = changed_fixture()
    sw.classify(
        changes,
        deadline=soon(),
        cap_usd=0.01,
        env={"OPENAI_API_KEY": "x"},
        runner=lambda jobs, dl: [ok_result(j, "banana") for j in jobs],
    )
    assert changes[0].label == ""
    assert "did not name a label" in changes[0].reason


def test_classify_is_a_noop_with_nothing_changed():
    unchanged = [sw.Change(ONE[0], "unchanged")]
    assert "no changed sources" in sw.classify(unchanged, deadline=soon(), cap_usd=0.01)


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def test_render_keeps_old_rows_and_appends_new_ones():
    first = sw.render_watch_md("", ["| a |"], ONE)
    second = sw.render_watch_md(first, ["| b |"], ONE)
    assert "| a |" in second
    assert "| b |" in second
    assert second.index("| a |") < second.index("| b |")


def test_render_regenerates_the_header_once_only():
    first = sw.render_watch_md("", ["| a |"], ONE)
    second = sw.render_watch_md(first, ["| b |"], ONE)
    assert second.count("# WATCH — provider sheet drift") == 1
    assert second.count(sw._ROW_MARKER) == 1


def test_render_lists_every_source_with_its_url():
    rendered = sw.render_watch_md("", [], sw.SOURCES)
    for source in sw.SOURCES:
        assert source.name in rendered
        assert source.url in rendered


def test_row_escapes_pipes_so_the_table_survives():
    change = sw.Change(ONE[0], "changed", added=1, removed=1, reason="a | b")
    assert "a \\| b" in sw._row("2026-08-26", change)


def test_row_shows_unclassified_when_there_is_no_label():
    change = sw.Change(ONE[0], "changed", added=1, removed=1)
    assert "unclassified" in sw._row("2026-08-26", change)


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #


def test_main_writes_the_ledger_the_snapshot_and_the_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "fetch", fetcher_for(PAGE_V1))
    monkeypatch.setattr(sw, "SOURCES", ONE)

    assert sw.main(["--outdir", str(tmp_path), "--no-classify"]) == 0

    assert (tmp_path / "WATCH.md").exists()
    assert (tmp_path / "snapshots.json").exists()
    assert (tmp_path / "pages" / "openai__pricing.txt").exists()
    assert "baseline" in (tmp_path / "WATCH.md").read_text()


def test_main_publishes_rows_even_when_the_classifier_dies(tmp_path, monkeypatch):
    """The whole point: a missed classification is never a missed watch."""
    monkeypatch.setattr(sw, "fetch", fetcher_for(PAGE_V1))
    monkeypatch.setattr(sw, "SOURCES", ONE)
    sw.main(["--outdir", str(tmp_path), "--no-classify"])

    monkeypatch.setattr(sw, "fetch", fetcher_for(PAGE_V2))
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    def boom(jobs, deadline):
        raise RuntimeError("venue exploded")

    monkeypatch.setattr(sw.offpeak, "run", boom)

    assert sw.main(["--outdir", str(tmp_path)]) == 0

    watch = (tmp_path / "WATCH.md").read_text()
    assert "changed" in watch
    assert "unclassified" in watch
    assert "venue exploded" in watch


def test_main_publishes_rows_with_no_keys_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "fetch", fetcher_for(PAGE_V1))
    monkeypatch.setattr(sw, "SOURCES", ONE)
    sw.main(["--outdir", str(tmp_path), "--no-classify"])

    monkeypatch.setattr(sw, "fetch", fetcher_for(PAGE_V2))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert sw.main(["--outdir", str(tmp_path)]) == 0
    watch = (tmp_path / "WATCH.md").read_text()
    assert "changed" in watch
    assert "no classifier key" in watch


def test_main_never_touches_the_price_sheet(tmp_path, monkeypatch):
    """Detection and resolution are different jobs."""
    before_date = prices.PRICE_SHEET_DATE
    before_prices = dict(prices._PRICES)

    monkeypatch.setattr(sw, "fetch", fetcher_for(PAGE_V1))
    monkeypatch.setattr(sw, "SOURCES", ONE)
    sw.main(["--outdir", str(tmp_path), "--no-classify"])
    monkeypatch.setattr(sw, "fetch", fetcher_for(PAGE_V2))
    sw.main(["--outdir", str(tmp_path), "--no-classify"])

    assert prices.PRICE_SHEET_DATE == before_date
    assert prices._PRICES == before_prices


def test_main_rejects_an_unknown_source_name(tmp_path):
    with pytest.raises(SystemExit):
        sw.main(["--outdir", str(tmp_path), "--only", "nope:nope", "--no-classify"])


def test_sources_are_https_and_uniquely_named():
    names = [s.name for s in sw.SOURCES]
    assert len(names) == len(set(names))
    assert all(s.url.startswith("https://") for s in sw.SOURCES)


def test_every_cited_source_is_actually_cited_in_prices_py():
    """If a comment in prices.py stops naming a page, this watch is watching air."""
    source_text = Path(sw.prices.__file__).read_text(encoding="utf-8")
    for source in sw.SOURCES:
        if not source.cited:
            continue
        host_and_path = source.url.removeprefix("https://")
        assert host_and_path in source_text, f"{source.name} marked cited, absent from prices.py"


def test_job_metadata_carries_the_source_name():
    """The prompt is built per source; the job should say which."""
    change = sw.Change(ONE[0], "changed", added=1, removed=1, diff="-a\n+b")
    captured: list[Job] = []

    def runner(jobs, deadline):
        captured.extend(jobs)
        return [ok_result(j, "LABEL: noise\nWHY: x") for j in jobs]

    sw.classify(
        [change], deadline=soon(), cap_usd=0.01, env={"OPENAI_API_KEY": "x"}, runner=runner
    )
    assert captured[0].metadata["source"] == "openai:pricing"
    assert "openai:pricing" in captured[0].messages[0]["content"]


# --------------------------------------------------------------------------- #
# Rate visibility — a page whose numbers are rendered in the browser
# --------------------------------------------------------------------------- #


def test_snapshot_counts_price_figures(tmp_path):
    page = "<p>Luna $0.20 / 1M in, $1.20 / 1M out</p>"
    _, snapshots, _ = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(page))
    assert snapshots["openai:pricing"]["price_figures"] == 2


def test_snapshot_counts_zero_for_a_client_rendered_page(tmp_path):
    page = "<p>Pricing</p><div id='app'></div>"
    _, snapshots, _ = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(page))
    assert snapshots["openai:pricing"]["price_figures"] == 0


def test_source_table_flags_a_page_with_no_visible_rates(tmp_path):
    """A shell must not read as coverage."""
    page = "<p>Pricing</p><div id='app'></div>"
    _, snapshots, pages = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(page))
    rendered = sw.render_watch_md("", [], ONE, snapshots)
    assert "rendered client-side" in rendered


def test_source_table_reports_the_count_when_rates_are_visible(tmp_path):
    page = "<p>$0.20 $1.20 $4.00</p>"
    _, snapshots, _ = sw.detect_changes(ONE, tmp_path, fetcher=fetcher_for(page))
    rendered = sw.render_watch_md("", [], ONE, snapshots)
    assert "| 3 |" in rendered
    assert "rendered client-side" not in rendered


def test_render_without_snapshots_still_works():
    assert "—" in sw.render_watch_md("", [], ONE)


def test_baseline_row_has_no_classification_column_noise():
    """A baseline has no diff, so 'unclassified' would be a lie about a failure."""
    row = sw._row("2026-08-26", sw.Change(ONE[0], "baseline", detail="1 chars"))
    assert "unclassified" not in row


def test_unreachable_row_has_no_classification_column_noise():
    row = sw._row("2026-08-26", sw.Change(ONE[0], "unreachable", detail="timeout"))
    assert "unclassified" not in row
    assert "timeout" in row
