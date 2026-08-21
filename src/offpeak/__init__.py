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
from .venues.base import BatchState, Venue

__version__ = "0.1.1"

__all__ = [
    "job",
    "run",
    "receipt",
    "Job",
    "Result",
    "Receipt",
    "Settlement",
    "Status",
    "Venue",
    "BatchState",
    "parse_deadline",
    "seconds_until",
    "default_venues",
    "prices",
    "__version__",
]
