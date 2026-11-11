"""Merging repeated eval runs (api/evals/repeats.py): per-case k of n, offline."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.repeats import first_failure, load, main, markdown, merge  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def result(name: str, passed: bool = True, **more) -> dict:
    row = {
        "name": name,
        "category": "ranking",
        "passed": passed,
        "failed_checks": {},
        "error": None,
        "turn_seconds": [2.0],
        "cost": 0.001,
    }
    return {**row, **more}


def report(*results: dict, model: str = "openrouter:acme/swift-2", **more) -> dict:
    passed = sum(r["passed"] for r in results)
    return {"model": model, "cases": len(results), "passed": passed, **more, "results": [*results]}


WRONG_TOOL = {"AnyTool": "called ['explain_score'], wanted one of ['get_area_profile']"}
UNGROUNDED = {"Grounded": "unsupported numbers ['440']", "state:preset": "state.preset='x'"}


def three_runs() -> list[dict]:
    """`steady` always passes, `broken` never does, `flaky` passes one run in three."""
    return [
        report(
            result("steady"),
            result("broken", False, failed_checks=WRONG_TOOL, turn_seconds=[4.0, 6.0]),
            result("flaky", False, failed_checks=UNGROUNDED),
        ),
        report(
            result("steady"),
            result("broken", False, failed_checks={"Other": "a later reason"}),
            result("flaky"),
        ),
        report(
            result("steady"),
            result("broken", False, failed_checks=WRONG_TOOL),
            result("flaky", False, failed_checks=UNGROUNDED),
        ),
    ]


def test_merge_counts_each_case_over_the_runs():
    merged = merge(three_runs())
    assert (merged["runs"], merged["cases"]) == (3, 3)
    assert merged["passed"] == [1, 2, 1] and merged["sizes"] == [3, 3, 3]
    # Worst first, and a case that always passed is not listed
    assert list(merged["failing"]) == ["broken", "flaky"]
    assert merged["failing"]["broken"] == {
        "runs": 3,
        "passed": 0,
        "why": "AnyTool: called ['explain_score'], wanted one of ['get_area_profile']",
    }
    assert merged["failing"]["flaky"]["passed"] == 1
    assert merged["failing"]["flaky"]["why"] == "Grounded: unsupported numbers ['440']"
    assert merged["consistent"] == ["broken"]
    assert merged["flaky"] == ["flaky"]
    assert round(merged["cost"], 6) == 0.009
    assert merged["turn_p50"] == 2.0  # ten turns: eight of 2 s, one of 4 and one of 6


def test_markdown_has_a_row_per_failing_case_and_the_totals():
    text = markdown(merge(three_runs()))
    assert text.splitlines() == [
        "## Assistant evals: 3 cases, 3 runs",
        "",
        "Model: `openrouter:acme/swift-2`.",
        "",
        "| Case | Passed | First failed check |",
        "|---|---|---|",
        "| `broken` | 0 of 3 | AnyTool: called ['explain_score'], wanted one of "
        "['get_area_profile'] |",
        "| `flaky` | 1 of 3 | Grounded: unsupported numbers ['440'] |",
        "",
        "**Failed every run (1): `broken`.** A consistent failure: this is what fails the check.",
        "",
        "1 case passed some runs and failed others (flaky): listed above, not failed on.",
        "",
        "- Mean pass rate: 44.4% (by run: 1/3, 2/3, 1/3)",
        "- Total cost: $0.0090",
        "- Turn time p50: 2.00 s",
    ]


def write_runs(tmp_path: Path, runs: list[dict]) -> list[str]:
    """One directory per run, as the workflow's --out leaves them."""
    out = []
    for i, run in enumerate(runs, start=1):
        folder = tmp_path / f"run-{i:02d}"
        folder.mkdir()
        (folder / "openrouter-acme-swift-2.json").write_text(json.dumps(run))
        out.append(str(folder))
    return out


def test_a_consistent_failure_fails_and_says_so(tmp_path, capsys):
    assert main(write_runs(tmp_path, three_runs())) == 1
    out = capsys.readouterr().out
    assert "**Failed every run (1): `broken`.**" in out
    assert "| `flaky` | 1 of 3 |" in out


def test_flaky_cases_are_listed_and_do_not_fail(tmp_path, capsys):
    runs = [report(*[r for r in run["results"] if r["name"] != "broken"]) for run in three_runs()]
    assert main(write_runs(tmp_path, runs)) == 0
    out = capsys.readouterr().out
    assert "| `flaky` | 1 of 3 | Grounded: unsupported numbers ['440'] |" in out
    assert "No case failed every run, so the check passes." in out
    assert "Failed every run" not in out


def test_clean_runs_pass_and_say_so(tmp_path, capsys):
    runs = [report(result("steady"), result("other")) for _ in range(2)]
    assert main(write_runs(tmp_path, runs)) == 0
    out = capsys.readouterr().out
    assert "Every case passed every run." in out
    assert "- Mean pass rate: 100.0% (by run: 2/2, 2/2)" in out
    assert "| Case |" not in out


def test_one_run_makes_every_failure_a_consistent_one(tmp_path):
    assert main(write_runs(tmp_path, three_runs()[:1])) == 1


def test_a_crashed_turn_and_a_case_that_never_ran_are_failures_with_a_reason():
    crashed = result("crashed", False, error="UsageLimitExceeded: 12 tool calls")
    crashed["failed_checks"] = WRONG_TOOL  # the checks fail too, because of the error
    assert first_failure(crashed) == "error: UsageLimitExceeded: 12 tool calls"
    # A case the evaluation could not run at all has only a name and an error
    lost = {"name": "lost", "passed": False, "error": "ReadTimeout"}
    merged = merge([report(crashed, lost), report(result("crashed"), lost)])
    assert merged["failing"]["lost"] == {"runs": 2, "passed": 0, "why": "error: ReadTimeout"}
    assert merged["consistent"] == ["lost"] and merged["flaky"] == ["crashed"]
    assert first_failure({"name": "bare", "passed": False}) == "failed, with no check recorded"


def test_a_reason_cannot_break_the_table():
    reason = {"reply_none": "the reply said a|b\\c\nand went on " + "x" * 300}
    text = markdown(merge([report(result("odd", False, failed_checks=reason))]))
    (row,) = [line for line in text.splitlines() if line.startswith("| `odd`")]
    assert "a\\|b\\\\c and went on" in row
    assert row.endswith("… |") and len(row) < 260


def test_costs_missing_or_estimated_are_said(tmp_path):
    free = result("steady", cost=None)
    assert "- Total cost: not reported by the provider" in markdown(merge([report(free)]))
    estimated = report(result("steady"), cost_estimated=True)
    assert "- Total cost: $0.0010 (estimated from list prices)" in markdown(merge([estimated]))


def test_refused_thinking_blocks_are_added_up_and_said():
    quiet = markdown(merge([report(result("steady")), report(result("steady"))]))
    assert "refused" not in quiet
    runs = [report(result("steady"), thinking_refusals=2), report(result("steady"))]
    assert "- Requests refused over a replayed thinking block and re-sent: 2" in markdown(
        merge(runs)
    )


def test_unusable_input_is_an_error_not_a_pass(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert main([str(tmp_path / "empty")]) == 2
    assert "expected one report, found 0" in capsys.readouterr().err
    (tmp_path / "notes.json").write_text('{"model": "x"}')
    assert main([str(tmp_path / "notes.json")]) == 2
    assert main([str(tmp_path / "missing.json")]) == 2


def test_the_committed_reports_merge_as_three_runs_would(capsys):
    """The reports in docs/evals are of different models, but of the shape a repeat has."""
    paths = sorted(str(p) for p in (ROOT / "docs" / "evals").glob("*.json"))
    reports = load(paths)
    failed = [{r["name"] for r in rep["results"] if not r["passed"]} for rep in reports]
    every_run = set.intersection(*failed)

    assert main(paths) == (1 if every_run else 0)
    out = capsys.readouterr().out
    assert f"{len(paths)} runs" in out and "These are runs of different models" in out
    assert out.count("\n| `") == len(set.union(*failed))
    by_run = ", ".join(f"{rep['passed']}/{rep['cases']}" for rep in reports)
    assert f"(by run: {by_run})" in out
