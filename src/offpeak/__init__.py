"""offpeak — deadline-priced inference.

The deadline is the input; the discount follows. Give AI work a deadline and
run it on the cheapest venue that keeps the SLA — provider batch tiers, 50%
off, today.

    import offpeak

    jobs = [offpeak.job("claude-haiku-4-5", f"Summarize:\\n\\n{d}") for d in docs]
    results = offpeak.run(jobs, deadline="06:00")
    print(offpeak.receipt(results))
"""

from importlib import metadata as _metadata

from . import prices
from .client import Settlement, default_venues, receipt, run
from .deadline import parse_deadline, seconds_until
from .job import Job, Receipt, Result, Status, job
from .prices import format_usd
from .quote import Quote, VenueQuote, quote
from .venues.base import BatchState, Venue

# The version lives in pyproject.toml and nowhere else. This used to be a
# second copy of the string, and it did what second copies do: 0.2.7 shipped
# reporting itself as 0.2.6.dev0. Read the installed metadata instead; the
# fallback only fires for a source tree that was never pip-installed.
try:
    __version__ = _metadata.version("offpeak")
except _metadata.PackageNotFoundError:  # pragma: no cover - uninstalled tree
    __version__ = "0.0.0.dev0"

__all__ = [
    "job",
    "run",
    "quote",
    "receipt",
    "Job",
    "Result",
    "Receipt",
    "Settlement",
    "Quote",
    "VenueQuote",
    "Status",
    "Venue",
    "BatchState",
    "parse_deadline",
    "seconds_until",
    "default_venues",
    "format_usd",
    "prices",
    "__version__",
]
