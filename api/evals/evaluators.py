"""Checks on a traced case (see cases.yaml for the expectation format)."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from evals.harness import Trace
from lix_api.agent.agent import INSTRUCTIONS


def _ok(passed: bool, reason: str = "") -> EvaluationReason:
    return EvaluationReason(value=passed, reason=reason or None)


@dataclass
class NoError(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        trace: Trace = ctx.output
        return _ok(trace.error is None, trace.error or "")


@dataclass
class ToolsInOrder(Evaluator):
    """The expected tools appear in this order among the calls (others may interleave)."""

    tools: list[str] = field(default_factory=list)

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        called = [c.name for c in ctx.output.calls]
        it = iter(called)
        ok = all(any(name == t for name in it) for t in self.tools)
        return _ok(ok, f"called {called}, expected {self.tools} in order")


@dataclass
class AnyTool(Evaluator):
    tools: list[str] = field(default_factory=list)

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        called = [c.name for c in ctx.output.calls]
        return _ok(
            bool(set(called) & set(self.tools)), f"called {called}, wanted one of {self.tools}"
        )


@dataclass
class ForbiddenTools(Evaluator):
    tools: list[str] = field(default_factory=list)

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        bad = [c.name for c in ctx.output.calls if c.name in self.tools]
        return _ok(not bad, f"called forbidden {bad}" if bad else "")


@dataclass
class NoTools(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        called = [c.name for c in ctx.output.calls]
        return _ok(not called, f"called {called}" if called else "")


def _check(actual: Any, op: str, value: Any) -> bool:
    if op == "truthy":
        return bool(actual)
    if actual is None:
        return False
    if op == "eq":
        return str(actual).lower() == str(value).lower()
    if op == "contains":
        return str(value).lower() in str(actual).lower()
    if op == "approx":
        try:
            return abs(float(actual) - float(value)) <= 0.1 * abs(float(value))
        except (TypeError, ValueError):
            return False
    if op == "len":
        return hasattr(actual, "__len__") and len(actual) == int(value)
    if op == "gte":
        return float(actual) >= float(value)
    if op == "lte":
        return float(actual) <= float(value)
    raise ValueError(f"Unknown op {op!r}")


@dataclass
class ArgCheck(Evaluator):
    """A check on an argument of ``tool``; passes if any call of it satisfies it."""

    tool: str = ""
    arg: str = ""
    op: str = "eq"
    value: Any = None

    def get_default_evaluation_name(self) -> str:
        return f"arg:{self.tool}.{self.arg}"

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        calls = [c for c in ctx.output.calls if c.name == self.tool]
        if not calls:
            return _ok(False, f"{self.tool} not called")
        # Models sometimes explore first (a narrower query, then the real one): any call counts
        seen = [c.args.get(self.arg) for c in calls]
        ok = any(_check(a, self.op, self.value) for a in seen)
        return _ok(ok, f"{self.tool}.{self.arg} in {seen!r}; wanted {self.op} {self.value!r}")


@dataclass
class StateCheck(Evaluator):
    path: str = ""
    op: str = "eq"
    value: Any = None

    def get_default_evaluation_name(self) -> str:
        return f"state:{self.path}"

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        actual: Any = ctx.output.state
        for key in self.path.split("."):
            actual = actual.get(key) if isinstance(actual, dict) else None
        return _ok(
            _check(actual, self.op, self.value),
            f"state.{self.path}={actual!r} {self.op} {self.value!r}",
        )


@dataclass
class ReplyCheck(Evaluator):
    any_of: list[str] = field(default_factory=list)
    none_of: list[str] = field(default_factory=list)

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        reply = ctx.output.reply.lower()
        if self.any_of and not any(s.lower() in reply for s in self.any_of):
            return _ok(False, f"reply has none of {self.any_of}")
        # Quoting something (e.g. an injected name, to warn about it) isn't saying it
        unquoted = QUOTED.sub("", reply)
        hit = [s for s in self.none_of if s.lower() in unquoted]
        return _ok(not hit, f"reply contains {hit}" if hit else "")


# Common words that mark a language; the share of a reply's words among them says which
# language it is in (a cheap check with no dependency, enough to catch an English reply)
STOPWORDS = {
    "en": {"the", "and", "is", "of", "to", "in", "a", "for", "with", "that", "it", "are", "on"},
    "cy": {
        "yn", "a'r", "mae", "ar", "yr", "o", "i", "y", "ac", "gyda", "sy'n", "ei", "hefyd", "ond",
        "neu", "gan", "am", "wedi", "fel", "hwn",
    },
    "gd": {
        "agus", "tha", "anns", "an", "a'", "air", "na", "le", "gu", "ach", "seo", "chan", "eil",
        "bha", "mar",
    },
    "ga": {
        "agus", "tá", "na", "an", "ar", "le", "go", "ach", "seo", "níl", "bhí", "atá", "sa", "mar",
        "den",
    },
}  # fmt: skip


@dataclass
class LanguageCheck(Evaluator):
    """The reply is in ``lang`` (its stopwords outnumber every other language's)."""

    lang: str

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        words = re.findall(r"[\w']+", ctx.output.reply.lower())
        counts = {lang: sum(w in stops for w in words) for lang, stops in STOPWORDS.items()}
        mine = counts.get(self.lang, 0)
        rivals = max((n for lang, n in counts.items() if lang != self.lang), default=0)
        ok = mine >= 3 and mine > rivals
        return _ok(
            ok, "" if ok else f"reply looks {max(counts, key=counts.get)} not {self.lang}: {counts}"
        )


QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
# Upper bounds ("within about 500 m", "under £400k") summarise results; they don't quote them
BOUND = re.compile(r"(within|under|below|less than|up to)( about| roughly| around)? £?$", re.I)
NUMBER = re.compile(r"(?<![\w.])£?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|%|km|m\b)?", re.I)


def numbers_in(text: str) -> list[tuple[float, str]]:
    out = []
    for m in NUMBER.finditer(text):
        value = float(m.group(1).replace(",", ""))
        out.append((value, (m.group(2) or "").lower()))
    return out


def _matches(c: float, k: float) -> bool:
    return abs(c - k) <= max(0.51, 0.01 * abs(k)) or abs(c - round(k)) < 0.5


def grounded_numbers(reply: str, sources: list[str]) -> list[str]:
    """Numbers in the reply that no tool result (or the prompt) supports.

    Small counts (≤ 10) and years are ignored: "two kids", "top 5", "in 2025". A number
    is supported if a source number matches it after rounding (72 for 72.4), with a
    1% tolerance, or ×1,000 for "k" (£350k) and metres for "km". Denominators ("per
    1,000") are skipped. A difference between two source numbers ("£108k higher") or,
    for percentages, their relative difference ("20% cheaper") also counts.
    """
    known = [v for s in sources for v, _ in numbers_in(s)]
    # Differences are only checked among the larger numbers, to keep the pairs few
    big = sorted({round(v, 2) for v in known if abs(v) > 10})[:800]
    unsupported = []
    for m in NUMBER.finditer(reply):
        value, unit = float(m.group(1).replace(",", "")), (m.group(2) or "").lower()
        # "per 1,000 residents" is a unit, not a claim
        if reply[max(0, m.start() - 4) : m.start()].lower() == "per ":
            continue
        if BOUND.search(reply[max(0, m.start() - 24) : m.start()]):
            continue
        if value <= 10 or (1990 <= value <= 2035 and value == int(value) and not unit):
            continue
        candidates = [value]
        if unit in ("k", "km"):
            candidates.append(value * 1000)
        if any(_matches(c, k) for c in candidates for k in known):
            continue
        tol = lambda c: max(1.0, 0.015 * c)  # noqa: E731
        derived = any(
            abs(c - (b - a)) <= tol(c)
            or (unit == "%" and b and abs(c - (b - a) / b * 100) <= 1.0)
            or (unit == "%" and a and abs(c - (b - a) / a * 100) <= 1.0)
            for c in candidates
            for i, a in enumerate(big)
            for b in big[i + 1 :]
        )
        if not derived:
            unsupported.append(f"{value:g}{unit}")
    return unsupported


@dataclass
class Grounded(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        trace: Trace = ctx.output
        # Tool results, the user's words, the model's own tool arguments (a 0.5 km
        # radius) and the standing facts in its instructions (~1,600 residents an LSOA)
        sources = trace.returns + [trace.prompt, INSTRUCTIONS]
        sources += [json.dumps(c.args) for c in trace.calls]
        bad = grounded_numbers(trace.reply, sources)
        return _ok(not bad, f"unsupported numbers {bad}" if bad else "")


def scored_areas(returns: list[str]) -> dict[tuple[str, str], list[tuple[str, float]]]:
    """Every area the run's tool results scored, by (level, code), with each overall it
    was given and the kind of result that gave it: "profile" (a profile or explanation's
    small area, and the larger area it names in ``resolved_from``), "compare", "rank"."""
    out: dict[tuple[str, str], list[tuple[str, float]]] = {}

    def add(source: str, level, code, overall) -> None:
        if level and code and overall is not None:
            out.setdefault((str(level), str(code)), []).append((source, float(overall)))

    for raw in returns:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if "lsoa21cd" in data:
            add("profile", "lsoa", data["lsoa21cd"], data.get("overall"))
            named = data.get("resolved_from") or {}
            add("profile", named.get("kind"), named.get("code"), named.get("overall"))
        for source, key in (("compare", "areas"), ("rank", "results")):
            for area in data.get(key) or []:
                if isinstance(area, dict):
                    add(source, area.get("level"), area.get("code"), area.get("overall"))
    return out


@dataclass
class ConsistentScores(Evaluator):
    """One area, one score: every overall the run's tools gave the same area agrees
    (#228: a town profiled as one of its small areas and compared as the whole showed two
    numbers for one name). For cases that keep one weighting. It fails too unless some
    area was scored by two kinds of result (a profile and a comparison, say): the same
    profile fetched twice agrees with itself and shows nothing."""

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        areas = scored_areas(ctx.output.returns)
        crossed = {k: v for k, v in areas.items() if len({source for source, _ in v}) > 1}
        if not crossed:
            return _ok(False, "no area was scored by two kinds of tool result")
        # Every tool gives a small area one decimal and an average of small areas two, so
        # the figures are equal, not close: a tolerance hid a ranking that rounded apart
        apart = {
            k: [score for _, score in v]
            for k, v in areas.items()
            if max(s for _, s in v) - min(s for _, s in v) > 1e-9
        }
        return _ok(not apart, f"one area, two scores: {apart}" if apart else "")


def _plain(text: str) -> str:
    """Lower case, "&" as "and", one space: a name as a reply may write it."""
    return re.sub(r"\s+", " ", text.lower().replace("&", " and ")).strip()


@dataclass
class CheapestNamed(Evaluator):
    """Asked which of the areas just ranked is the most affordable, the reply names the
    one with the lowest typical price in that list, and names it first (#236: a reply once
    named two dearer ones; "X is cheapest, Longwell Green is dearer" names it too)."""

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        for raw in ctx.output.returns:
            try:
                rows = json.loads(raw).get("results") or []
            except (TypeError, ValueError, AttributeError):
                continue
            priced = [r for r in rows if isinstance(r, dict) and r.get("median_price")]
            if len(priced) >= 2:
                break
        else:
            return _ok(False, "no ranking with prices to choose from")
        cheapest = min(priced, key=lambda r: r["median_price"])
        reply = _plain(ctx.output.reply)

        def first_mention(row: dict) -> int | None:
            at = [reply.find(_plain(str(row[k]))) for k in ("name", "name_en") if row.get(k)]
            at = [i for i in at if i >= 0]
            return min(at) if at else None

        mentions = {r.get("name"): first_mention(r) for r in priced}
        where = mentions[cheapest.get("name")]
        if where is None:
            return _ok(False, f"the cheapest listed is {cheapest.get('name')}, not named")
        earlier = [n for n, i in mentions.items() if i is not None and i < where]
        return _ok(not earlier, f"{earlier} named before the cheapest, {cheapest.get('name')}")


@dataclass
class ModelEngine(Evaluator):
    """A profile case on a real model must come from the model, not the rules fallback
    (on the scripted or recorded model the rules are the engine, and that passes)."""

    def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
        from lix_api.agent.limits import limits
        from lix_api.agent.models import model_name

        engine = ctx.output.state.get("engine")
        reason = ctx.output.state.get("engine_reason")
        if model_name() in limits.FREE_MODELS:
            return _ok(engine == "rules", f"engine={engine} on a free model")
        return _ok(engine == "model", f"engine={engine} ({reason})")


def evaluators_for(expect: dict, kind: str = "chat") -> list[Evaluator]:
    """The evaluators a case's ``expect`` block asks for (NoError always; a profile case
    also checks which engine built the profile)."""
    out: list[Evaluator] = [NoError()]
    if kind == "profile":
        out.append(ModelEngine())
    if expect.get("tools"):
        out.append(ToolsInOrder(tools=expect["tools"]))
    if expect.get("tools_any"):
        out.append(AnyTool(tools=expect["tools_any"]))
    if expect.get("forbid_tools"):
        out.append(ForbiddenTools(tools=expect["forbid_tools"]))
    if expect.get("no_tools"):
        out.append(NoTools())
    out += [ArgCheck(**a) for a in expect.get("args", [])]
    out += [StateCheck(**s) for s in expect.get("state", [])]
    if expect.get("reply_any") or expect.get("reply_none"):
        out.append(
            ReplyCheck(any_of=expect.get("reply_any", []), none_of=expect.get("reply_none", []))
        )
    if expect.get("grounded"):
        out.append(Grounded())
    if expect.get("reply_lang"):
        out.append(LanguageCheck(lang=expect["reply_lang"]))
    if expect.get("consistent"):
        out.append(ConsistentScores())
    if expect.get("cheapest_named"):
        out.append(CheapestNamed())
    return out
