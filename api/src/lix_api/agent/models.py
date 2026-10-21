# ruff: noqa: E501 (the scripted model's rule table reads best unwrapped)
"""Which LLM drives the assistant, from the environment.

LIX_MODEL            provider:model: openrouter:anthropic/claude-haiku-5.5 (default; one
                     OPENROUTER_API_KEY reaches many providers), anthropic:claude-opus-5-5,
                     openai:gpt-5, google:gemini-2.5-pro, ollama:llama3.1 (with
                     OLLAMA_BASE_URL), or "test" for a scripted model that needs no API key
                     (end-to-end tests), or "replay" for the recorded demo conversations.
                     Without a key the assistant answers the recorded prompts and says
                     how to add one for anything else
LIX_FALLBACK_MODELS  comma-separated models to try if the first fails
LIX_EFFORT           reasoning effort (default "low": the assistant mostly picks and
                     fills in tools)
LIX_MODEL_TIMEOUT    seconds before a model request is abandoned (default 60)
"""

import json
import os
import re

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.settings import ModelSettings

from lix_core.log import setup_logging

logger = setup_logging("agent.models")

DEFAULT_MODEL = "openrouter:anthropic/claude-haiku-5.5"


def model_name() -> str:
    return os.environ.get("LIX_MODEL", DEFAULT_MODEL)


# Why the configured model couldn't be built (e.g. a missing API key), if it couldn't
MODEL_PROBLEM: str | None = None


def build_model() -> Model:
    """The model from LIX_MODEL (plus fallbacks). If it can't be built (no key, say), the
    recorded conversations answer what they can and anything else gets an explanation,
    so a missing key doesn't stop the map and API from starting."""
    global MODEL_PROBLEM
    from pydantic_ai.exceptions import UserError

    name = model_name()
    if name == "test":
        return scripted_model()
    if name == "replay":
        from lix_api.agent.replay import replay_model

        return replay_model()
    fallbacks = [
        m.strip() for m in os.environ.get("LIX_FALLBACK_MODELS", "").split(",") if m.strip()
    ]
    try:
        primary = _model(name)
        return FallbackModel(primary, *[_model(m) for m in fallbacks]) if fallbacks else primary
    except (UserError, ValueError, ImportError) as e:
        MODEL_PROBLEM = f"{name}: {e}"
        logger.error(
            f"The assistant's model can't be used ({MODEL_PROBLEM}); answering "
            "the recorded prompts only"
        )
        from lix_api.agent.replay import replay_model

        return replay_model(fallback=_explain(MODEL_PROBLEM))


def _explain(problem: str):
    """Answers any message by saying how to configure the assistant."""
    text = (
        "The assistant isn't configured with a model key, so it can only answer the "
        "suggested questions (from recordings). The map, search, weights and area profiles "
        "all work. To ask anything else, add a key to .env (see .env.example) and restart. "
        f"({problem})"
    )

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return reply


def _model(name: str) -> Model:
    """A model carrying its own provider settings, so a fallback never inherits the
    primary's (an OpenRouter Qwen fallback once asked for Anthropic's endpoint)."""
    from pydantic_ai.models import infer_model

    settings = provider_settings(name)
    if settings and name.startswith("openrouter:"):
        from pydantic_ai.models.openrouter import OpenRouterModel

        return OpenRouterModel(name.split(":", 1)[1], provider="openrouter", settings=settings)
    if settings and name.startswith("anthropic:"):
        from pydantic_ai.models.anthropic import AnthropicModel

        return AnthropicModel(name.split(":", 1)[1], provider="anthropic", settings=settings)
    return infer_model(name)


def provider_settings(name: str) -> ModelSettings:
    """What a provider needs for one model: prompt caching of the stable prefix (tool
    definitions and instructions) and effort, by the model's own provider."""
    effort = os.environ.get("LIX_EFFORT", "low")
    settings: dict = {}
    if name.startswith("anthropic:"):
        # Tool definitions are the stable prefix (tools render before the system
        # prompt); per-turn state sits in the instructions after them
        settings["anthropic_cache_tool_definitions"] = True
        settings["anthropic_cache_instructions"] = True
        settings["anthropic_effort"] = effort
    elif name.startswith("openrouter:"):
        settings["openrouter_reasoning"] = {"effort": effort}
        # Report the cost of each request, so usage limits and the turn log can use it
        settings["openrouter_usage"] = {"include": True}
        if name.split(":", 1)[1].startswith("anthropic/"):
            # Anthropic's own endpoint first, and cache breakpoints on the stable prefix:
            # without them every request re-bills the instructions and 13 tool schemas,
            # about $0.12 a question on Opus (#155)
            settings["openrouter_provider"] = {"order": ["anthropic"], "allow_fallbacks": True}
            settings["openrouter_cache_tool_definitions"] = "1h"
            settings["openrouter_cache_instructions"] = "1h"
            settings["openrouter_cache_messages"] = True
    return settings  # type: ignore[return-value]


def model_settings() -> ModelSettings:
    """The run's settings for the configured model: the generic caps plus its provider's
    (a fallback carries its own, see ``_model``)."""
    settings: dict = {"max_tokens": 4000, "timeout": float(os.environ.get("LIX_MODEL_TIMEOUT", 60))}
    settings.update(provider_settings(model_name()))
    return settings  # type: ignore[return-value]


# --- scripted model ------------------------------------------------------------------

_RULES: list[tuple[str, str, callable]] = [
    (r"^\s*(?:i'?m|i am|we'?re|we are|my (?:partner|wife|husband|family))\b"
     r"(?!.*\b(?:best|rank|where|near|around|compare|tell me|show me|why|somewhere|areas?|"
     r"neighbourhoods?|live|look|move|suit|should)\b).*",
     "set_profile",
     lambda m: {"description": m.group(0).strip()}),
    (r"\b(compare)\b(.+?)\band\b(.+)", "compare_areas",
     lambda m: {"areas": [m.group(2).strip(" ,"), m.group(3).strip(" ?.")]}),
    (r"\bwell[- ]run pubs?\b.*\b(?:near|in|around)\b (.+)", "nearest_pois",
     lambda m: {"category": "well_run_pub", "near": m.group(1).strip(" ?."), "max_km": 1.5}),
    (r"\b(family|young professional|retiree|retired|commuter|balanced)\b.*\bweights?\b|"
     r"\bweights?\b.*\b(family|young professional|retiree|retired|commuter|balanced)\b",
     "set_weights",
     lambda m: {"preset": (m.group(1) or m.group(2)).replace(" ", "_").replace("retired", "retiree")}),
    (r"\bwhy\b.*\b(?:in|for|of)\b (.+)", "explain_score",
     lambda m: {"area": m.group(1).strip(" ?.")}),
    (r"\b(?:least|lowest|quietest|less)\b.*\bnois\w*\b.*\b(?:in|near|around)\b ([A-Za-z][\w' -]+?)[?.]?$",
     "rank_areas",
     lambda m: {"within": m.group(1).strip(), "level": "msoa", "limit": 5,
                "sort_by": "indicator:noise_exposure"}),
    (r"\b(?:best|top|rank)\b.*\b(?:in|near|around)\b ([A-Za-z][\w' -]+?)(?: under £?([\d,]+)k?)?[?.]?$",
     "rank_areas",
     lambda m: {"within": m.group(1).strip(), "level": "msoa", "limit": 5,
                **({"max_median_price": float(m.group(2).replace(",", "")) * (1000 if len(m.group(2)) <= 4 else 1)}
                   if m.group(2) else {})}),
    (r"\b(?:tell me about|profile|what is|how is)\b (.+)", "get_area_profile",
     lambda m: {"area": m.group(1).strip(" ?.")}),
]  # fmt: skip


def _scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Deterministic stand-in for an LLM: one tool call per matching request, then a summary."""
    last = messages[-1]
    retries = [p for p in last.parts if isinstance(p, RetryPromptPart)]
    if retries:
        return ModelResponse(parts=[TextPart(f"Sorry, I couldn't do that: {retries[0].content}")])
    returns = [p for p in last.parts if isinstance(p, ToolReturnPart)]
    if returns:
        names = ", ".join(p.tool_name for p in returns)
        return ModelResponse(parts=[TextPart(f"Here's what I found ({names}).")])
    prompt = next((p.content for p in last.parts if isinstance(p, UserPromptPart)), "")
    text = prompt if isinstance(prompt, str) else json.dumps(prompt)
    available = {t.name for t in info.function_tools}
    for pattern, tool, args in _RULES:
        m = re.search(pattern, text, flags=re.I)
        if m and tool in available:
            return ModelResponse(parts=[ToolCallPart(tool, args(m))])
    return ModelResponse(
        parts=[TextPart("Ask me to rank areas, profile a place, compare two areas, "
                        "find well-run pubs or change the weights.")]
    )  # fmt: skip


async def _scripted_stream(messages: list[ModelMessage], info: AgentInfo):
    """Streaming form of the scripted model (AG-UI always streams)."""
    response = _scripted(messages, info)
    for part in response.parts:
        if isinstance(part, TextPart):
            for word in part.content.split(" "):
                yield word + " "
        elif isinstance(part, ToolCallPart):
            yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args))}


def scripted_model() -> FunctionModel:
    return FunctionModel(_scripted, stream_function=_scripted_stream, model_name="scripted")
