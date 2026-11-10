"""The daily budget's accounting (#254): what a run counts for and whose runs count."""

import json
import logging

from lix_api.agent.limits import INPUT_PRICE_PER_TOKEN, OUTPUT_PRICE_PER_TOKEN, Limits, is_free


def _record(**fields) -> dict:
    base = {
        "at": "2026-11-10T12:00:00+00:00",
        "kind": "chat",
        "source": "app",
        "model": "openrouter:anthropic/claude-haiku-5.5",
        "input_tokens": 10_000,
        "output_tokens": 1_000,
        "cost": 0.01,
    }
    return base | fields


def _limits(budget: float = 1.0) -> Limits:
    return Limits(daily_budget=budget, today=lambda: "2026-11-10")


def test_a_reported_cost_is_what_counts():
    limits = _limits()
    limits.spend_run(_record(cost=0.25))
    assert limits.spent == 0.25
    limits.spend_run(None)  # a run whose logging failed
    assert limits.spent == 0.25


def test_a_paid_model_nobody_can_price_still_reaches_the_budget(caplog):
    # No provider cost and no list price: the run used to count for nothing, so the
    # budget never tripped for that model
    limits = _limits(budget=0.5)
    record = _record(model="anthropic:some-new-model", cost=None)
    with caplog.at_level(logging.WARNING):
        limits.spend_run(record)
        limits.spend_run(record)
    expected = 10_000 * INPUT_PRICE_PER_TOKEN + 1_000 * OUTPUT_PRICE_PER_TOKEN
    assert limits.spent == 2 * expected
    assert [r.message for r in caplog.records].count(caplog.records[0].message) == 1  # said once
    assert "no cost" in caplog.records[0].message
    for _ in range(3):
        limits.spend_run(record)
    assert limits.budget_spent()


def test_free_models_and_an_unset_budget_count_nothing():
    assert is_free("test") and is_free("replay") and is_free("ollama:llama3.1")
    assert not is_free("openrouter:qwen/qwen3.8-27b") and not is_free("")
    local = _limits()
    local.spend_run(_record(model="ollama:llama3.1", cost=None))
    assert local.spent == 0
    uncapped = _limits(budget=0)
    uncapped.spend_run(_record(model="anthropic:some-new-model", cost=None))
    assert uncapped.spent == 0


def test_a_restart_counts_the_apps_runs_of_today_only(tmp_path):
    # Evals and recordings on the same machine append to the same log; a background Opus
    # eval once put $0.18 on a restarted stack's counter
    log = tmp_path / "agent.jsonl"
    lines = [
        _record(cost=0.10),
        _record(cost=0.20, source="eval"),
        _record(cost=0.40, source="record"),
        _record(cost=0.05, at="2026-11-09T23:59:00+00:00"),  # yesterday
        {k: v for k, v in _record(cost=0.03).items() if k != "source"},  # written before the tag
        _record(model="anthropic:some-new-model", cost=None),  # unpriced, counted by tokens
    ]
    log.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n")
    limits = _limits()
    limits.load(log)
    unpriced = 10_000 * INPUT_PRICE_PER_TOKEN + 1_000 * OUTPUT_PRICE_PER_TOKEN
    assert abs(limits.spent - (0.10 + 0.03 + unpriced)) < 1e-9


def test_a_recording_costs_nothing_whatever_model_is_configured():
    """Started without a key, the configured model is still the paid one by name, and the
    recordings answer (``models: ["replay"]``). Counted at the cautious price, a newcomer's
    keyless demo spent its day's budget in 107 recorded answers and then refused (found
    by the v0.7.2 pass)."""
    limits = Limits(daily_budget=1.0)
    recorded = _record(model="openrouter:anthropic/claude-haiku-5.5", cost=None)
    recorded["models"] = ["replay"]
    for _ in range(200):
        limits.spend_run(recorded)
    assert limits.spent == 0.0 and not limits.budget_spent()
    # ... and the scripted model, and the reply that says a limit was reached
    for name in ("scripted", "limits"):
        limits.spend_run({**recorded, "models": [name]})
    assert limits.spent == 0.0
    # A run a paid model took part in is still counted, priced or not
    limits.spend_run({**recorded, "models": ["replay", "anthropic/claude-haiku-5.5"]})
    assert limits.spent > 0
