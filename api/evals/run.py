"""Run the assistant eval: cases.yaml against one model, on the full build.

    cd api && uv run python -m evals.run --model openrouter:anthropic/claude-opus-5.5
    cd api && uv run python -m evals.run --model test --cases compare_two,well_run_pubs

Writes docs/evals/<model>.json (every case: checks, tools called, reply, timing, cost)
and prints a summary. Real models cost money: run a few cases first (--cases) and check
the cost line before running everything.
"""

import argparse
import asyncio
import json
import os
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name("cases.yaml")


def load_cases(names: list[str] | None = None, category: str | None = None) -> list[dict]:
    cases = yaml.safe_load(CASES.read_text())["cases"]
    if names:
        unknown = set(names) - {c["name"] for c in cases}
        if unknown:
            raise SystemExit(f"Unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c["name"] in names]
    if category:
        cases = [c for c in cases if c["category"] == category]
    return cases


def build_dataset(cases: list[dict]):
    from pydantic_evals import Case, Dataset

    from evals.evaluators import evaluators_for
    from evals.harness import CaseInput

    return Dataset(
        name="liveability-assistant",
        cases=[
            Case(
                name=c["name"],
                inputs=CaseInput(
                    turns=c["turns"],
                    state=c.get("state", {}),
                    patch=c.get("patch"),
                    kind=c.get("kind", "chat"),
                ),
                metadata={"category": c["category"]},
                evaluators=tuple(evaluators_for(c.get("expect", {}), c.get("kind", "chat"))),
            )
            for c in cases
        ],
    )


def openrouter_price(model: str) -> tuple[float, float] | None:
    """($ per input token, $ per output token) from OpenRouter's public model list."""
    if not model.startswith("openrouter:"):
        return None
    import requests

    slug = model.split(":", 1)[1]
    try:
        models = requests.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]
    except Exception:
        return None
    m = next((m for m in models if m["id"] == slug), None)
    if m is None:
        return None
    return float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])


def summarise(report, model: str) -> dict:
    rows = []
    for case in report.cases:
        trace = case.output
        checks = {name: (r.value, r.reason) for name, r in case.assertions.items()}
        rows.append(
            {
                "name": case.name,
                "category": case.metadata["category"],
                "passed": all(v for v, _ in checks.values()),
                "failed_checks": {n: reason for n, (v, reason) in checks.items() if not v},
                "tools": [c.name for c in trace.calls],
                "reply": trace.reply,
                "error": trace.error,
                "turn_seconds": [round(s, 2) for s in trace.turn_seconds],
                "input_tokens": trace.input_tokens,
                "output_tokens": trace.output_tokens,
                "cost": trace.cost,
            }
        )
    for failure in getattr(report, "failures", []):
        rows.append({"name": failure.name, "passed": False, "error": failure.error_message})
    turns = [s for r in rows for s in r.get("turn_seconds", [])]
    estimated = False
    if not any(r.get("cost") is not None for r in rows) and (price := openrouter_price(model)):
        # The provider didn't report cost: estimate it from list prices (no cache discount)
        for r in rows:
            if "input_tokens" in r:
                r["cost"] = r["input_tokens"] * price[0] + r["output_tokens"] * price[1]
        estimated = True
    costs = [r["cost"] for r in rows if r.get("cost") is not None]
    words = [len(r["reply"].split()) for r in rows if r.get("reply")]
    by_category: dict[str, list[bool]] = {}
    for r in rows:
        by_category.setdefault(r.get("category", "?"), []).append(r["passed"])
    return {
        "model": model,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_category.items())},
        "turn_seconds_p50": round(statistics.median(turns), 2) if turns else None,
        "turn_seconds_p95": round(sorted(turns)[int(0.95 * (len(turns) - 1))], 2)
        if turns
        else None,
        "cost_total": round(sum(costs), 4) if costs else None,
        "cost_per_case": round(sum(costs) / len(costs), 4) if costs else None,
        "cost_estimated": estimated,
        "reply_words_median": statistics.median(words) if words else None,
        "results": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--model", required=True, help='e.g. openrouter:anthropic/claude-opus-5.5, or "test"'
    )
    parser.add_argument("--cases", help="Comma-separated case names (default: all)")
    parser.add_argument("--category", help="Only cases in this category")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default=str(ROOT / "docs" / "evals"))
    args = parser.parse_args(argv)

    # The agent reads LIX_MODEL when imported (settings, fallbacks): set it first
    os.environ["LIX_MODEL"] = args.model
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    from evals.harness import run_case
    from lix_api.agent.models import build_model

    model = build_model()
    cases = load_cases(args.cases.split(",") if args.cases else None, args.category)
    dataset = build_dataset(cases)

    async def task(inputs):
        return await run_case(inputs, model=model)

    report = (
        dataset.evaluate_sync(task, max_concurrency=args.concurrency, progress=False)
        if hasattr(dataset, "evaluate_sync")
        else asyncio.run(dataset.evaluate(task, max_concurrency=args.concurrency, progress=False))
    )
    summary = summarise(report, args.model)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9.]+", "-", args.model.lower()).strip("-")
    path = out / f"{slug}.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print(
        f"\n{args.model}: {summary['passed']}/{summary['cases']} passed  {summary['by_category']}"
    )
    print(f"turn latency p50 {summary['turn_seconds_p50']}s, p95 {summary['turn_seconds_p95']}s")
    if summary["cost_total"] is not None:
        est = " (estimated from list prices)" if summary["cost_estimated"] else ""
        print(f"cost ${summary['cost_total']} total, ${summary['cost_per_case']} per case{est}")
    print(f"reply length: median {summary['reply_words_median']} words")
    for r in summary["results"]:
        if not r["passed"]:
            why = r.get("error") or r.get("failed_checks")
            print(f"  FAIL {r['name']}: {why}  tools={r.get('tools')}")
    print(f"Wrote {path}")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
