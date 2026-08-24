"""The flexibility report — reading a log, classifying it, and pricing the wait.

Every test here is arithmetic and text. The tool makes no API calls and needs no
key, which is the point of it: it prices somebody's own log against a published
sheet before anyone spends anything.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "flexibility_report",
    Path(__file__).resolve().parent.parent / "tools" / "flexibility_report.py",
)
fr = importlib.util.module_from_spec(_spec)
sys.modules["flexibility_report"] = fr
_spec.loader.exec_module(fr)

_syn_spec = importlib.util.spec_from_file_location(
    "make_synthetic_log",
    Path(__file__).resolve().parent.parent / "tools" / "make_synthetic_log.py",
)
syn = importlib.util.module_from_spec(_syn_spec)
sys.modules["make_synthetic_log"] = syn
_syn_spec.loader.exec_module(syn)

NOW = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)


def row(**over):
    base = {
        "job_class": "evals",
        "model": "claude-sonnet-5",
        "venue": "anthropic",
        "venue_tier": "standard",
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "submitted_at": "2026-08-01T00:00:00Z",
        "required_by": "2026-08-05T00:00:00Z",
    }
    base.update(over)
    return fr.read_row(base)


class TestClassification:
    def test_a_deadline_past_the_batch_window_is_deferrable(self):
        assert row().classification == "deferrable"

    def test_exactly_the_window_counts_as_deferrable(self):
        # 24h is the window every batch tier here publishes; the boundary is
        # inclusive, and the report says so in the rule it prints.
        assert row(required_by="2026-08-02T00:00:00Z").classification == "deferrable"

    def test_slack_inside_the_window_is_marginal_not_deferrable(self):
        # A batch usually lands far inside its window, but "usually" is not an
        # SLA and the fallback that protects the deadline pays list.
        assert row(required_by="2026-08-01T06:00:00Z").classification == "marginal"

    @pytest.mark.parametrize("value", ["interactive", "none", "None", "", "n/a", "-", None])
    def test_the_control_group_is_never_deferrable(self, value):
        # If a report ever counts a job with a human waiting on it as savable,
        # the report is broken. This is the test that catches that.
        assert row(required_by=value).classification == "interactive"

    def test_a_missing_required_by_key_is_interactive_not_a_crash(self):
        record = {"job_class": "x", "model": "claude-sonnet-5", "input_tokens": 10}
        assert fr.read_row(record).classification == "interactive"

    def test_an_unparseable_deadline_is_interactive_and_says_so(self):
        r = row(required_by="next tuesday-ish")
        assert r.classification == "interactive"
        assert any("unparseable" in p for p in r.problems)

    def test_the_rule_is_printed_in_the_report_not_buried(self):
        report = fr.render(fr.analyse([row()], now=lambda: NOW))
        assert "## The classification rule" in report
        assert "24h or more" in report
        # and it appears before any dollar table
        assert report.index("## The classification rule") < report.index(
            "## What the wait is worth"
        )


class TestLiberalInput:
    def test_tokens_can_arrive_as_a_count_times_an_average(self):
        r = fr.read_row(
            {
                "job_class": "x", "model": "claude-sonnet-5", "venue": "anthropic",
                "requests": 1000, "avg_input_tokens": 500, "avg_output_tokens": 20,
                "submitted_at": "2026-08-01T00:00:00Z",
                "required_by": "2026-08-05T00:00:00Z",
            }
        )
        assert (r.input_tokens, r.output_tokens) == (500_000, 20_000)
        assert r.estimated, "a derived count must be marked"
        assert any("500 avg x 1,000 requests" in i for i in r.inferred)

    def test_alternative_field_spellings_are_accepted(self):
        r = fr.read_row(
            {
                "class": "x", "model_id": "claude-sonnet-5", "provider": "anthropic",
                "prompt_tokens": 10, "completion_tokens": 2,
                "timestamp": "2026-08-01T00:00:00Z", "deadline": "2026-08-05T00:00:00Z",
                "tier": "on-demand",
            }
        )
        assert r.job_class == "x" and r.model == "claude-sonnet-5"
        assert (r.input_tokens, r.output_tokens) == (10, 2)
        assert r.tier == "standard"

    def test_the_venue_is_derived_from_the_model_and_recorded_as_derived(self):
        r = row(venue=None, model="gpt-5.6-luna")
        assert r.venue == "openai"
        assert any("from the model name" in i for i in r.inferred)

    def test_gpt_oss_derives_to_groq_not_openai(self):
        # The namespace is Groq's catalogue; the model name would otherwise read
        # as OpenAI's and route the whole analysis to the wrong price rule.
        assert row(venue=None, model="openai/gpt-oss-120b").venue == "groq"

    def test_naive_timestamps_are_read_as_utc_rather_than_rejected(self):
        assert row(submitted_at="2026-08-01T00:00:00").classification == "deferrable"

    def test_every_inference_reaches_the_report(self):
        report = fr.render(fr.analyse([row(venue=None, model="gpt-5.6-luna")], now=lambda: NOW))
        assert "## What was inferred" in report
        assert "from the model name" in report

    def test_a_repeated_inference_is_disclosed_once_with_a_count(self):
        # A recurring job produces the same inference on every occurrence.
        # Printing it forty times does not make the disclosure more complete.
        rows = [
            fr.read_row(
                {
                    "job_class": "x", "model": "claude-sonnet-5", "venue": "anthropic",
                    "requests": 10, "avg_input_tokens": 100,
                    "submitted_at": f"2026-08-0{d}T00:00:00Z",
                    "required_by": "2026-09-01T00:00:00Z",
                }
            )
            for d in (1, 2, 3)
        ]
        report = fr.render(fr.analyse(rows, now=lambda: NOW))
        assert report.count("tokens in = 100 avg x 10 requests") == 1
        assert "*(x3 rows)*" in report

    def test_a_log_with_nothing_inferred_says_so_plainly(self):
        report = fr.render(fr.analyse([row()], now=lambda: NOW))
        assert "Every field this report used was present in the log." in report


class TestVenueRules:
    def test_it_never_applies_a_flat_fifty_percent(self):
        assert fr.VENUE_RULES["kimi"].discount == 0.40
        assert fr.VENUE_RULES["xai"].discount == 0.20
        assert fr.VENUE_RULES["deepseek"].discount is None
        assert fr.VENUE_RULES["qwen"].discount is None

    def test_the_fifty_percent_venues_are_the_ones_that_publish_fifty(self):
        for venue in ("openai", "anthropic", "google", "mistral", "groq", "bedrock"):
            assert fr.VENUE_RULES[venue].discount == 0.50, venue

    def test_kimi_saves_forty_percent_not_fifty(self):
        r = row(venue="kimi", model="claude-sonnet-5")  # priced model, kimi rule
        assert r.incremental_usd == pytest.approx(2.00 * 0.40)

    def test_a_clock_priced_venue_is_unpriced_rather_than_given_a_tier(self):
        # DeepSeek and Qwen sell no batch tier. Their saving is real and is a
        # different instrument; pricing it as a tier would be an estimate.
        r = row(venue="deepseek", model="claude-sonnet-5")
        assert r.incremental_usd is None

    def test_the_clock_priced_venues_are_named_in_the_report(self):
        report = fr.render(
            fr.analyse([row(venue="deepseek", model="claude-sonnet-5")], now=lambda: NOW)
        )
        assert "clock-priced" in report
        assert "Not priced here: deepseek" in report

    def test_every_rule_carries_a_citation(self):
        for name, rule in fr.VENUE_RULES.items():
            assert rule.source and "." in rule.source, name

    def test_the_sources_reach_the_report(self):
        report = fr.render(fr.analyse([row()], now=lambda: NOW))
        assert fr.VENUE_RULES["anthropic"].source in report


class TestAlreadyCaptured:
    def test_work_already_on_a_batch_tier_has_no_incremental_left(self):
        # Telling somebody they could save 50% on work they already batch is how
        # a report gets thrown away.
        r = row(venue_tier="batch")
        assert r.incremental_usd == 0.0
        assert r.already_captured_usd == pytest.approx(1.00)

    def test_it_charges_the_batch_price_for_work_that_ran_batched(self):
        assert row(venue_tier="batch").spend_usd == pytest.approx(1.00)
        assert row(venue_tier="standard").spend_usd == pytest.approx(2.00)

    def test_flex_counts_as_captured_because_it_prices_like_batch(self):
        assert row(venue_tier="flex").already_captured_usd > 0

    def test_the_headline_is_incremental_of_what_is_already_captured(self):
        analysis = fr.analyse([row(), row(venue_tier="batch")], now=lambda: NOW)
        assert analysis.already_captured_usd == pytest.approx(1.00)
        assert analysis.incremental_usd == pytest.approx(1.00)
        assert "subtracted rather than counted twice" in fr.render(analysis)

    def test_a_fleet_capturing_nothing_yet_is_told_so(self):
        assert "still on the table" in fr.render(fr.analyse([row()], now=lambda: NOW))


class TestTheDollarLayer:
    def test_an_off_sheet_model_is_unpriced_never_free(self):
        r = row(model="text-embedding-3-large")
        assert r.spend_usd is None
        assert r.list_usd is None

    def test_an_unpriced_row_does_not_void_the_totals_around_it(self):
        analysis = fr.analyse([row(), row(model="text-embedding-3-large")], now=lambda: NOW)
        assert analysis.spend_usd == pytest.approx(2.00)
        assert analysis.unpriced == 1

    def test_a_group_that_is_entirely_unpriced_reads_unknown_not_zero(self):
        # $0.00 for a group nobody could price is the report telling somebody
        # their embedding bill is nothing.
        analysis = fr.analyse([row(model="text-embedding-3-large")], now=lambda: NOW)
        assert analysis.spend_usd is None
        assert "—" in fr.render(analysis)

    def test_an_unpriced_row_makes_the_report_declare_itself_a_floor(self):
        report = fr.render(
            fr.analyse([row(), row(model="text-embedding-3-large")], now=lambda: NOW)
        )
        assert "is therefore a floor" in report
        assert "text-embedding-3-large" in report

    def test_estimated_tokens_are_marked_est_inline(self):
        analysis = fr.analyse(
            [
                fr.read_row(
                    {
                        "job_class": "x", "model": "claude-sonnet-5", "venue": "anthropic",
                        "requests": 10, "avg_input_tokens": 100,
                        "submitted_at": "2026-08-01T00:00:00Z",
                        "required_by": "2026-08-05T00:00:00Z",
                    }
                )
            ],
            now=lambda: NOW,
        )
        assert "`EST`" in fr.render(analysis)

    def test_the_sheet_date_is_on_the_page(self):
        report = fr.render(fr.analyse([row()], now=lambda: NOW))
        assert fr.prices.PRICE_SHEET_DATE in report

    def test_the_arithmetic_is_shown_per_row(self):
        report = fr.render(fr.analyse([row()], now=lambda: NOW))
        assert "1,000,000 x $2.0000" in report

    def test_there_is_no_carbon_in_v0(self):
        report = fr.render(fr.analyse([row()], now=lambda: NOW)).lower()
        for word in ("carbon", "gco2", "co2", "emissions", "intensity"):
            assert word not in report.split("## caveats")[0], word


class TestAnnualisation:
    def test_it_scales_the_observed_window_to_a_year_and_shows_the_multiplier(self):
        rows = [
            row(submitted_at="2026-08-01T00:00:00Z"),
            row(submitted_at="2026-08-31T00:00:00Z"),
        ]
        analysis = fr.analyse(rows, now=lambda: NOW)
        assert analysis.window_days == pytest.approx(30.0)
        assert analysis.annualised_usd == pytest.approx(4.00 * 365 / 30)
        assert "365/30.0" in fr.render(analysis)

    def test_a_log_that_cannot_be_dated_is_not_annualised_at_all(self):
        analysis = fr.analyse([row()], now=lambda: NOW)
        assert analysis.window_days is None
        assert analysis.annualised_usd is None
        assert "no span to project from" in fr.render(analysis)

    def test_an_explicit_window_overrides_the_observed_one(self):
        analysis = fr.analyse([row()], days=10, now=lambda: NOW)
        assert analysis.annualised_usd == pytest.approx(2.00 * 36.5)


class TestCaveats:
    def test_the_decaying_venues_are_all_flagged(self):
        report = fr.render(fr.analyse([row()], now=lambda: NOW))
        assert "2026-11-21" in report, "the sol promo"
        assert "gemini-3.7-flash" in report and "2026-12-31" in report
        assert "Qwen night-hours promotion" in report

    def test_a_sheet_promo_is_flagged_once_not_twice(self):
        # Gemini's Flash decay used to be restated in DECAY_CAVEATS as well as
        # living in PROMO_NOTES, so the report printed it twice. Reading it from
        # the sheet is what makes it disappear when the price stops being
        # promotional, instead of outliving it in a hardcoded tuple.
        subjects = [c.subject for c in fr.promo_caveats()]
        assert len(subjects) == len(set(subjects))
        assert sum("3.7-flash" in s.lower().replace(" ", "-") for s in subjects) == 1

    def test_the_sol_caveat_is_read_from_the_sheet_not_restated(self):
        # So that a price which stops being promotional stops being caveated.
        subjects = [c.subject for c in fr.promo_caveats()]
        assert any("gpt-5.6-sol" in s for s in subjects)


class TestTheArtifactSaysWhatItIs:
    def test_synthetic_is_labelled_in_the_header_not_only_the_commit(self):
        report = fr.render(fr.analyse([row()], label="synthetic", now=lambda: NOW))
        assert report.startswith("> ## ⚠️ SYNTHETIC DATA — NOT A CUSTOMER")
        assert "No real" in report

    def test_an_unlabelled_report_carries_no_banner(self):
        assert not fr.render(fr.analyse([row()], now=lambda: NOW)).startswith(">")

    def test_the_render_is_deterministic(self):
        a = fr.analyse([row()], now=lambda: NOW)
        assert fr.render(a) == fr.render(a)


class TestLoading:
    def test_it_reads_a_bare_json_list(self, tmp_path):
        path = tmp_path / "log.json"
        path.write_text(json.dumps([{"model": "claude-sonnet-5", "input_tokens": 5}]))
        assert len(fr.load_log(path)) == 1

    def test_it_reads_a_wrapped_json_object(self, tmp_path):
        path = tmp_path / "log.json"
        path.write_text(json.dumps({"jobs": [{"model": "claude-sonnet-5"}]}))
        assert len(fr.load_log(path)) == 1

    def test_it_reads_csv(self, tmp_path):
        path = tmp_path / "log.csv"
        path.write_text("job_class,model,input_tokens\nevals,claude-sonnet-5,10\n")
        rows = fr.load_log(path)
        assert rows[0].input_tokens == 10

    def test_an_unrecognisable_json_object_is_refused_with_a_reason(self, tmp_path):
        path = tmp_path / "log.json"
        path.write_text(json.dumps({"data": []}))
        with pytest.raises(ValueError, match="no jobs/rows/records/log list"):
            fr.load_log(path)


class TestTheWorkedExample:
    def test_the_generator_is_deterministic(self):
        assert syn.build_log() == syn.build_log()

    def test_the_log_declares_itself_synthetic_in_the_file(self):
        assert "GENERATED DATA" in syn.build_log()["_synthetic"]["warning"]

    def test_it_has_the_four_workloads_the_shape_calls_for(self):
        classes = {j["job_class"] for j in syn.build_log()["jobs"]}
        assert "release evals" in classes
        assert "embedding backfill" in classes
        assert "weekly report generation" in classes
        assert "interactive product surface" in classes

    def test_the_control_group_classifies_as_non_deferrable(self):
        rows = [fr.read_row(j) for j in syn.build_log()["jobs"]]
        control = [r for r in rows if r.job_class == "interactive product surface"]
        assert control, "the control group must exist"
        assert all(r.classification == "interactive" for r in control)
        summary = next(
            c for c in fr.analyse(rows, now=lambda: NOW).classes
            if c.job_class == "interactive product surface"
        )
        assert summary.deferrable_spend_usd == 0.0
        assert summary.deferrable_share == 0.0

    def test_it_exercises_every_awkward_case_the_reader_needs_to_see(self):
        rows = [fr.read_row(j) for j in syn.build_log()["jobs"]]
        assert any(r.estimated for r in rows), "tokens from an average"
        assert any(not r.estimated for r in rows), "tokens given outright"
        assert any("from the model name" in i for r in rows for i in r.inferred)
        assert any(r.tier == "batch" for r in rows), "already captured"
        assert any(r.list_usd is None for r in rows), "off the price sheet"
        assert any(r.rule and r.rule.kind == "clock" for r in rows), "clock-priced"
        assert any(r.classification == "marginal" for r in rows), "inside the window"

    def test_the_committed_example_matches_a_fresh_run(self):
        # The example in examples/ is checked in, so it has to still be what the
        # generator produces — otherwise it is a screenshot, not a worked example.
        committed = json.loads(
            (Path(__file__).resolve().parent.parent / "examples" / "synthetic-job-log.json")
            .read_text()
        )
        assert committed == syn.build_log()
