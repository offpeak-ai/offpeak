"""offpeak — deadline-priced inference.

Same model, same tokens, a different hour. Give AI work a deadline and run it
on the cheapest venue that keeps the SLA — provider batch tiers (−50%) today.

    import offpeak

    jobs = [offpeak.job("claude-haiku-4-5", f"Summarize:\\n\\n{d}") for d in docs]
    results = offpeak.run(jobs, deadline="06:00")
    print(offpeak.receipt(results))
"""

from . import prices
from .client import Settlement, default_venues, receipt, run
from .deadline import parse_deadline, seconds_until
from .job import Job, Receipt, Result, Status, job
from .prices import format_usd
from .quote import Quote, VenueQuote, quote
from .venues.base import BatchState, Venue

__version__ = "0.2.1.dev0"

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
