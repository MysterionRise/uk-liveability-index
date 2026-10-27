"""HTTP, AG-UI and MCP tests against the synthetic store."""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(store):
    from lix_api.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    health = client.get("/health").json()
    assert health["lsoas"] == 12
    assert health["geography"] == {
        "active": ["E", "W", "I"], "area_counts": {"E": 7, "W": 2, "I": 3},
    }  # fmt: skip


def test_rest_routes(client):
    assert client.get("/api/v1/search", params={"q": "LS6 3HN"}).json()[0]["kind"] == "postcode"
    assert client.get("/api/v1/areas/LS6 3HN").json()["neighbourhood"] == "Headingley"
    adjusted = client.get(
        "/api/v1/areas/LS6 3HN", params={"preset": "balanced", "theme_weights": '{"safety": 3}'}
    ).json()
    assert (
        adjusted["preset"].endswith("(adjusted)") and adjusted["overall_percentile_range"] is None
    )
    assert client.get("/api/v1/areas/LS6 3HN", params={"theme_weights": "nope"}).status_code == 400
    r = client.post("/api/v1/rank", json={"within": "Leeds", "level": "lsoa", "limit": 2})
    assert len(r.json()["results"]) == 2
    quiet = client.post(
        "/api/v1/rank", json={"within": "Leeds", "limit": 2, "sort_by": "indicator:noise_exposure"}
    ).json()
    assert quiet["sort_by"] == "indicator:noise_exposure" and quiet["sort_label"]
    assert client.post("/api/v1/rank", json={"sort_by": "indicator:nope"}).status_code == 400
    assert (
        client.get(
            "/api/v1/compare", params=[("areas", "LS6 3HN"), ("areas", "BN2 9QA")]
        ).status_code
        == 200
    )
    assert client.get("/api/v1/pois", params={"category": "gp", "near": "LS6 3HN"}).json()["pois"]
    assert client.get("/api/v1/presets").json()["default"] == "balanced"


def test_errors_map_to_status_codes(client):
    assert client.get("/api/v1/areas/Nowheresville XYZ").status_code == 404
    assert client.post("/api/v1/rank", json={"preset": "hermit"}).status_code == 400
    # Bad inputs are refused by the models, not honoured (#102)
    assert client.post("/api/v1/rank", json={"limit": -5}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "Leeds", "limit": -1}).status_code == 422
    # Bad input is a 400 (or a 422 from the models); 404 is for an area that does not exist
    assert client.post("/api/v1/rank", json={"level": "region"}).status_code == 400
    assert client.post("/api/v1/rank", json={"min_theme_scores": {"vibes": 1}}).status_code == 400
    # A refusal that quotes an id is still a JSON body (the pass found f-string bodies)
    r = client.post(
        "/api/v1/rank", json={"within": "Cardiff", "sort_by": "indicator:primary_school_quality"}
    )
    assert r.status_code == 400 and "not available in Wales" in r.json()["detail"]
    r = client.get('/api/v1/areas/say "hi" there')
    assert r.status_code == 404 and r.json()["detail"].startswith("Couldn't find")
    assert client.post("/api/v1/rank", json={"radius_km": -5, "within": "Leeds"}).status_code == 422
    assert client.post("/api/v1/rank", json={"theme_weights": {"safety": -1}}).status_code == 422
    assert (
        client.get(
            "/api/v1/pois", params={"category": "gp", "near": "LS6 3HN", "limit": 0}
        ).status_code
        == 422
    )
    r = client.post("/api/v1/sql", json={"query": "COPY lsoa TO '/tmp/x'"})
    assert r.status_code == 400 and "SELECT" in r.json()["detail"]


def _agui(client, text: str, state: dict | None = None) -> list[dict]:
    body = {
        "threadId": "t", "runId": "r", "state": state or {"preset": "balanced"},
        "messages": [{"id": "m1", "role": "user", "content": text}],
        "tools": [], "context": [], "forwardedProps": {},
    }  # fmt: skip
    events = []
    with client.stream("POST", "/agent", json=body, headers={"accept": "text/event-stream"}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return events


def test_agent_streams_tool_result_and_state(client):
    events = _agui(client, "Use family weights please")
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
    assert "RUN_ERROR" not in types
    start = next(e for e in events if e["type"] == "TOOL_CALL_START")
    assert start["toolCallName"] == "set_weights"
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    assert snapshot["preset"] == "family"


def test_agent_ranking_highlights_map(client):
    events = _agui(client, "What are the best areas in Leeds?")
    result = json.loads(next(e for e in events if e["type"] == "TOOL_CALL_RESULT")["content"])
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    assert snapshot["map"]["highlighted"] == [r["code"] for r in result["results"]]


def test_sql_tool_only_in_analyst_mode(store):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from pydantic_ai.ui import StateDeps

    from lix_api.agent.agent import agent
    from lix_api.agent.state import LiveabilityState

    seen: dict[str, set[str]] = {}

    def record(messages, info: AgentInfo):
        seen["tools"] = {t.name for t in info.function_tools}
        return ModelResponse(parts=[TextPart("ok")])

    model = FunctionModel(record)
    agent.run_sync("hi", model=model, deps=StateDeps(LiveabilityState(mode="consumer")))
    assert "run_sql" not in seen["tools"]
    agent.run_sync("hi", model=model, deps=StateDeps(LiveabilityState(mode="analyst")))
    assert "run_sql" in seen["tools"]


async def test_mcp_lists_and_calls_tools(store):
    from fastmcp import Client

    from lix_api.mcp_server import mcp

    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert {"search_place", "get_area_profile", "rank_areas", "explain_score"} <= names
        result = await c.call_tool("get_area_profile", {"area": "LS6 3HN"})
        assert result.structured_content["neighbourhood"] == "Headingley"


def test_agent_turns_are_logged(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    _agui(client, "Use family weights please")
    lines = (tmp_path / "logs" / "agent.jsonl").read_text().splitlines()
    record = json.loads(lines[-1])
    assert record["model"] == "test"
    assert record["tools"] == ["set_weights"]
    assert record["requests"] == 2  # the tool call, then the summary


def test_runaway_tool_loop_is_stopped(client, monkeypatch):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from lix_api.agent.agent import agent

    def loop(messages, info):
        return ModelResponse(parts=[ToolCallPart("list_indicators", {"theme": "safety"})])

    async def stream_loop(messages, info):
        from pydantic_ai.models.function import DeltaToolCall

        yield {0: DeltaToolCall(name="list_indicators", json_args='{"theme": "safety"}')}

    monkeypatch.setenv("LIX_REQUEST_LIMIT", "3")
    with agent.override(model=FunctionModel(loop, stream_function=stream_loop)):
        events = _agui(client, "loop forever")
    types = [e["type"] for e in events]
    # A plain assistant message explains it and the run finishes (no raw error toast)
    assert "RUN_ERROR" not in types and types[-1] == "RUN_FINISHED"
    text = "".join(e.get("delta", "") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "more steps than I'm allowed" in text


def test_openrouter_settings(monkeypatch):
    from lix_api.agent.models import model_settings, provider_settings

    monkeypatch.setenv("LIX_MODEL", "openrouter:anthropic/claude-opus-5.5")
    s = model_settings()
    assert s["openrouter_reasoning"] == {"effort": "low"}
    assert s["openrouter_usage"] == {"include": True}
    assert s["openrouter_provider"]["order"] == ["anthropic"]
    # The stable prefix is cached through OpenRouter as it is through Anthropic (#155)
    assert s["openrouter_cache_tool_definitions"] == "1h"
    assert s["openrouter_cache_instructions"] == "1h"
    assert s["openrouter_cache_messages"] is True
    # A fallback carries its own provider's settings, not the primary's
    qwen = provider_settings("openrouter:qwen/qwen3.8-27b")
    assert "openrouter_provider" not in qwen and "openrouter_cache_instructions" not in qwen
    assert provider_settings("anthropic:claude-haiku-5.5")["anthropic_cache_instructions"] is True
    monkeypatch.setenv("LIX_MODEL", "openrouter:meta-llama/llama-4-maverick")
    assert "openrouter_provider" not in model_settings()


def test_unknown_place_goes_back_to_the_model(client):
    # The scripted model ranks "Belfast"; the tool can't find it, and the model is told
    # so (a retry prompt) instead of the whole turn failing
    events = _agui(client, "What are the best areas in Belfast?")
    types = [e["type"] for e in events]
    assert "RUN_ERROR" not in types and types[-1] == "RUN_FINISHED"
    text = "".join(e.get("delta", "") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "couldn't" in text.lower()


def test_model_errors_get_plain_messages():
    from pydantic_ai.exceptions import ModelHTTPError

    from lix_api.agent.errors import friendly_message

    assert "rejected" in friendly_message(ModelHTTPError(401, "m"))
    assert "out of credit" in friendly_message(ModelHTTPError(402, "m"))
    assert "busy" in friendly_message(ModelHTTPError(429, "m"))
    assert "still work" in friendly_message(RuntimeError("boom"))


def test_state_changing_tools_run_before_later_tools():
    """Models often emit set_weights and rank_areas together; run in parallel, the ranking
    could read the old weights (seen in the real-model eval). Tools that change the shared
    state are barriers: tools emitted after them start only once they finish."""
    from lix_api.agent.agent import agent

    tools = agent._function_toolset.tools
    for name in ("set_weights", "show_on_map", "add_to_shortlist", "remove_from_shortlist"):
        assert tools[name].sequential, name
    assert not tools["rank_areas"].sequential


def test_set_weights_stores_theme_ids(client):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from lix_api.agent.agent import agent

    args = {"preset": "Families", "theme_weights": {"crime": 3}}

    def call(messages, info):
        if any(isinstance(p, ToolReturnPart) for p in messages[-1].parts):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("set_weights", args)])

    async def stream(messages, info):
        response = call(messages, info)
        part = response.parts[0]
        if isinstance(part, TextPart):
            yield part.content
        else:
            yield {0: DeltaToolCall(name="set_weights", json_args=json.dumps(args))}

    with agent.override(model=FunctionModel(call, stream_function=stream)):
        events = _agui(client, "weights for families, safety matters most")
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    assert snapshot["preset"] == "family" and snapshot["theme_weights"] == {"safety": 3}


def test_data_files_must_be_revalidated(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lix_api.main import DataFiles

    (tmp_path / "scores.parquet").write_bytes(b"PAR1....PAR1")
    app = FastAPI()
    app.mount("/data", DataFiles(directory=tmp_path), name="data")
    r = TestClient(app).get("/data/scores.parquet", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.headers["cache-control"] == "no-cache"


def test_replay_plays_a_recorded_turn_and_falls_back(client, tmp_path):
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart

    from lix_api.agent.agent import agent
    from lix_api.agent.replay import replay_model

    turn = [
        ModelResponse(
            parts=[ToolCallPart("set_weights", {"preset": "retiree"}, tool_call_id="c1")]
        ),
        ModelResponse(parts=[TextPart("Recorded reply about retirees.")]),
    ]
    path = tmp_path / "cassettes.json"
    path.write_text(
        json.dumps({"weights for retirees": json.loads(ModelMessagesTypeAdapter.dump_json(turn))})
    )

    with agent.override(model=replay_model(path)):
        events = _agui(client, "Weights for retirees?")
        other = _agui(client, "Use family weights please")
    text = "".join(e.get("delta", "") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert text == "Recorded reply about retirees."
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    assert snapshot["preset"] == "retiree"  # the recorded tool call ran for real
    # An unrecorded prompt falls back to the scripted model
    assert next(e for e in other if e["type"] == "TOOL_CALL_START")["toolCallName"] == "set_weights"


def test_explaining_an_area_shows_it_on_the_map(client):
    events = _agui(client, "Why is the score low in LS6 3HN?")
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    assert snapshot["map"]["selected"] == "E01000001" and snapshot["map"]["bbox"]


def test_nearby_places_bring_the_map_to_them(client):
    events = _agui(client, "Show me well-run pubs near LS6 3HN")
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    west, south, east, north = snapshot["map"]["bbox"]
    for poi in snapshot["map"]["pois"]:
        assert west <= poi["point"]["lon"] <= east and south <= poi["point"]["lat"] <= north


def _text(events: list[dict]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")


def test_limits_turns_rate_and_budget(tmp_path):
    from lix_api.agent.limits import Limits

    now = [0.0]
    day = ["2026-10-08"]
    limits = Limits(session_turns=3, rate_per_minute=2, daily_budget=0.05,
                    clock=lambda: now[0], today=lambda: day[0])  # fmt: skip
    assert limits.admit(1) is None and limits.admit(2) is None
    assert "busy" in limits.admit(3)  # two questions in the last minute
    now[0] = 61
    assert "limit of 3 questions" in limits.admit(4)
    assert limits.admit(3) is None
    limits.spend(0.03)
    limits.spend(None)
    limits.spend(0.03)
    now[0] = 200
    assert "today's budget" in limits.admit(1)
    day[0] = "2026-10-09"  # a new day, a new budget
    assert limits.admit(1) is None

    log = tmp_path / "agent.jsonl"
    log.write_text(
        '{"at": "2026-10-09T08:00:00+00:00", "cost": 0.02}\n'
        '{"at": "2026-10-08T23:59:00+00:00", "cost": 0.50}\n'
        "not json\n"
        '{"at": "2026-10-09T09:00:00+00:00", "cost": null}\n'
    )
    limits.load(log)
    assert limits.status()["spent"] == 0.02


def test_refused_questions_get_a_chat_reply(client, monkeypatch):
    from lix_api import main

    monkeypatch.setattr(main.limits, "spent", 99.0)
    monkeypatch.setattr(main.limits, "daily_budget", 1.0)
    events = _agui(client, "Use family weights please")
    types = [e["type"] for e in events]
    assert types[-1] == "RUN_FINISHED" and "RUN_ERROR" not in types
    assert "TOOL_CALL_START" not in types  # the model wasn't asked
    assert "today's budget" in _text(events)
    assert client.get("/health").json()["assistant"]["limits"]["spent"] == 99.0


def test_without_a_key_recorded_prompts_still_answer(monkeypatch, tmp_path):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart

    from lix_api.agent import models

    monkeypatch.setenv("LIX_MODEL", "openrouter:anthropic/claude-haiku-5.5")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LIX_FALLBACK_MODELS", raising=False)
    cassettes = tmp_path / "cassettes.json"
    recorded = [ModelResponse(parts=[TextPart("Recorded answer.")], model_name="m")]
    turn = json.loads(ModelMessagesTypeAdapter.dump_json(recorded))
    cassettes.write_text(json.dumps({"hello there": turn}))
    monkeypatch.setattr("lix_api.agent.replay.CASSETTES", cassettes)
    monkeypatch.setattr(models, "MODEL_PROBLEM", None)
    model = models.build_model()
    assert models.MODEL_PROBLEM and "openrouter" in models.MODEL_PROBLEM
    agent = Agent(model)
    assert agent.run_sync("Hello there?").output == "Recorded answer."
    assert "isn't configured" in agent.run_sync("Something else").output
    monkeypatch.setattr(models, "MODEL_PROBLEM", None)


def test_health_reports_version(client):
    body = client.get("/health").json()
    assert body["version"] == "0.6.3"
    assert body["assistant"]["recorded_only"] is False


def test_run_cost_prefers_what_the_provider_billed():
    from types import SimpleNamespace

    from pydantic_ai.messages import ModelResponse, TextPart

    from lix_api.agent.runlog import run_cost

    def response(cost=None):
        details = {"cost": cost} if cost is not None else None
        return ModelResponse(parts=[TextPart("x")], provider_details=details)

    estimate = SimpleNamespace(cost=0.5)
    assert run_cost([response(0.001), response(0.002)], estimate) == 0.003
    assert run_cost([response()], estimate) == 0.5
    assert run_cost([response()], SimpleNamespace(cost=None)) is None


def test_a_question_about_one_measure_ranks_by_it_and_colours_the_map(client):
    events = _agui(client, "Which neighbourhoods have the least noise in Leeds?")
    args = next(json.loads(e["delta"]) for e in events if e["type"] == "TOOL_CALL_ARGS")
    assert args["sort_by"] == "indicator:noise_exposure"
    result = json.loads(next(e for e in events if e["type"] == "TOOL_CALL_RESULT")["content"])
    assert result["sort_by"] == "indicator:noise_exposure" and result["sort_label"]
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    assert snapshot["map"]["layer"] == "indicator:noise_exposure"
    assert snapshot["map"]["highlighted"] == [r["code"] for r in result["results"]]


def test_free_models_ignore_the_budget_real_models_spent(monkeypatch):
    """The scripted and recorded assistants cost nothing: a day's real spend in the log
    must not refuse them (the release pass and the clip recordings hit this)."""
    from lix_api.agent.limits import Limits

    monkeypatch.setenv("LIX_MODEL", "replay")
    monkeypatch.setenv("LIX_DAILY_BUDGET", "1")
    limits = Limits.from_env()
    limits.spent = 3.8
    assert limits.admit(1) is None
    monkeypatch.setenv("LIX_MODEL", "openrouter:anthropic/claude-haiku-5.5")
    limits = Limits.from_env()
    limits.spent = 3.8
    assert "budget" in (limits.admit(1) or "")


OWNER = "I'm a young professional with one child going into primary school and I have a sun deficit"


def test_profile_endpoint_uses_the_rules_on_the_test_model(client):
    r = client.post("/api/v1/profile", json={"description": OWNER})
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "rules" and body["engine_reason"] == "free_model"
    assert body["preset"] == "young_professional" and body["theme_weights"]["education"] == 1.25
    welsh = client.post("/api/v1/profile", json={"description": OWNER, "locale": "cy"}).json()
    assert welsh["rationale"].startswith("Yn dechrau o")
    assert client.post("/api/v1/profile", json={"description": "ab"}).status_code == 422


def test_profile_endpoint_refuses_when_rate_limited(client, monkeypatch):
    from lix_api import main
    from lix_api.services import profiles

    monkeypatch.setattr(profiles, "engine_choice", lambda model=None: ("model", None))
    monkeypatch.setattr(main.limits, "rate_per_minute", 0)
    r = client.post("/api/v1/profile", json={"description": OWNER})
    assert r.status_code == 429 and "busy" in r.json()["detail"]


def test_profile_endpoint_falls_back_when_the_budget_is_spent(client, monkeypatch):
    from lix_api import main

    monkeypatch.setenv("LIX_MODEL", "openrouter:anthropic/claude-haiku-5.5")
    monkeypatch.setattr(main.limits, "spent", 99.0)
    monkeypatch.setattr(main.limits, "daily_budget", 1.0)
    body = client.post("/api/v1/profile", json={"description": OWNER}).json()
    assert body["engine"] == "rules" and body["engine_reason"] == "budget"


def test_set_profile_tool_updates_the_state(client):
    events = _agui(client, OWNER)
    names = [e["toolCallName"] for e in events if e["type"] == "TOOL_CALL_START"]
    assert names == ["set_profile"]
    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    assert snapshot["profile"]["name"].startswith("Young professional")
    assert snapshot["profile"]["description"] == OWNER
    assert snapshot["preset"] == "young_professional"
    assert snapshot["theme_weights"]["education"] == 1.25
    assert snapshot["indicator_weights"]["sunshine"] == 2


def test_state_lines_mention_the_profile():
    from lix_api.agent.agent import _state_lines
    from lix_api.agent.state import LiveabilityState, ProfileRef

    text = _state_lines(
        LiveabilityState(profile=ProfileRef(name="Sunny parent", rationale="Sun and schools."))
    )
    assert "Sunny parent" in text and "Sun and schools." in text


def test_profiles_and_comparisons_take_indicator_multipliers(client):
    r = client.get(
        "/api/v1/areas/LS6 3HN",
        params={"preset": "balanced", "indicator_weights": '{"sunshine": 2}'},
    )
    assert r.status_code == 200 and r.json()["preset"].endswith("(adjusted)")
    r = client.get(
        "/api/v1/compare",
        params=[("areas", "LS6 3HN"), ("areas", "BN2 9QA"), ("theme_weights", '{"safety": 3}')],
    )
    assert r.status_code == 200 and r.json()["preset"].endswith("(adjusted)")
    assert (
        client.get("/api/v1/areas/LS6 3HN", params={"indicator_weights": "nope"}).status_code == 400
    )


def test_weights_are_checked_once_for_every_route(client):
    """Null, nested, infinite, negative or oversized weights are refused with the field
    named, on the body and the query forms alike, never a 500 (#172)."""
    for bad in ('{"safety": null}', '{"no2": {"a": 1}}', '{"safety": [1]}', '{"safety": "x"}'):
        r = client.get("/api/v1/areas/E01000001", params={"theme_weights": bad})
        assert r.status_code == 400, bad
        assert "theme_weights" in r.json()["detail"]
    out_of_range = ('{"safety": Infinity}', '{"safety": NaN}', '{"safety": 1e308}')
    for bad in (*out_of_range, '{"safety": -1}', '{"safety": 9}'):
        r = client.get("/api/v1/areas/E01000001", params={"theme_weights": bad})
        assert r.status_code == 400, bad
    params = [("areas", "LS6 3HN"), ("areas", "BN2 9QA"), ("theme_weights", '{"safety": [1]}')]
    assert client.get("/api/v1/compare", params=params).status_code == 400
    assert client.post("/api/v1/rank", json={"theme_weights": {"safety": 1e308}}).status_code == 422
    assert client.post("/api/v1/rank", json={"indicator_weights": {"no2": 100}}).status_code == 422
    assert client.post("/api/v1/rank", json={"max_median_price": -1}).status_code == 422
    # A null overall gives a null percentile and band, never a mid-table 50
    r = client.get("/api/v1/areas/I01000001", params={"theme_weights": '{"safety": 0}'})
    assert r.status_code == 200


def test_references_and_bodies_are_bounded(client):
    """One request cannot pin a core: references are capped at 200 characters, a body at
    16 KB, and only the first scopes of a comma list are read (#171)."""
    long = "Leeds, " * 40
    assert client.get("/api/v1/search", params={"q": long}).status_code == 422
    assert client.post("/api/v1/rank", json={"within": long}).status_code == 422
    assert client.get("/api/v1/pois", params={"category": "gp", "near": long}).status_code == 422
    assert client.get(f"/api/v1/areas/{long}").status_code == 422
    params = [("areas", long), ("areas", "LS6 3HN")]
    assert client.get("/api/v1/compare", params=params).status_code == 422
    body = json.dumps({"within": "Leeds", "pad": "x" * 20_000})
    huge = client.post("/api/v1/rank", content=body, headers={"content-type": "application/json"})
    assert huge.status_code == 413
    # The assistant's endpoint carries the whole conversation: a long one is not a 413
    # (the v0.6.1 walkthrough was refused after four questions)
    long_chat = {
        "threadId": "t", "runId": "r", "state": {}, "tools": [], "context": [],
        "forwardedProps": {},
        "messages": [{"id": str(i), "role": "user", "content": "x" * 2000} for i in range(12)],
    }  # fmt: skip
    r = client.post("/agent", json=long_chat)
    assert r.status_code != 413
    # Twenty scopes cost no more than three: the rest are ignored
    r = client.get("/api/v1/search", params={"q": "Headingley" + ", Leeds" * 20})
    assert r.status_code == 200 and r.json()[0]["name"] == "Headingley"


def test_head_and_docs_through_the_api_prefix(client):
    """Uptime probes send HEAD; the schema and docs live under /api/v1 so the proxy can
    reach them (#178)."""
    assert client.head("/health").status_code == 200
    assert client.head("/api/v1/search?q=Leeds").status_code == 200
    assert client.get("/api/v1/openapi.json").status_code == 200
    assert client.get("/api/v1/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 404


def test_search_and_profile_speak_the_interface_language(client):
    """#187: a result's council and a profile's urban/rural class come back in the
    requested locale; the settlement type is a key the page words."""
    places = client.get("/api/v1/search", params={"q": "Cardiff", "locale": "cy"}).json()
    city = next(p for p in places if p["kind"] == "place")
    assert (city["place_type"], city["detail"]) == ("City", "Caerdydd")
    plain = client.get("/api/v1/search", params={"q": "Cardiff"}).json()
    assert next(p for p in plain if p["kind"] == "place")["detail"] == "Cardiff"
    welsh = client.get("/api/v1/areas/W01001880", params={"locale": "cy"}).json()
    assert welsh["urban_rural"] == "Urban"
    assert welsh["urban_rural_label"] == "Trefol: yn nes at dref neu ddinas fawr"
    english = client.get("/api/v1/areas/W01001880").json()
    assert english["urban_rural_label"] == "Urban"
