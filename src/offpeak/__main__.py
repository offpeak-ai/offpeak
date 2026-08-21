"""``python -m offpeak`` — the quote desk, from a terminal.

    python -m offpeak quote --model gpt-5.6-luna --input-tokens 800 \\
        --output-tokens 200 --jobs 5000

Prices against the bundled sheet only. No API calls, no key required.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .job import Job
from .quote import quote


def _quote_cmd(a: argparse.Namespace) -> int:
    jobs = [
        Job(
            model=a.model,
            messages=[],
            metadata={"input_tokens": a.input_tokens, "output_tokens": a.output_tokens},
        )
        for _ in range(a.jobs)
    ]
    try:
        print(quote(jobs, a.deadline))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="offpeak", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"offpeak {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    q = sub.add_parser("quote", help="price a batch of jobs against a deadline")
    q.add_argument("--model", required=True, help="e.g. gpt-5.6-luna, claude-haiku-4-5")
    q.add_argument("--input-tokens", type=int, required=True, help="per job")
    q.add_argument("--output-tokens", type=int, required=True, help="per job")
    q.add_argument("--jobs", type=int, default=1, help="how many jobs (default 1)")
    q.add_argument(
        "--deadline",
        default="24h",
        help='when it must be done: "24h", "06:00", an ISO timestamp (default 24h)',
    )
    q.set_defaults(func=_quote_cmd)

    a = ap.parse_args(argv)
    if a.jobs < 1:
        ap.error("--jobs must be at least 1")
    if a.input_tokens < 0 or a.output_tokens < 0:
        ap.error("token counts cannot be negative")
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
