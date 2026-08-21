from datetime import datetime, timedelta, timezone

import pytest

import offpeak
from offpeak import Receipt, job
from offpeak.prices import batch_cost_usd, get_price, list_cost_usd, register_price

NOW = datetime(2026, 8, 20, 22, 0, 0, tzinfo=timezone.utc)


def test_job_from_prompt_string():
    j = job("claude-haiku-4-5", "hello", temperature=0.2)
    assert j.messages == [{"role": "user", "content": "hello"}]
    assert j.params == {"temperature": 0.2}
    assert j.id.startswith("job_")


def test_job_with_system_and_messages():
    j = job("gpt-5.1", [{"role": "user", "content": "hi"}], system="be terse")
    assert j.messages[0] == {"role": "system", "content": "be terse"}
    assert j.messages[1]["role"] == "user"


def test_job_requires_input():
    with pytest.raises(ValueError):
        job("gpt-5.1")


def test_price_prefix_match():
    assert get_price("claude-haiku-4-5-20260101") == get_price("claude-haiku-4-5")
    assert get_price("totally-unknown-model") is None


def test_cost_math_and_override():
    # claude-haiku-4-5: $1 / $5 per 1M
    assert list_cost_usd("claude-haiku-4-5", 1_000_000, 200_000) == pytest.approx(2.0)
    assert batch_cost_usd("claude-haiku-4-5", 1_000_000, 200_000) == pytest.approx(1.0)
    assert list_cost_usd("mystery", 10, 10) is None
    register_price("mystery", 2.0, 4.0)
    assert list_cost_usd("mystery", 1_000_000, 0) == pytest.approx(2.0)


def _receipt(fell_back=False, completed=True):
    return Receipt(
        venue="anthropic:batch",
        model="claude-haiku-4-5",
        deadline=NOW + timedelta(hours=8),
        submitted_at=NOW,
        completed_at=NOW + timedelta(hours=2) if completed else None,
        input_tokens=100_000,
        output_tokens=10_000,
        fell_back=fell_back,
    )


def test_receipt_captures_the_spread():
    r = _receipt()
    assert r.sla_met
    assert r.list_usd == pytest.approx(0.15)
    assert r.paid_usd == pytest.approx(0.075)
    assert r.spread_usd == pytest.approx(0.075)


def test_fallback_pays_list_price():
    r = _receipt(fell_back=True)
    assert r.sla_met
    assert r.paid_usd == r.list_usd
    assert r.spread_usd == 0.0


def test_settlement_aggregates():
    results = [
        offpeak.Result(job=job("claude-haiku-4-5", "x"), text="ok", receipt=_receipt())
        for _ in range(4)
    ]
    settlement = offpeak.receipt(results)
    assert settlement.total == 4
    assert settlement.ok == 4
    assert settlement.sla_met == 4
    assert settlement.captured_usd == pytest.approx(0.3)
    assert settlement.captured_pct == pytest.approx(50.0)
    text = str(settlement)
    assert "OFFPEAK SETTLEMENT" in text and "captured" in text


def test_a_registered_price_does_not_leak_into_other_tests():
    # Guards the conftest fixture: test_cost_math_and_override registers
    # "mystery", and without cleanup every later test sees it.
    assert get_price("mystery") is None
    assert get_price("mystery-model") is None
