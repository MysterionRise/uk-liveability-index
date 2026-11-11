"""Merge repeated eval runs into one Markdown summary: how often each case passed.

    python3 api/evals/repeats.py runs/run-01 runs/run-02 runs/run-03
    python3 api/evals/repeats.py docs/evals/*.json      # any reports of the same shape

Each argument is a report written by evals.run (``<out>/<model>.json``) or a directory
holding exactly one. Prints a table of the cases that failed at least once (passed k of
n, and the first failed check), then the totals: mean pass rate, what the runs cost and
the median turn time.

Exits 1 when a case failed in every run it was in: a consistent failure, which is what
the evals.yml workflow fails on. A case that passed some runs and failed others is listed
as flaky and leaves the exit code alone: the checks are assertions on what a model did,
so one run cannot tell a broken case from an unlucky one, which is why the workflow
repeats. Exits 2 when there is nothing to merge.

Standard library only, so it runs without the project's environment.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def load(paths: list[str]) -> list[dict]:
    """The report of each run, in the order given."""
    reports = []
    for arg in paths:
        path = Path(arg)
        if path.is_dir():
            found = sorted(path.glob("*.json"))
            if len(found) != 1:
                raise ValueError(f"{path}: expected one report, found {len(found)}")
            path = found[0]
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"{path}: not a report ({e})") from e
        if not isinstance(report, dict) or not isinstance(report.get("results"), list):
            raise ValueError(f"{path}: not a report (no results list)")
        reports.append(report)
    if not reports:
        raise ValueError("no reports to merge")
    return reports


def first_failure(result: dict) -> str:
    """Why a case failed: the turn's error if it crashed, else its first failed check."""
    if result.get("error"):
        return f"error: {result['error']}"
    checks = result.get("failed_checks") or {}
    for name, reason in checks.items():
        return f"{name}: {reason}"
    return "failed, with no check recorded"


def merge(reports: list[dict]) -> dict:
    """Per case, the runs it was in and how many it passed; and the totals."""
    cases: dict[str, dict] = {}
    turns: list[float] = []
    costs: list[float] = []
    for report in reports:
        for result in report["results"]:
            case = cases.setdefault(result["name"], {"runs": 0, "passed": 0, "why": None})
            case["runs"] += 1
            if result.get("passed"):
                case["passed"] += 1
            elif case["why"] is None:
                case["why"] = first_failure(result)
            turns += result.get("turn_seconds") or []
            if result.get("cost") is not None:
                costs.append(result["cost"])
    failing = {name: c for name, c in cases.items() if c["passed"] < c["runs"]}
    return {
        "runs": len(reports),
        "models": list(dict.fromkeys(r.get("model", "?") for r in reports)),
        "cases": len(cases),
        "passed": [sum(bool(r.get("passed")) for r in rep["results"]) for rep in reports],
        "sizes": [len(rep["results"]) for rep in reports],
        # Worst first: the cases that never passed, then by how seldom
        "failing": dict(sorted(failing.items(), key=lambda kv: (kv[1]["passed"], kv[0]))),
        "consistent": sorted(name for name, c in failing.items() if c["passed"] == 0),
        "flaky": sorted(name for name, c in failing.items() if c["passed"] > 0),
        "cost": sum(costs) if costs else None,
        "cost_estimated": any(r.get("cost_estimated") for r in reports),
        "turn_p50": statistics.median(turns) if turns else None,
    }


def _cell(text: str, limit: int = 200) -> str:
    """One line of a Markdown table cell: backslashes first, then the column separator."""
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names)


def markdown(merged: dict) -> str:
    runs = merged["runs"]
    lines = [
        f"## Assistant evals: {merged['cases']} cases, {runs} run{'s' if runs != 1 else ''}",
        "",
    ]
    models = merged["models"]
    if len(models) == 1:
        lines.append(f"Model: `{models[0]}`.")
    else:
        lines.append(
            f"Models: {_names(models)}. These are runs of different models, so a case that "
            "passed some of them says nothing about flakiness."
        )
    lines.append("")
    if merged["failing"]:
        lines += ["| Case | Passed | First failed check |", "|---|---|---|"]
        for name, case in merged["failing"].items():
            lines.append(
                f"| `{_cell(name)}` | {case['passed']} of {case['runs']} | {_cell(case['why'])} |"
            )
        lines.append("")
    else:
        lines += ["Every case passed every run.", ""]
    if merged["consistent"]:
        lines += [
            f"**Failed every run ({len(merged['consistent'])}): {_names(merged['consistent'])}.** "
            "A consistent failure: this is what fails the check.",
            "",
        ]
    elif merged["failing"]:
        lines += ["No case failed every run, so the check passes.", ""]
    if merged["flaky"]:
        count = len(merged["flaky"])
        lines += [
            f"{count} case{'s' if count != 1 else ''} passed some runs and failed others "
            "(flaky): listed above, not failed on.",
            "",
        ]

    by_run = list(zip(merged["passed"], merged["sizes"]))
    rates = [passed / size for passed, size in by_run if size]
    per_run = ", ".join(f"{passed}/{size}" for passed, size in by_run)
    rate = f"{100 * statistics.mean(rates):.1f}%" if rates else "no cases"
    lines.append(f"- Mean pass rate: {rate} (by run: {per_run})")
    if merged["cost"] is None:
        lines.append("- Total cost: not reported by the provider")
    else:
        estimated = " (estimated from list prices)" if merged["cost_estimated"] else ""
        lines.append(f"- Total cost: ${merged['cost']:.4f}{estimated}")
    if merged["turn_p50"] is not None:
        lines.append(f"- Turn time p50: {merged['turn_p50']:.2f} s")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("runs", nargs="+", help="A report (.json), or a directory holding one")
    args = parser.parse_args(argv)
    try:
        merged = merge(load(args.runs))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(markdown(merged), end="")
    return 1 if merged["consistent"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
