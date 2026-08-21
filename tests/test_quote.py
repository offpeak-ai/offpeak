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
        # gpt-5.6-luna: $0.10 in / $0.60 out per 1M.
        assert q.list_usd == pytest.approx(0.70)
        assert q.batch_usd == pytest.approx(0.70 * BATCH_DISCOUNT)
        assert q.spread_usd == pytest.approx(0.35)
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
        assert "$1.00" in card and "$0.50" in card

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
        assert "$1.00" in out and "$0.50" in out

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
