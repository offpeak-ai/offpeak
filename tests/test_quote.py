"""quote() and the CLI — arithmetic against the price sheet, no network."""

import pytest

import offpeak
from offpeak import __main__ as cli
from offpeak import job
from offpeak.job import Job
from offpeak.prices import BATCH_DISCOUNT, format_usd
from offpeak.quote import BATCH_COMPLETION_WINDOW_S, estimate_tokens, quote


def counted(model, input_tokens, output_tokens, n=1):
    return [
        Job(
            model=model,
            messages=[],
            metadata={"input_tokens": input_tokens, "output_tokens": output_tokens},
        )
        for _ in range(n)
    ]


class TestQuoteArithmetic:
    def test_batch_is_exactly_the_published_discount_off_list(self):
        q = quote(counted("gpt-5.6-luna", 1_000_000, 1_000_000), "48h")
        # gpt-5.6-luna: $0.20 in / $1.20 out per 1M, standard tier.
        assert q.list_usd == pytest.approx(1.40)
        assert q.batch_usd == pytest.approx(1.40 * BATCH_DISCOUNT)
        assert q.spread_usd == pytest.approx(0.70)
        assert q.spread_pct == pytest.approx(50.0)

    def test_scales_with_job_count(self):
        one = quote(counted("gpt-5.6-luna", 800, 200), "48h")
        many = quote(counted("gpt-5.6-luna", 800, 200, n=5000), "48h")
        assert many.list_usd == pytest.approx(one.list_usd * 5000)
        assert many.jobs == 5000

    def test_jobs_split_across_the_venues_that_would_run_them(self):
        jobs = counted("claude-haiku-4-5", 100, 10, n=3) + counted("gpt-5.6-luna", 100, 10, n=2)
        q = quote(jobs, "48h")
        assert {k: v.jobs for k, v in q.by_venue.items()} == {
            "anthropic:batch": 3,
            "openai:batch": 2,
        }
        assert q.list_usd == pytest.approx(
            sum(v.list_usd for v in q.by_venue.values())
        )

    def test_unpriced_model_is_counted_not_guessed(self):
        q = quote(counted("claude-nonexistent-9", 1000, 100), "48h")
        assert q.unpriced == 1
        assert q.list_usd == 0.0  # no guess contributed

    def test_empty_job_list_still_quotes_the_deadline(self):
        q = quote([], "48h")
        assert q.jobs == 0 and q.list_usd == 0.0
        assert q.window_seconds > 0

    def test_a_single_job_need_not_be_wrapped_in_a_list(self):
        assert quote(counted("gpt-5.6-luna", 100, 10)[0], "48h").jobs == 1


class TestNoNetwork:
    def test_quote_makes_no_api_calls(self, monkeypatch):
        # Any venue touching .client would construct a real SDK client.
        import offpeak.venues.anthropic_batch as ab
        import offpeak.venues.openai_batch as ob

        def boom(self):
            raise AssertionError("quote() must not touch a provider client")

        monkeypatch.setattr(type(ab.AnthropicBatch()), "client", property(boom))
        monkeypatch.setattr(type(ob.OpenAIBatch()), "client", property(boom))
        q = quote(counted("claude-haiku-4-5", 100, 10) + counted("gpt-5.6-luna", 100, 10), "48h")
        assert q.jobs == 2


class TestTokenBasis:
    def test_explicit_counts_win(self):
        j = Job(model="gpt-5.6-luna", messages=[], metadata={
            "input_tokens": 7, "output_tokens": 3})
        assert estimate_tokens(j)[:2] == (7, 3)
        assert estimate_tokens(j)[2] == "explicit"

    def test_input_falls_back_to_a_labeled_chars_estimate(self):
        j = job("gpt-5.6-luna", "x" * 400)
        input_tokens, _, input_basis, _ = estimate_tokens(j)
        assert input_tokens == 100  # 400 chars / 4
        assert "estimated" in input_basis

    def test_max_tokens_is_used_as_an_output_ceiling(self):
        j = job("gpt-5.6-luna", "hi", max_tokens=128)
        _, output_tokens, _, output_basis = estimate_tokens(j)
        assert output_tokens == 128
        assert "ceiling" in output_basis

    def test_unknown_output_is_labeled_not_invented(self):
        _, output_tokens, _, output_basis = estimate_tokens(job("gpt-5.6-luna", "hi"))
        assert output_tokens == 0
        assert output_basis.startswith("unknown")

    def test_system_prompt_counts_toward_the_estimate(self):
        plain = estimate_tokens(job("gpt-5.6-luna", "x" * 40))[0]
        with_system = estimate_tokens(job("gpt-5.6-luna", "x" * 40, system="y" * 40))[0]
        assert with_system > plain

    def test_basis_is_reported_on_the_quote(self):
        q = quote([job("gpt-5.6-luna", "x" * 400, max_tokens=64)], "48h")
        assert "estimated" in q.basis["input"]
        assert "ceiling" in q.basis["output"]


class TestFloorWarning:
    def test_unknown_output_makes_the_quote_a_floor(self):
        q = quote([job("claude-haiku-4-5", "summarize this")], "48h")
        assert q.is_floor and q.unknown_output == 1
        assert "FLOOR" in str(q)

    def test_a_fully_specified_quote_is_not_a_floor(self):
        q = quote(counted("claude-haiku-4-5", 100, 50), "48h")
        assert not q.is_floor
        assert "FLOOR" not in str(q)

    def test_the_floor_understates_because_output_is_the_expensive_side(self):
        # Why the warning exists: haiku output is 5x input.
        floor = quote([job("claude-haiku-4-5", "x" * 400)], "48h")
        priced = quote(counted("claude-haiku-4-5", 100, 100), "48h")
        assert priced.list_usd > floor.list_usd


class TestAssumedOutput:
    """Opt-in only. The library never invents an output size on its own."""

    def test_the_default_is_still_a_floor_not_an_assumption(self):
        q = quote([job("gpt-5.6-luna", "x" * 400)], "48h")
        assert q.is_floor and not q.is_estimated
        assert q.output_tokens == 0

    def test_a_ratio_prices_output_and_marks_the_quote_an_estimate(self):
        q = quote([job("gpt-5.6-luna", "x" * 400)], "48h", assumed_output_ratio=0.25)
        assert q.input_tokens == 100 and q.output_tokens == 25
        assert q.is_estimated and not q.is_floor
        assert q.assumed_output == 1
        assert "ratio 0.25" in q.basis["output"]

    def test_est_and_floor_are_different_markers(self):
        card = str(quote([job("gpt-5.6-luna", "x" * 400)], "48h", assumed_output_ratio=0.5))
        assert "EST" in card and "FLOOR" not in card
        floor_card = str(quote([job("gpt-5.6-luna", "x" * 400)], "48h"))
        assert "FLOOR" in floor_card and "EST" not in floor_card

    def test_a_quote_can_be_part_floor_and_part_estimate(self):
        # One job says what it expects, the other says nothing and no ratio was
        # given: the card has to carry both marks rather than pick one.
        expecting = Job(
            model="gpt-5.6-luna", messages=[{"role": "user", "content": "x" * 400}],
            metadata={"expected_output_tokens": 50},
        )
        q = quote([expecting, job("gpt-5.6-luna", "x" * 400)], "48h")
        assert q.is_estimated and q.is_floor
        assert q.assumed_output == 1 and q.unknown_output == 1
        card = str(q)
        assert "EST" in card and "FLOOR" in card

    def test_an_expectation_outranks_a_ceiling_set_for_safety(self):
        j = job("gpt-5.6-luna", "hi", max_tokens=4096)
        j.metadata = {"expected_output_tokens": 300}
        tokens, basis = estimate_tokens(j)[1], estimate_tokens(j)[3]
        assert tokens == 300
        assert basis == "assumed (expected_output_tokens)"

    def test_a_measured_count_outranks_an_expectation(self):
        j = Job(model="gpt-5.6-luna", messages=[], metadata={
            "input_tokens": 10, "output_tokens": 7, "expected_output_tokens": 999})
        assert estimate_tokens(j)[1] == 7
        assert estimate_tokens(j)[3] == "explicit"

    def test_a_ceiling_outranks_the_run_wide_ratio(self):
        # max_tokens is this job's own signal; the ratio is a blanket default.
        j = job("gpt-5.6-luna", "x" * 400, max_tokens=64)
        assert estimate_tokens(j, assumed_output_ratio=10.0)[1] == 64

    def test_the_ratio_only_touches_jobs_with_no_signal_at_all(self):
        q = quote(
            counted("gpt-5.6-luna", 1000, 100) + [job("gpt-5.6-luna", "x" * 400)],
            "48h",
            assumed_output_ratio=1.0,
        )
        assert q.output_tokens == 100 + 100  # the counted job kept its own count
        assert q.assumed_output == 1

    def test_a_ratio_above_one_is_allowed_generators_write_more_than_they_read(self):
        q = quote([job("gpt-5.6-luna", "x" * 400)], "48h", assumed_output_ratio=4.0)
        assert q.output_tokens == 400

    @pytest.mark.parametrize("bad", [0, -0.5])
    def test_a_non_positive_ratio_is_a_programming_error(self, bad):
        with pytest.raises(ValueError, match="must be positive"):
            quote([job("gpt-5.6-luna", "hi")], "48h", assumed_output_ratio=bad)

    def test_an_assumption_costs_money_which_is_the_point(self):
        floor = quote([job("claude-haiku-4-5", "x" * 4000)], "48h")
        est = quote([job("claude-haiku-4-5", "x" * 4000)], "48h", assumed_output_ratio=0.5)
        assert est.list_usd > floor.list_usd


class TestDeadlineRisk:
    def test_a_deadline_inside_the_batch_window_is_flagged(self):
        q = quote(counted("gpt-5.6-luna", 100, 10), "30m")
        assert not q.within_batch_window
        assert "risk" in str(q)

    def test_a_deadline_clear_of_the_window_is_not_flagged(self):
        q = quote(counted("gpt-5.6-luna", 100, 10), f"{BATCH_COMPLETION_WINDOW_S * 2}s")
        assert q.within_batch_window
        assert "risk " not in str(q)

    def test_a_past_deadline_is_a_programming_error(self):
        with pytest.raises(ValueError, match="not in the future"):
            quote(counted("gpt-5.6-luna", 100, 10), "2020-01-01T00:00:00Z")

    def test_an_unsupported_model_is_a_programming_error(self):
        with pytest.raises(ValueError, match="no venue supports"):
            quote(counted("mistral-large", 100, 10), "48h")


class TestQuoteCard:
    def test_sub_cent_quotes_keep_significant_digits(self):
        card = str(quote(counted("claude-haiku-4-5", 15, 5), "48h"))
        assert "$0.0000400" in card  # (15*1 + 5*5) / 1e6
        assert "$0.0000200" in card

    def test_dollar_quotes_keep_two_decimals(self):
        card = str(quote(counted("gpt-5.6-luna", 800, 200, n=5000), "48h"))
        assert "$2.00" in card and "$1.00" in card

    def test_card_names_its_price_snapshot_and_disclaims_being_a_bill(self):
        card = str(quote(counted("gpt-5.6-luna", 100, 10), "48h"))
        assert "not a bill" in card


class TestCli:
    def test_quote_subcommand_prints_a_card(self, capsys):
        rc = cli.main(["quote", "--model", "gpt-5.6-luna", "--input-tokens", "800",
                       "--output-tokens", "200", "--jobs", "5000"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "OFFPEAK QUOTE" in out
        assert "5000 job(s)" in out
        assert "$2.00" in out and "$1.00" in out

    def test_defaults_to_one_job(self, capsys):
        cli.main(["quote", "--model", "gpt-5.6-luna", "--input-tokens", "1000",
                  "--output-tokens", "1000"])
        assert "1 across 1 venue(s)" in capsys.readouterr().out

    def test_bad_deadline_exits_nonzero_without_a_traceback(self, capsys):
        rc = cli.main(["quote", "--model", "gpt-5.6-luna", "--input-tokens", "1",
                       "--output-tokens", "1", "--deadline", "not-a-time"])
        assert rc == 2
        assert "error:" in capsys.readouterr().err

    def test_negative_tokens_are_rejected(self):
        with pytest.raises(SystemExit):
            cli.main(["quote", "--model", "gpt-5.6-luna", "--input-tokens", "-1",
                      "--output-tokens", "1"])

    def test_zero_jobs_is_rejected(self):
        with pytest.raises(SystemExit):
            cli.main(["quote", "--model", "gpt-5.6-luna", "--input-tokens", "1",
                      "--output-tokens", "1", "--jobs", "0"])


class TestFormatUsdIsShared:
    def test_none_renders_as_a_dash_not_zero(self):
        assert format_usd(None) == "—"

    def test_exported_from_the_package_root(self):
        assert offpeak.format_usd(0.0000119) == "0.0000119"
