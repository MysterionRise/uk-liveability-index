"""Recorded conversations for the demo: record once with a real model, replay offline.

    uv run python -m lix_api.agent.replay record --model openrouter:anthropic/claude-opus-5.5
    LIX_MODEL=replay uv run lix-api

A cassette maps a user prompt to the model responses of that turn (tool calls, then
the reply). Replaying returns the next recorded response for the prompt, so tools run
for real against the data and the page shows live cards; prompts that weren't recorded
fall back to the scripted model (or ``fallback``).

LIX_REPLAY_DELAY     seconds between streamed words (default 0); a little delay makes
                     recorded answers stream naturally in screen recordings
"""

import argparse
import asyncio
import json
import os
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from lix_core.paths import get_project_root

PROMPTS = get_project_root() / "config" / "demo.yaml"
CASSETTES = get_project_root() / "config" / "demo_cassettes.json"


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).strip(" ?.!")


def _turn(messages: list[ModelMessage]) -> tuple[str, int]:
    """The current user prompt and how many responses the model has given since it."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, ModelRequest):
            prompt = next((p.content for p in m.parts if isinstance(p, UserPromptPart)), None)
            if prompt is not None:
                text = prompt if isinstance(prompt, str) else json.dumps(prompt)
                answered = sum(isinstance(x, ModelResponse) for x in messages[i + 1 :])
                return normalise(text), answered
    return "", 0


def load_cassettes(path: Path = CASSETTES) -> dict[str, list[ModelResponse]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        key: [
            m
            for m in ModelMessagesTypeAdapter.validate_python(turn)
            if isinstance(m, ModelResponse)
        ]
        for key, turn in raw.items()
    }


Responder = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def replay_model(path: Path | None = None, fallback: Responder | None = None) -> FunctionModel:
    """Replays recorded turns; other prompts go to ``fallback`` (the scripted model)."""
    from lix_api.agent.models import _scripted

    cassettes = load_cassettes(path or CASSETTES)
    other = fallback or _scripted
    delay = float(os.environ.get("LIX_REPLAY_DELAY", 0))

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt, answered = _turn(messages)
        recorded = cassettes.get(prompt)
        if recorded and answered < len(recorded):
            return recorded[answered]
        return other(messages, info)

    async def stream(messages: list[ModelMessage], info: AgentInfo):
        response = respond(messages, info)
        calls = {}
        for part in response.parts:
            if isinstance(part, TextPart):
                for word in re.split(r"(?<= )", part.content):
                    if delay:
                        await asyncio.sleep(delay)
                    yield word
            elif isinstance(part, ToolCallPart):
                args = part.args if isinstance(part.args, str) else json.dumps(part.args or {})
                calls[len(calls)] = DeltaToolCall(
                    name=part.tool_name, json_args=args, tool_call_id=part.tool_call_id
                )
        if calls:
            yield calls

    return FunctionModel(respond, stream_function=stream, model_name="replay")


def without_preamble(response: ModelResponse) -> ModelResponse:
    """A response that calls tools keeps the calls, not text written beside them: that is
    thinking aloud the instructions forbid ("Need indicator id; guess raw__broadband?"),
    and a visitor watching a replay would read it (v0.7.1 hardening pass)."""
    if not any(isinstance(p, ToolCallPart) for p in response.parts):
        return response
    return replace(response, parts=[p for p in response.parts if not isinstance(p, TextPart)])


async def record(model_name: str, path: Path = CASSETTES) -> dict:
    """Run every demo prompt (a fresh conversation each) and save the model's responses."""
    import yaml
    from pydantic_ai.ui import StateDeps

    from lix_api.agent.agent import agent
    from lix_api.agent.models import model_settings
    from lix_api.agent.runlog import usage_limits
    from lix_api.agent.state import LiveabilityState

    prompts = yaml.safe_load(PROMPTS.read_text())["prompts"]
    cassettes = {}
    for item in prompts:
        deps = StateDeps(LiveabilityState(**item.get("state", {})))
        result = await agent.run(
            item["text"],
            deps=deps,
            model=model_name,
            model_settings=model_settings(),
            usage_limits=usage_limits(),
        )
        turn = [without_preamble(m) for m in result.new_messages() if isinstance(m, ModelResponse)]
        cassettes[normalise(item["text"])] = json.loads(ModelMessagesTypeAdapter.dump_json(turn))
        tools = [p.tool_name for m in turn for p in m.parts if isinstance(p, ToolCallPart)]
        print(f"recorded {item['text'][:60]!r}: {tools or 'no tools'}")
    path.write_text(json.dumps(cassettes, indent=1, ensure_ascii=False))
    return cassettes


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="Record the demo prompts with a real model")
    rec.add_argument("--model", required=True)
    args = parser.parse_args()
    if args.cmd == "record":
        os.environ["LIX_MODEL"] = args.model  # model_settings() follows the model
        os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
        asyncio.run(record(args.model))
        print(f"Wrote {CASSETTES}")


if __name__ == "__main__":
    main()
