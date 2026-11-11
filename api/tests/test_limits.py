"""The daily budget's accounting (#254): what a run counts for and whose runs count."""

import json
import logging
import os

import pytest

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


# What the base record's tokens count for at the cautious price, in and out
TOKENS_IN = 10_000 * INPUT_PRICE_PER_TOKEN
TOKENS_OUT = 1_000 * OUTPUT_PRICE_PER_TOKEN


def _line(field: str | None = None, raw: str = "null", **fields) -> str:
    """The base record as a line of the log, with ``field`` set to the JSON text ``raw``:
    NaN, 1e999 and four hundred digits are not what ``json.dumps`` writes."""
    record = _record(**fields)
    if field is None:
        return json.dumps(record)
    record[field] = "<raw>"
    return json.dumps(record).replace('"<raw>"', raw)


def _no_cost(field: str, raw: str) -> str:
    return _line(field, raw, cost=None)


ODD_LINES = [
    # Not a run record at all: left out
    pytest.param("null", 0, id="null"),
    pytest.param("5", 0, id="a whole number"),
    pytest.param("1.5", 0, id="a decimal"),
    pytest.param('"a string"', 0, id="a string"),
    pytest.param("[1, 2]", 0, id="a list"),
    pytest.param("true", 0, id="true"),
    pytest.param("{", 0, id="cut short"),
    pytest.param("1" + "0" * 5_000, 0, id="more digits than Python reads"),
    pytest.param("[" * 100_000, 0, id="nested past the parser's depth"),
    pytest.param(b"\xff\xfe\x00 not text", 0, id="bytes that are not UTF-8"),
    # A cost that cannot be used: the run counts by its tokens, like one nobody priced
    pytest.param(_line("cost", '"0.5"'), TOKENS_IN + TOKENS_OUT, id="cost as a string"),
    pytest.param(_line("cost", "[0.5]"), TOKENS_IN + TOKENS_OUT, id="cost as a list"),
    pytest.param(_line("cost", '{"usd": 0.5}'), TOKENS_IN + TOKENS_OUT, id="cost as an object"),
    pytest.param(_line("cost", "true"), TOKENS_IN + TOKENS_OUT, id="cost true"),
    pytest.param(_line("cost", "-99.9"), TOKENS_IN + TOKENS_OUT, id="a negative cost"),
    pytest.param(_line("cost", "NaN"), TOKENS_IN + TOKENS_OUT, id="cost NaN"),
    pytest.param(_line("cost", "Infinity"), TOKENS_IN + TOKENS_OUT, id="cost Infinity"),
    pytest.param(_line("cost", "-Infinity"), TOKENS_IN + TOKENS_OUT, id="cost -Infinity"),
    pytest.param(_line("cost", "1e999"), TOKENS_IN + TOKENS_OUT, id="cost past a float"),
    pytest.param(_line("cost", "1" + "0" * 400), TOKENS_IN + TOKENS_OUT, id="cost of 400 digits"),
    # A token count that cannot be used counts for nothing; the other one still counts
    pytest.param(_no_cost("input_tokens", '"10000"'), TOKENS_OUT, id="tokens as a string"),
    pytest.param(_no_cost("input_tokens", "[10000]"), TOKENS_OUT, id="tokens as a list"),
    pytest.param(_no_cost("output_tokens", '{"n": 1000}'), TOKENS_IN, id="tokens as an object"),
    pytest.param(_no_cost("input_tokens", "-10000"), TOKENS_OUT, id="negative tokens"),
    pytest.param(_no_cost("output_tokens", "NaN"), TOKENS_IN, id="tokens NaN"),
    pytest.param(_no_cost("input_tokens", "true"), TOKENS_OUT, id="tokens true"),
    pytest.param(_no_cost("output_tokens", "1e999"), TOKENS_IN, id="tokens past a float"),
    # Who answered, in a shape the app never writes: counted as a paid model's run
    pytest.param(_no_cost("models", "5"), TOKENS_IN + TOKENS_OUT, id="models a number"),
    pytest.param(_no_cost("models", "[[1]]"), TOKENS_IN + TOKENS_OUT, id="models a list of lists"),
    pytest.param(_no_cost("models", "true"), TOKENS_IN + TOKENS_OUT, id="models true"),
    # Fields that say whose run it was and when, misshapen: not today's, or not the app's
    pytest.param(_line("at", "20261110"), 0, id="a date that is a number"),
    pytest.param(_line("at", "null"), 0, id="no date"),
    pytest.param(_line("source", '["app"]'), 0, id="source as a list"),
    # A stray byte in a record's text spoils nothing: the record is still read
    pytest.param(_line(cost=0.5).encode().replace(b"chat", b"ch\xffat"), 0.5, id="a stray byte"),
    pytest.param(_line("model", "7", cost=0.5), 0.5, id="model a number"),
    pytest.param(_line("cost", "1"), 1.0, id="cost a whole number"),
]


@pytest.mark.parametrize(("line", "counted"), ODD_LINES)
def test_an_odd_line_in_the_log_spoils_only_itself(tmp_path, line, counted):
    """One line reading ``null`` in data/logs/agent.jsonl stopped the API starting (it
    restarted in a loop), and a negative or NaN cost switched the day's budget off
    without a word (v0.7.2 pass). Whatever a line holds, the runs logged around it are
    counted exactly, and the day's spend stays a number that a budget can be held to."""
    log = tmp_path / "agent.jsonl"
    odd = line if isinstance(line, bytes) else line.encode()
    around = _line(cost=0.25).encode(), _line(cost=0.5).encode()
    log.write_bytes(b"\n".join([around[0], odd, around[1]]) + b"\n")
    limits = _limits(budget=2.0)
    limits.load(log)
    assert limits.spent == pytest.approx(0.75 + counted, abs=1e-9)
    assert not limits.budget_spent()
    limits.spend(2.0)
    assert limits.budget_spent()  # nan was never "greater than or equal to" a budget


def test_odd_lines_are_reported_once_by_their_number_alone(tmp_path, caplog):
    log = tmp_path / "agent.jsonl"
    lines = [
        _line(cost=0.25),
        "null",
        '"MARKER-7f3a"',
        "",  # an empty line is nothing to report
        _line("cost", '"MARKER-7f3a"'),
        "not json MARKER-7f3a",
        _line(cost=0.5, at="2026-11-09T23:59:00+00:00"),
    ]
    log.write_text("\n".join(lines) + "\n")
    limits = _limits()
    with caplog.at_level(logging.WARNING):
        limits.load(log)
    assert limits.spent == pytest.approx(0.25 + TOKENS_IN + TOKENS_OUT, abs=1e-9)
    said = [r.getMessage() for r in caplog.records if "agent.jsonl" in r.getMessage()]
    assert len(said) == 1 and said[0].startswith("4 lines of agent.jsonl")
    assert "MARKER" not in caplog.text
    # A log of well-formed records has nothing to report
    caplog.clear()
    log.write_text(_line(cost=0.25) + "\n")
    with caplog.at_level(logging.WARNING):
        limits.load(log)
    assert limits.spent == 0.25 and not caplog.records


@pytest.mark.parametrize("problem", ["a directory in its place", "no permission to read it"])
def test_a_log_that_cannot_be_opened_counts_nothing(tmp_path, caplog, problem):
    log = tmp_path / "agent.jsonl"
    if problem == "a directory in its place":
        log.mkdir()
    else:
        log.write_text(_line(cost=0.25) + "\n")
        log.chmod(0o000)
        if os.access(log, os.R_OK):
            pytest.skip("this user reads any file (root)")
    limits = _limits()
    with caplog.at_level(logging.WARNING):
        limits.load(log)
    assert limits.spent == 0.0
    said = [r.getMessage() for r in caplog.records if "agent.jsonl" in r.getMessage()]
    assert len(said) == 1 and "could not be read" in said[0]
