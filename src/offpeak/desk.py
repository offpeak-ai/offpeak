"""The hosted desk — optional, off by default, never on the hot path.

``submit()`` accepts ``desk=`` (a URL, or ``True`` to read ``OFFPEAK_DESK``) and
a key from ``OFFPEAK_KEY``. When set, the SDK asks the desk for a plan before
submitting — metadata only, never prompts or provider keys — and reports each
settled receipt back to it afterward.

Desk-optional by construction: every call here has a short timeout, and any
error, non-2xx status or malformed reply degrades straight to local behaviour.
A venue outage cancels a job; a desk outage never does.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata as _metadata
from urllib.parse import urlparse

from .job import Receipt

__all__ = ["DeskPlan", "resolve_desk", "desk_key", "request_plan", "send_receipt", "desk_host"]

TIMEOUT_S = 3.0

try:
    _SDK_VERSION = _metadata.version("offpeak")
except _metadata.PackageNotFoundError:  # pragma: no cover - uninstalled tree
    _SDK_VERSION = "0.0.0.dev0"

# venue name -> env vars that, if any is set, mean the caller can use it. Only
# presence is ever inspected — the desk gets a boolean, never the value.
_VENUE_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "anthropic:batch": ("ANTHROPIC_API_KEY",),
    "openai:batch": ("OPENAI_API_KEY",),
    "groq:batch": ("GROQ_API_KEY",),
    "mistral:batch": ("MISTRAL_API_KEY",),
    "gemini:batch": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "deepseek:clock": ("DEEPSEEK_API_KEY",),
    "qwen:batch": ("DASHSCOPE_API_KEY", "ALIBABA_API_KEY"),
}


def venue_key_signals(venue_names: list[str]) -> dict[str, bool]:
    """Which of *venue_names* the caller looks configured to use, as booleans."""
    return {
        name: any(os.environ.get(e) for e in _VENUE_KEY_ENVS.get(name, ()))
        for name in venue_names
    }


@dataclass
class DeskPlan:
    """A desk's reply to ``POST /v1/plan``."""

    plan_id: str
    venue: str | None = None
    lane: str | None = None
    submit_by: str | None = None
    rescue_at: str | None = None
    quote: dict | None = None


def resolve_desk(desk: object) -> tuple[str | None, str | None]:
    """(url, key) for *desk*, or ``(None, None)`` when the desk is off.

    ``desk=True`` reads ``OFFPEAK_DESK``; a string is used as-is; anything else
    (``None``, ``False``) turns the desk off. The key is never accepted as an
    argument — it only ever comes from ``OFFPEAK_KEY`` — so it can never end up
    written into a saved :class:`~offpeak.Ticket`.
    """
    if desk is True:
        url = os.environ.get("OFFPEAK_DESK")
    elif isinstance(desk, str):
        url = desk
    else:
        url = None
    if not url:
        return None, None
    return url.rstrip("/"), desk_key()


def desk_key() -> str | None:
    return os.environ.get("OFFPEAK_KEY")


def desk_host(desk_url: str) -> str:
    return urlparse(desk_url).netloc or desk_url


def _post(url: str, key: str | None, payload: dict, *, timeout: float) -> dict | None:
    """POST JSON to *url*; return the parsed reply, or ``None`` on any failure.

    Every failure mode — unreachable host, timeout, non-2xx, a reply that is
    not a JSON object — degrades the same way: no exception, no retry, just
    ``None``. That single behaviour is what makes the desk optional rather
    than a dependency.
    """
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.status // 100 != 2:
                return None
            body = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return body if isinstance(body, dict) else None


def request_plan(
    desk_url: str,
    key: str | None,
    *,
    deadline: datetime,
    models: list[str],
    job_count: int,
    input_tokens: int,
    output_tokens: int,
    venue_keys: dict[str, bool],
    timeout: float = TIMEOUT_S,
) -> DeskPlan | None:
    """``POST {desk_url}/v1/plan`` with metadata only.

    Never sends a prompt, a message, or a provider key — only the deadline,
    the models and job count, token estimates from the free quote, and which
    of the caller's venues look configured. Returns ``None`` on any failure,
    including a reply that lacks a usable ``plan_id``.
    """
    body = _post(
        f"{desk_url}/v1/plan",
        key,
        {
            "deadline": deadline.astimezone().isoformat(),
            "models": sorted(models),
            "job_count": job_count,
            "estimated_input_tokens": input_tokens,
            "estimated_output_tokens": output_tokens,
            "venue_keys": venue_keys,
            "sdk_version": _SDK_VERSION,
        },
        timeout=timeout,
    )
    if body is None or not isinstance(body.get("plan_id"), str) or not body["plan_id"]:
        return None
    return DeskPlan(
        plan_id=body["plan_id"],
        venue=body.get("venue") if isinstance(body.get("venue"), str) else None,
        lane=body.get("lane") if isinstance(body.get("lane"), str) else None,
        submit_by=body.get("submit_by") if isinstance(body.get("submit_by"), str) else None,
        rescue_at=body.get("rescue_at") if isinstance(body.get("rescue_at"), str) else None,
        quote=body.get("quote") if isinstance(body.get("quote"), dict) else None,
    )


def send_receipt(
    desk_url: str,
    key: str | None,
    plan_id: str,
    receipt: Receipt,
    *,
    timeout: float = TIMEOUT_S,
) -> bool:
    """``POST {desk_url}/v1/receipts`` with *receipt* plus *plan_id*.

    Returns whether the desk accepted it. The caller's settlement is already
    final either way — this is reporting, not a step the job depends on.
    """
    payload = {
        "plan_id": plan_id,
        "venue": receipt.venue,
        "model": receipt.model,
        "deadline": receipt.deadline.astimezone().isoformat(),
        "submitted_at": receipt.submitted_at.astimezone().isoformat(),
        "completed_at": (
            receipt.completed_at.astimezone().isoformat() if receipt.completed_at else None
        ),
        "input_tokens": receipt.input_tokens,
        "output_tokens": receipt.output_tokens,
        "fell_back": receipt.fell_back,
        "sla_met": receipt.sla_met,
        "list_usd": receipt.list_usd,
        "paid_usd": receipt.paid_usd,
    }
    return _post(f"{desk_url}/v1/receipts", key, payload, timeout=timeout) is not None
