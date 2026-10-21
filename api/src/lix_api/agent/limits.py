"""Caps on assistant use across questions, so a shared key can't run up a bill.

Each question is already capped (``runlog.usage_limits``). These add, checked before a
question reaches the model:

LIX_SESSION_TURNS     questions per conversation (default 30)
LIX_RATE_PER_MINUTE   questions per minute across everyone using this server (default 20)
LIX_DAILY_BUDGET      spend per UTC day, in the provider's currency (default 1.00; 0 turns
                      the cap off). It counts the cost the provider reports (OpenRouter
                      does) and survives restarts by re-reading today's turn log.

A refused question gets a chat reply saying why; the map, search and weights carry on.
"""

import json
import os
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from lix_api.agent.messages import message
from lix_core.log import setup_logging

logger = setup_logging("agent.limits")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class Limits:
    def __init__(
        self,
        session_turns: int = 30,
        rate_per_minute: int = 20,
        daily_budget: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = _today,
    ) -> None:
        self.session_turns = session_turns
        self.rate_per_minute = rate_per_minute
        self.daily_budget = daily_budget
        self.clock = clock
        self.today = today
        self.recent: deque[float] = deque()
        self.day = today()
        self.spent = 0.0

    # Models that cost nothing: the scripted answers of the tests and the recorded
    # conversations of the demo. They are never held back by a budget real models spent
    FREE_MODELS = ("test", "replay")

    @classmethod
    def from_env(cls) -> "Limits":
        free = os.environ.get("LIX_MODEL", "") in cls.FREE_MODELS
        return cls(
            session_turns=int(os.environ.get("LIX_SESSION_TURNS", 30)),
            rate_per_minute=int(os.environ.get("LIX_RATE_PER_MINUTE", 20)),
            daily_budget=0.0 if free else float(os.environ.get("LIX_DAILY_BUDGET") or 1.0),
        )

    def _roll(self) -> None:
        if self.today() != self.day:
            self.day, self.spent = self.today(), 0.0

    def budget_spent(self) -> bool:
        """Whether today's spend has reached the budget (never for a free model)."""
        self._roll()
        return self.daily_budget > 0 and self.spent >= self.daily_budget

    def admit(self, user_turns: int, locale: str = "en") -> str | None:
        """None if the question may go ahead (and counts it), else the reason it can't, in
        the interface language.

        ``user_turns`` is the number of user messages in the conversation so far,
        including this one: AG-UI clients send the whole conversation each time.
        """
        if self.budget_spent():
            return message("budget", locale)
        now = self.clock()
        while self.recent and now - self.recent[0] > 60:
            self.recent.popleft()
        if len(self.recent) >= self.rate_per_minute:
            return message("rate", locale)
        if user_turns > self.session_turns:
            return message("session_limit", locale, turns=str(self.session_turns))
        self.recent.append(now)
        return None

    def spend(self, cost: float | None) -> None:
        if cost:
            self._roll()
            self.spent += cost

    def load(self, path: Path) -> None:
        """Add up today's spend from the turn log (after a restart)."""
        self._roll()
        if not path.exists():
            return
        total = 0.0
        with open(path) as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(record.get("at", "")).startswith(self.day) and record.get("cost"):
                    total += record["cost"]
        self.spent = total
        if total:
            logger.info(f"Spent {total:.4f} on the assistant so far today")

    def status(self) -> dict:
        self._roll()
        return {
            "day": self.day,
            "spent": round(self.spent, 4),
            "daily_budget": self.daily_budget or None,
            "session_turns": self.session_turns,
            "rate_per_minute": self.rate_per_minute,
        }


def user_turns(body: dict) -> int:
    """User messages in an AG-UI run input."""
    return sum(1 for m in body.get("messages") or [] if m.get("role") == "user")


def reply_model(text: str):
    """A model that only says ``text``: how a refused question is answered."""
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    async def stream(messages: list[ModelMessage], info: AgentInfo):
        yield text

    return FunctionModel(reply, stream_function=stream, model_name="limits")


# The server's one set of limits: the chat endpoint, the profile generator and the
# assistant's tools all admit through and spend against it
limits = Limits.from_env()
