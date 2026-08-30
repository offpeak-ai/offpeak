"""The reconciler — the sheet against the pages, with no network at all.

Every fixture under ``fixtures/watch-pages/`` is the page text ``sheet_watch``
committed to ``board-data`` verbatim, byte for byte. That is the point: these
tests parse the same bytes CI parses, so a provider changing its table layout
breaks a test here rather than silently returning ``unverifiable`` in
production and reading as agreement.

The load-bearing test is
:func:`test_a_sheet_without_the_fast_rows_is_caught_on_opus_5`. Fast mode
shipped on Opus 5 and Opus 4.8 *before* the watch took its 2026-08-26 baseline,
so no hash diff could ever have surfaced it. The reconciler is only worth
shipping if it catches exactly that, and the test removes the rows from the
sheet to prove it does.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from offpeak import prices

_spec = importlib.util.spec_from_file_location(
    "sheet_reconcile", Path(__file__).resolve().parent.parent / "tools" / "sheet_reconcile.py"
)
sr = importlib.util.module_from_spec(_spec)
# Same reason as test_sheet_watch: the tool's dataclasses carry string
# annotations, and dataclasses resolves those through sys.modules.
sys.modules["sheet_reconcile"] = sr
_spec.loader.exec_module(sr)

PAGES = Path(__file__).resolve().parent / "fixtures" / "watch-pages"


def page(source: str) -> str:
    return (PAGES / f"{source}__pricing.txt").read_text(encoding="utf-8")


def all_pages() -> dict[str, str]:
    return {name: page(name) for name in (*sr.PARSERS, *sr.SKIPPED)}


def mismatches(source: str) -> list[sr.Finding]:
    findings, _ = sr.reconcile_source(source, page(source))
    return [f for f in findings if f.kind == "mismatch"]


# --------------------------------------------------------------------------- #
# Reading a cell
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line,expected",
    [
        ("$10", 10.0),
        ("$0.075", 0.075),
        ("$ 4", 4.0),
        ("$1,000", 1000.0),
        # Google qualifies the figure in the same cell; the first number is the
        # rate that applies to the shape this sheet prices.
        ("$0.54 (text / image / video / audio)", 0.54),
        ("$2.00, prompts <= 200k tokens", 2.0),
        ("$0.75 through December 31, 2026.", 0.75),
        ("Free of charge", None),
        ("Not available", None),
        # Prose is not a rate: a cell starts with its figure.
        ("Groq Closes $350 million Series A", None),
    ],
)
def test_a_cell_yields_its_rate_and_prose_does_not(line, expected):
    assert sr._money(line) == expected


def test_only_a_per_million_token_cell_counts_as_a_token_rate():
    assert sr._mtok("$10 / MTok") == 10.0
    assert sr._mtok("$4 / 1000 pages") is None
    assert sr._mtok("$0.003") is None


def test_a_cached_column_is_neither_input_nor_output():
    # They are separately published rates for a different thing, and the sheet
    # does not carry them. Reading "Cached input" as input would price a cache
    # hit as a prompt.
    assert sr._column_role("Cached input") is None
    assert sr._column_role("Cache writes") is None
    assert sr._column_role("Base Input Tokens") == "input"
    assert sr._column_role("Output Tokens") == "output"
    assert sr._column_role("Batch input") == "input"


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #


class TestAnthropicPage:
    def test_the_standard_and_batch_tables_both_parse(self):
        rows = sr.parse_anthropic(page("anthropic"))
        assert rows["claude-sonnet-5"]["input"] == 2.0
        assert rows["claude-sonnet-5"]["output"] == 10.0
        assert rows["claude-sonnet-5"]["batch_input"] == 1.0
        assert rows["claude-sonnet-5"]["batch_output"] == 5.0

    def test_a_shared_row_label_prices_both_models(self):
        # The fast table's only row is "Claude Opus 5 / Claude Opus 4.8" — one
        # label, two models, one published rate.
        rows = sr.parse_anthropic(page("anthropic"))
        assert rows["claude-opus-5"]["fast_input"] == 10.0
        assert rows["claude-opus-5"]["fast_output"] == 50.0
        assert rows["claude-opus-4-8"]["fast_input"] == 10.0
        assert rows["claude-opus-4-8"]["fast_output"] == 50.0

    def test_fast_mode_is_not_spread_across_the_rest_of_the_table(self):
        rows = sr.parse_anthropic(page("anthropic"))
        for model in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5"):
            assert rows[model]["fast_input"] is None, model

    def test_a_retired_row_keeps_its_id_without_the_parenthetical(self):
        rows = sr.parse_anthropic(page("anthropic"))
        assert rows["claude-opus-4-1"]["input"] == 15.0

    def test_the_prose_on_the_page_is_not_read_as_a_row(self):
        # The page is mostly sentences that begin with the word "Claude".
        rows = sr.parse_anthropic(page("anthropic"))
        assert all(
            row["input"] is not None or row["fast_input"] is not None for row in rows.values()
        )
        assert len(rows) < 30

    def test_the_page_still_says_sonnet_5s_increase_was_cancelled(self):
        # The second correction this reconciler was written for. It is prose,
        # not a rate, so nothing below compares it — but if the sentence ever
        # goes away, the reason the sheet carries no PromoNote goes with it.
        text = page("anthropic")
        assert "is now the standard price" in text
        assert "will not occur" in text

    def test_the_sheet_agrees_with_the_page(self):
        assert mismatches("anthropic") == []


# --------------------------------------------------------------------------- #
# The row no hash diff could have caught
# --------------------------------------------------------------------------- #


def test_a_sheet_without_the_fast_rows_is_caught_on_opus_5():
    """Fast mode predates the watch's baseline, so only this can find it."""
    del prices._FAST_PRICES["claude-opus-5"]
    del prices._FAST_PRICES["claude-opus-4-8"]

    found = {(f.model, f.field): f for f in mismatches("anthropic")}
    assert ("claude-opus-5", "fast_input") in found
    assert ("claude-opus-5", "fast_output") in found
    assert ("claude-opus-4-8", "fast_input") in found

    finding = found[("claude-opus-5", "fast_input")]
    assert finding.page == 10.0
    assert finding.sheet is None
    assert "carries no row" in finding.note


def test_the_shipped_sheet_has_no_mismatch_left_on_opus_5():
    """The other half of the proof: the correction is actually in prices.py."""
    assert prices.get_fast_price("claude-opus-5") == (10.0, 50.0)
    assert [f for f in mismatches("anthropic") if f.model.startswith("claude-opus")] == []


def test_a_rate_that_moved_is_caught_as_well_as_one_that_is_absent():
    prices.register_price("claude-sonnet-5", 3.00, 15.00)  # the cancelled rise
    found = {(f.model, f.field): f for f in mismatches("anthropic")}
    assert found[("claude-sonnet-5", "input")].page == 2.0
    assert found[("claude-sonnet-5", "input")].sheet == 3.0
    assert found[("claude-sonnet-5", "batch_input")].sheet == 1.5


# --------------------------------------------------------------------------- #
# OpenAI, Google, Mistral
# --------------------------------------------------------------------------- #


class TestOpenAIPage:
    def test_every_tier_on_the_gpt_5_6_family_parses(self):
        rows = sr.parse_openai(page("openai"))
        sol = rows["gpt-5.6-sol"]
        assert (sol["input"], sol["output"]) == (4.0, 20.0)
        assert (sol["batch_input"], sol["batch_output"]) == (2.0, 10.0)
        assert (sol["fast_input"], sol["fast_output"]) == (8.0, 40.0)

    def test_the_short_context_column_is_the_one_taken(self):
        # The header runs twice, short then long. The sheet stores the short
        # rate, so the parser must take the first Input and the first Output —
        # sol's long-context input is $8.00, which is also its fast rate.
        assert sr.parse_openai(page("openai"))["gpt-5.6-sol"]["input"] == 4.0

    def test_flex_is_parsed_and_dropped_rather_than_stored_as_batch(self):
        # Flex prices identically to batch on this family, so a parser that
        # confused them would agree with the sheet for the wrong reason.
        assert all(
            not key.startswith("flex_") for key in sr.parse_openai(page("openai"))["gpt-5.6-sol"]
        )

    def test_the_sheet_agrees_with_the_page(self):
        assert mismatches("openai") == []


class TestGooglePage:
    def test_a_promotional_rate_reads_as_todays_number(self):
        # "$0.75 through December 31, 2026." then "$1.50 starting January 1,
        # 2027." — the sheet stores the first, and carries the second as a
        # PromoNote.
        rows = sr.parse_google(page("google"))
        assert rows["gemini-3.7-flash"]["input"] == 0.75
        assert rows["gemini-3.7-flash"]["output"] == 3.75
        assert prices.get_promo_note("gemini-3.7-flash").post_promo == (1.50, 7.50)

    def test_a_context_tiered_rate_reads_as_the_short_context_number(self):
        rows = sr.parse_google(page("google"))
        assert rows["gemini-3.1-pro-preview"]["input"] == 2.00
        assert rows["gemini-3.1-pro-preview"]["output"] == 12.00

    def test_one_block_naming_two_ids_prices_both(self):
        rows = sr.parse_google(page("google"))
        assert rows["gemini-3.1-pro-preview-customtools"]["input"] == 2.00

    def test_priority_is_read_as_the_fast_tier(self):
        # OpenAI's own page settles the synonym: "Priority processing was
        # renamed Fast mode on July 30, 2026."
        assert sr.parse_google(page("google"))["gemini-3.7-flash"]["fast_input"] == 1.35

    def test_googles_priority_tier_is_reported_as_drift(self):
        # A finding this tool surfaced and deliberately did not act on: the
        # sheet says Google sells no fast tier, and the page prices one. It is
        # a row for a human, which is the whole contract here.
        found = {(f.model, f.field) for f in mismatches("google")}
        assert ("gemini-3.7-flash", "fast_input") in found

    def test_the_standard_and_batch_legs_still_agree(self):
        off_fast = [f for f in mismatches("google") if not f.field.startswith("fast_")]
        assert off_fast == []


class TestMistralPage:
    def test_a_card_grid_yields_its_family_rates(self):
        rows = sr.parse_mistral(page("mistral"))
        medium = rows["mistral-medium-latest"]
        assert (medium["input"], medium["output"]) == (1.5, 7.5)
        assert rows["ministral-3b-latest"]["input"] == 0.1

    def test_an_embedding_card_carries_input_only(self):
        # The page lists no output rate. None is not zero: the sheet stores 0.00
        # as the published answer, and this stores "not on the page".
        rows = sr.parse_mistral(page("mistral"))
        assert rows["mistral-embed"]["input"] == 0.1
        assert rows["mistral-embed"]["output"] is None

    def test_a_card_does_not_inherit_the_block_above_it(self):
        # The fine-tuned classifier blocks carry Input/Output labels and no id
        # of their own. A forward-accumulating parser hands their rates to
        # mistral-moderation, which is free.
        assert sr.parse_mistral(page("mistral"))["mistral-moderation-2603"]["input"] is None

    def test_the_sheet_agrees_with_the_page(self):
        assert mismatches("mistral") == []

    def test_batch_and_priority_are_unverifiable_rather_than_confirmed(self):
        findings, _ = sr.reconcile_source("mistral", page("mistral"))
        unverifiable = {
            f.field for f in findings if f.kind == "unverifiable" and f.model == "mistral-medium"
        }
        assert unverifiable == {"batch_input", "batch_output", "fast_input", "fast_output"}


# --------------------------------------------------------------------------- #
# Groq: the page that cannot be read, and must say so
# --------------------------------------------------------------------------- #


def test_groq_is_unverifiable_and_never_ok():
    findings, parsed = sr.reconcile_source("groq", page("groq"))
    assert parsed == {}
    assert [f.kind for f in findings] == ["unverifiable"]
    assert "client-side" in findings[0].note


def test_groqs_one_dollar_figure_is_a_funding_round_not_a_rate():
    assert "$350 million" in page("groq")
    assert sr.reconcile(all_pages()).parsed["groq"] == 0


# --------------------------------------------------------------------------- #
# Lining the two sides up
# --------------------------------------------------------------------------- #


class TestMatching:
    def test_a_family_prefix_matches_the_providers_latest_alias(self):
        rows = sr.parse_mistral(page("mistral"))
        assert sr.match_row("mistral-medium", rows)["model"] == "mistral-medium-latest"

    def test_a_family_prefix_does_not_bind_to_a_different_product(self):
        # "codestral" is a prefix of both codestral-latest ($0.30 / $0.90) and
        # codestral-embed ($0.15, input only). The -latest rule settles it.
        rows = sr.parse_mistral(page("mistral"))
        assert sr.match_row("codestral", rows)["model"] == "codestral-latest"

    def test_an_exact_id_wins_over_a_longer_one_that_starts_with_it(self):
        rows = sr.parse_google(page("google"))
        assert sr.match_row("gemini-3.5-flash", rows)["model"] == "gemini-3.5-flash"

    def test_a_sheet_id_the_page_never_prints_is_missing_not_a_mismatch(self):
        # glm-5-2 is on the sheet as an alias; Mistral prints only zai-glm-5-2.
        findings, _ = sr.reconcile_source("mistral", page("mistral"))
        missing = [f for f in findings if f.kind == "missing"]
        assert [f.model for f in missing] == ["glm-5-2"]

    def test_a_page_model_off_the_sheet_is_informational_not_drift(self):
        findings, _ = sr.reconcile_source("anthropic", page("anthropic"))
        unpriced = {f.model for f in findings if f.kind == "unpriced"}
        assert "claude-mythos-5" in unpriced
        assert not [f for f in findings if f.kind == "mismatch"]

    @pytest.mark.parametrize(
        "model,source",
        [
            ("claude-opus-5", "anthropic"),
            ("gpt-5.6-sol", "openai"),
            ("gemini-3.7-flash", "google"),
            ("mistral-medium", "mistral"),
            ("zai-glm-5-2", "mistral"),
            # Groq serves the open-weight gpt-oss models under an "openai/"
            # prefix. It must not be read as OpenAI's.
            ("openai/gpt-oss-120b", "groq"),
        ],
    )
    def test_every_sheet_model_is_owned_by_exactly_one_source(self, model, source):
        assert sr.owner(model) == source

    def test_the_sheets_side_applies_the_batch_rule_rather_than_storing_it(self):
        rates = sr.sheet_rates("claude-opus-5")
        assert rates["batch_input"] == 5.00 * prices.BATCH_DISCOUNT
        assert rates["fast_input"] == 10.00


# --------------------------------------------------------------------------- #
# Failing softly
# --------------------------------------------------------------------------- #


def test_an_absent_page_is_unverifiable_rather_than_a_crash():
    findings, parsed = sr.reconcile_source("anthropic", None)
    assert parsed == {}
    assert findings[0].kind == "unverifiable"
    assert "no committed page text" in findings[0].note


def test_a_page_that_stopped_looking_like_itself_reports_nothing_rather_than_ok():
    findings, _ = sr.reconcile_source("anthropic", "<h1>We have moved</h1>")
    assert not [f for f in findings if f.kind == "mismatch"]
    assert {f.kind for f in findings} == {"missing"}


def test_a_parser_that_raises_becomes_a_finding(monkeypatch):
    def boom(_text):
        raise RuntimeError("table gone")

    monkeypatch.setitem(sr.PARSERS, "anthropic", boom)
    findings, _ = sr.reconcile_source("anthropic", page("anthropic"))
    assert findings[0].kind == "unverifiable"
    assert "parser failed" in findings[0].note


# --------------------------------------------------------------------------- #
# The watch's classification, borrowed
# --------------------------------------------------------------------------- #


def test_the_newest_classification_per_source_is_picked_up():
    labels = sr.latest_classifications((PAGES / "WATCH.md").read_text(encoding="utf-8"))
    assert labels["openai"] == "copy change (2026-08-28)"
    assert labels["anthropic"] == "unclassified (2026-08-27)"


def test_a_source_with_no_classified_row_has_no_label():
    assert "groq" not in sr.latest_classifications((PAGES / "WATCH.md").read_text())


def test_a_watch_ledger_with_no_rows_yields_nothing():
    assert sr.latest_classifications("# WATCH\n\nnothing yet\n") == {}


# --------------------------------------------------------------------------- #
# The report and the CLI
# --------------------------------------------------------------------------- #


def test_the_report_names_the_sheet_it_reconciled_against():
    report = sr.reconcile(all_pages())
    rendered = sr.render_reconcile_md(report, {}, "2026-08-28")
    assert prices.sheet_date() in rendered
    assert "no number here has edited the price sheet" in rendered.lower()


def test_a_skipped_source_reads_as_skipped_not_as_agreement():
    report = sr.reconcile(all_pages())
    rendered = sr.render_reconcile_md(report, {}, "2026-08-28")
    row = next(line for line in rendered.splitlines() if line.startswith("| `groq`"))
    assert "skipped" in row
    assert "ok" not in row


def test_the_classification_lands_in_the_issue_body():
    report = sr.reconcile(all_pages())
    bodies = sr.issue_bodies(report, {"google": "price change (2026-08-27)"}, "2026-08-28")
    assert "price change (2026-08-27)" in bodies["google"]["body"]
    assert "gemini-3.7-flash fast_input" in bodies["google"]["body"]
    assert "never edits" in bodies["google"]["body"]


def test_only_sources_with_something_to_report_get_an_issue():
    report = sr.reconcile(all_pages())
    bodies = sr.issue_bodies(report, {}, "2026-08-28")
    assert "google" in bodies
    assert "anthropic" not in bodies
    assert "openai" not in bodies


def test_main_writes_both_artifacts_and_exits_non_zero_on_drift(tmp_path):
    code = sr.main(["--pages", str(PAGES), "--outdir", str(tmp_path), "--date", "2026-08-28"])
    assert code == 1  # Google's priority tier
    report = (tmp_path / "RECONCILE.md").read_text()
    assert "gemini-3.7-flash" in report
    payload = json.loads((tmp_path / "reconcile.json").read_text())
    assert payload["google"]["mismatches"] == 10
    assert payload["google"]["title"] == "sheet drift: google"


def test_main_exits_zero_when_nothing_mismatches(tmp_path, monkeypatch):
    # Every source but Google agrees today, so a run over just those is clean —
    # which is what a green day is supposed to look like.
    monkeypatch.delitem(sr.PARSERS, "google")
    assert sr.main(["--pages", str(PAGES), "--date", "2026-08-28"]) == 0


def test_main_never_touches_the_price_sheet(tmp_path):
    """Detection and resolution are different jobs — same rule as the watch."""
    before_prices = dict(prices._PRICES)
    before_fast = dict(prices._FAST_PRICES)
    before_date = prices.sheet_date()

    sr.main(["--pages", str(PAGES), "--outdir", str(tmp_path), "--date", "2026-08-28"])

    assert prices._PRICES == before_prices
    assert prices._FAST_PRICES == before_fast
    assert prices.sheet_date() == before_date


def test_a_pages_dir_with_nothing_in_it_is_survivable(tmp_path):
    assert sr.main(["--pages", str(tmp_path), "--date", "2026-08-28"]) == 0
