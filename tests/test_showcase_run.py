"""The showcase runner — everything that happens before money moves.

These tests never submit anything and never reach the network: `quote()` makes
no API calls, and the novel cache is pre-populated on disk so `fetch_book`
takes its cached branch.
"""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "showcase_run", Path(__file__).resolve().parent.parent / "tools" / "showcase_run.py"
)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def _novel(paragraphs: int, chars: int = 600) -> str:
    """A stand-in novel: *paragraphs* blocks of *chars* each, blank-line split."""
    para = " ".join(["word"] * (chars // 5))
    return "\n\n".join(f"{i}. {para}" for i in range(paragraphs))


def _seed_cache(cache: Path, paragraphs: int = 400) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for book_id in sr.BOOKS:
        (cache / f"pg{book_id}.txt").write_text(
            "Licence preamble.\n"
            "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n\n"
            + _novel(paragraphs)
            + "\n\n*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\nLicence footer.",
            encoding="utf-8",
        )


class TestGutenbergStripping:
    def test_the_licence_header_and_footer_are_not_the_novel(self):
        raw = (
            "Licence preamble.\n"
            "*** START OF THE PROJECT GUTENBERG EBOOK THING ***\n"
            "the novel\n"
            "*** END OF THE PROJECT GUTENBERG EBOOK THING ***\n"
            "Licence footer."
        )
        assert sr.strip_gutenberg(raw) == "the novel"

    def test_a_text_without_markers_survives_intact(self):
        assert sr.strip_gutenberg("  just prose  ") == "just prose"


class TestPassages:
    def test_passages_reach_the_target_size(self):
        ps = sr.passages(_novel(200), 4000)
        assert ps
        assert all(len(p) >= 4000 for p in ps[:-1])

    def test_passages_break_on_paragraph_boundaries_not_mid_sentence(self):
        # A passage that ends mid-sentence would make the summarisation task
        # artificial, so a passage runs to the end of the paragraph that
        # crosses the target.
        text = _novel(200)
        for p in sr.passages(text, 4000):
            assert p in text

    def test_a_trailing_scrap_is_dropped_rather_than_padded(self):
        # Half a target's worth is kept; a sliver is not a passage.
        assert sr.passages("one\n\ntwo", 100_000) == []


class TestBook:
    def test_the_book_is_the_requested_size(self, tmp_path):
        cache = tmp_path / "books"
        _seed_cache(cache)
        a = sr.parse_args(["--jobs", "40"])
        jobs = sr.build_book(a, cache)
        assert len(jobs) == 40

    def test_every_job_carries_the_ceiling_and_the_model(self, tmp_path):
        cache = tmp_path / "books"
        _seed_cache(cache)
        a = sr.parse_args(["--jobs", "20", "--max-tokens", "64"])
        jobs = sr.build_book(a, cache)
        assert all(j.params["max_tokens"] == 64 for j in jobs)
        assert {j.model for j in jobs} == {sr.DEFAULT_MODEL}

    def test_the_sample_spans_every_novel_rather_than_draining_the_first(
        self, tmp_path
    ):
        cache = tmp_path / "books"
        _seed_cache(cache)
        a = sr.parse_args(["--jobs", str(len(sr.BOOKS))])
        jobs = sr.build_book(a, cache)
        # Round-robin: the first N jobs come one from each novel, so no two
        # share an opening paragraph index.
        assert len(jobs) == len(sr.BOOKS)

    def test_a_book_too_short_for_the_run_aborts_rather_than_shrinking(self, tmp_path):
        cache = tmp_path / "books"
        _seed_cache(cache, paragraphs=2)
        a = sr.parse_args(["--jobs", "2000"])
        with pytest.raises(SystemExit, match="passages available"):
            sr.build_book(a, cache)

    def test_the_passage_is_the_work_not_filler(self, tmp_path):
        cache = tmp_path / "books"
        _seed_cache(cache)
        a = sr.parse_args(["--jobs", "5"])
        jobs = sr.build_book(a, cache)
        assert "Summarize this passage in three sentences." in (
            jobs[0].messages[0]["content"]
        )


class TestGate:
    """The three branches of the gate, exercised on a book small enough to keep
    the tests fast. The window is moved to the book rather than the book to the
    window; ``TestShowcaseDefaults`` pins the production numbers separately."""

    def _run(self, tmp_path, extra):
        out = tmp_path / "run"
        _seed_cache(out / "books")
        return sr.main(["--out", str(out), "--jobs", "100", *extra]), out

    def test_a_dry_run_inside_the_window_submits_nothing(self, tmp_path, capsys):
        rc, out = self._run(
            tmp_path, ["--min-list", "0.001", "--cap", "1000", "--dry-run"]
        )
        text = capsys.readouterr().out
        assert rc == 0
        assert "inside the window" in text
        assert not (out / "handles.jsonl").exists()

    def test_over_the_cap_aborts_before_submitting(self, tmp_path, capsys):
        rc, out = self._run(tmp_path, ["--min-list", "0.001", "--cap", "0.01"])
        text = capsys.readouterr().out
        assert rc == 2
        assert "over the hard cap" in text
        assert not (out / "handles.jsonl").exists()

    def test_under_the_showcase_floor_aborts_before_submitting(self, tmp_path, capsys):
        # A book that quietly shrank is not a showcase; the floor catches it
        # before the money does.
        rc, out = self._run(tmp_path, ["--min-list", "1000", "--cap", "2000"])
        text = capsys.readouterr().out
        assert rc == 2
        assert "under the showcase floor" in text
        assert not (out / "handles.jsonl").exists()

    def test_it_refuses_to_resubmit_over_a_recorded_run(self, tmp_path, capsys):
        out = tmp_path / "run"
        _seed_cache(out / "books")
        (out / "handles.jsonl").write_text('{"venue": "anthropic:batch"}\n')
        rc = sr.main(["--out", str(out), "--jobs", "100", "--dry-run"])
        assert rc == 3
        assert "refusing to re-submit" in capsys.readouterr().out


class TestShowcaseDefaults:
    """The production numbers, pinned where a reader can see them."""

    def test_the_showcase_is_two_thousand_jobs_on_sonnet(self):
        assert sr.DEFAULT_JOBS == 2000
        assert sr.DEFAULT_MODEL == "claude-sonnet-5"

    def test_the_window_brackets_the_intended_spend(self):
        assert sr.DEFAULT_MIN_LIST_USD == 6.00
        assert sr.DEFAULT_CAP_USD == 12.00

    def test_the_ceiling_leaves_room_for_three_sentences(self):
        assert sr.DEFAULT_MAX_TOKENS >= 150

    def test_five_novels_hold_enough_text_for_the_book(self):
        # 2,000 passages of ~1,300 tokens needs ~10.4M characters, which is why
        # these five are long ones.
        assert len(sr.BOOKS) == 5
        needed = sr.DEFAULT_JOBS * sr.DEFAULT_PASSAGE_TOKENS * sr.CHARS_PER_TOKEN
        assert needed == 10_400_000
