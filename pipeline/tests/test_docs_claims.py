"""The claims check of `lix docs --check`: the numbers the docs state against the repository."""

import argparse
import json
from datetime import date

import pytest
import yaml

from lix_core.config import get_config
from lix_core.paths import get_project_root
from lix_pipeline.claims import (
    ModelRun,
    check_claims,
    claims_in,
    config_facts,
    eval_facts,
    facts,
    scan,
    scanned_files,
)

HAIKU = ModelRun(
    model="openrouter:anthropic/claude-haiku-5.5",
    report="haiku.json",
    passed=56,
    cases=58,
    run=date(2026, 11, 4),
    p50=3.56,
    p95=7.33,
    cost=0.0007,
    words=69.0,
)
OPUS = ModelRun(
    model="openrouter:anthropic/claude-opus-5.5",
    report="opus.json",
    passed=56,
    cases=58,
    run=date(2026, 11, 4),
    p50=5.58,
    p95=11.48,
    cost=0.0274,
    words=82.0,
)
QWEN = ModelRun(
    model="openrouter:qwen/qwen3.8-27b",
    report="qwen.json",
    passed=52,
    cases=58,
    run=date(2026, 11, 4),
    p50=5.56,
    p95=13.38,
    cost=0.0021,
    words=67.5,
)
KNOWN = {
    "areas": 62833,
    "areas.S": 7392,
    "areas.N": 850,
    "areas.I": 18919,
    "indicators": 112,
    "indicators.scored": 44,
    "indicators.context": 66,
    "indicators.diagnostic": 2,
    "sources.registered": 236,
    "sources.in_use": 224,
    "themes": 9,
    "themes.N": 8,
    "themes.I": 7,
    "presets": 5,
    "languages": 4,
    "eval.cases": 58,
    "eval.grounded": 31,
    "eval.grounded.elsewhere": 29,
    "eval.category.ranking": 14,
    "eval.category.lookup": 15,
    "eval.category.grounding": 2,
    "eval.runs": [HAIKU, OPUS, QWEN],
    "eval.models": 3,
    "eval.passed": [52, 56, 56],
    "eval.run_dates": [date(2026, 11, 4)],
    "validation.places": 58,
    "validation.checks": 168,
    "validation.anchors": 109,
}


def problems(text: str, known: dict = KNOWN) -> list[str]:
    return [str(m) for m in scan(text, known, "README.md")]


# --- facts ------------------------------------------------------------------------------


def test_config_facts_are_what_the_config_files_hold():
    known = config_facts()
    root = get_project_root()

    indicators = get_config("indicators")["indicators"]
    roles = [i.get("role", "scored") for i in indicators]
    assert known["indicators"] == len(indicators)
    assert known["indicators.scored"] == roles.count("scored")
    assert known["indicators.context"] == roles.count("context")
    assert known["indicators.diagnostic"] == roles.count("diagnostic")

    nations = get_config("nations")["nations"]
    assert known["areas"] == sum(n["levels"]["low"]["count"] for n in nations.values())
    assert known["areas.I"] == nations["I"]["levels"]["low"]["count"]
    assert known["themes"] == len(get_config("indicators")["themes"])
    assert known["themes.I"] == known["themes"] - len(nations["I"]["theme_gaps"])

    registered = [slug for slug in get_config("datasets") if not slug.startswith("x-")]
    assert known["sources.registered"] == len(registered)
    # Every source in use is registered, and some registered ones are not yet read
    assert 0 < known["sources.in_use"] <= known["sources.registered"]
    assert known["presets"] == len(get_config("weights")["presets"])
    assert known["languages"] == 1 + len(list((root / "config" / "i18n").glob("*.yaml")))

    places = get_config("validation_places")["places"]
    assert known["validation.places"] == len(places)
    assert known["validation.checks"] == sum(len(p.get("checks", [])) for p in places)
    assert 0 < known["validation.anchors"] <= known["validation.checks"]


def test_sources_in_use_are_the_ones_a_manifest_credits():
    """The docs' "datasets in use" and the pack's source list are one definition."""
    from lix_pipeline.serve.scores import manifest

    credited = manifest({})["sources"]
    assert config_facts()["sources.in_use"] == len(credited)
    assert "nspl" in credited  # the backbone's, which no indicator names


def test_eval_facts_come_from_the_cases_and_the_reports(tmp_path):
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {"name": "a", "category": "ranking", "expect": {"grounded": True}},
                    {"name": "b", "category": "ranking", "expect": {"tools": ["rank_areas"]}},
                    {"name": "c", "category": "grounding", "expect": {"grounded": True}},
                    {"name": "d", "category": "profile"},
                ]
            }
        )
    )
    reports = tmp_path / "evals"
    reports.mkdir()
    (reports / "openrouter-acme-swift-2.json").write_text(
        json.dumps(
            {
                "model": "openrouter:acme/swift-2",
                "run_at": "2026-11-04T23:59:08+00:00",
                "cases": 4,
                "passed": 3,
                "turn_seconds_p50": 2.5,
                "turn_seconds_p95": 6.0,
                "cost_per_case": 0.0012,
                "reply_words_median": 70.0,
                "results": [],
            }
        )
    )
    known = eval_facts(cases, reports)
    assert known["eval.cases"] == 4
    assert known["eval.grounded"] == 2
    assert known["eval.grounded.elsewhere"] == 1
    assert known["eval.category.ranking"] == 2
    assert known["eval.models"] == 1
    assert known["eval.passed"] == [3]
    assert known["eval.run_dates"] == [date(2026, 11, 4)]
    (run,) = known["eval.runs"]
    assert (run.model, run.passed, run.cases, run.p50, run.cost) == (
        "openrouter:acme/swift-2",
        3,
        4,
        2.5,
        0.0012,
    )


# --- counts -----------------------------------------------------------------------------


def test_a_wrong_count_is_reported_with_its_file_and_line():
    text = "# Index\n\n- **61,000 neighbourhoods** across the UK and\n  Ireland, on nine themes\n"
    assert problems(text) == [
        'README.md:3: says "61,000 neighbourhoods", the repo says 62,833 (config/nations.yaml)'
    ]


def test_a_claim_that_wraps_is_reported_on_the_line_of_its_number():
    text = "from **about 224 open datasets** (230 in the\n  registry) and 95\nindicators\n"
    assert problems(text) == [
        'README.md:1: says "230 in the registry", the repo says 236 (config/datasets.yaml)',
        'README.md:2: says "95 indicators", the repo says 112 (config/indicators.yaml)',
    ]


def test_right_counts_pass_with_or_without_separators_and_as_words():
    text = (
        "62,833 neighbourhoods (62833 neighbourhoods), up to 112 indicators (44 scored across "
        "9 themes), nine themes, five ready-made presets, an interface in four languages, "
        "tested on 58 cases, 31 of them also checking grounding; a review of 58 contrasting "
        "places (168 checks, 109 of them run on every build)."
    )
    assert len(claims_in(text, KNOWN)) == 13
    assert problems(text) == []


@pytest.mark.parametrize(
    ("text", "passes"),
    [
        ("about 224 open datasets", True),
        ("about 228 open datasets", True),  # within 2% of 224
        ("around 220 open datasets", True),
        ("about 230 open datasets", False),
        ("about 215 open datasets", False),
        ("228 open datasets", False),  # unhedged: exact or wrong
        ("223 open datasets", False),
        ("over 220 open datasets", True),
        ("more than 222 open datasets", True),
        ("over 226 open datasets", False),  # within 2%, but not over
        ("over 200 open datasets", False),  # true, and no longer near the fact
        ("nearly 63,000 neighbourhoods", True),
        ("about 60,000 neighbourhoods", False),
    ],
)
def test_a_hedged_count_passes_within_the_tolerance_and_an_unhedged_one_must_be_exact(text, passes):
    assert (problems(text) == []) is passes


def test_a_count_of_datasets_or_cases_may_be_either_of_its_two_facts():
    assert problems("224 datasets, 236 datasets; 58 cases, of which 31 cases check grounding") == []
    assert problems("230 datasets") == [
        'README.md:1: says "230 datasets", the repo says 224 or 236 '
        "(the sources of config/indicators.yaml and the backbone)"
    ]
    # ... but where the phrase names one of them, it must be that one
    assert len(problems("grounding is checked on 58 cases")) == 1


def test_a_nations_own_counts_are_checked_where_the_phrase_names_them():
    right = (
        "Scotland: the unit is the 2022 Data Zone (7,392). The 850 Super Data Zones are scored; "
        "the unit is the Census 2022 Small Area (18,919). Ireland opts in to its own: its\n"
        "areas score on seven themes, and Northern Ireland scores on eight themes."
    )
    assert len(claims_in(right, KNOWN)) == 5
    assert problems(right) == []
    assert len(problems(right.replace("7,392", "6,976").replace("seven", "eight"))) == 2
    assert problems("Ireland's seven themes and Northern Ireland's eight themes") == []
    assert problems("Northern Ireland's seven themes") == [
        'README.md:1: says "Northern Ireland\'s seven themes", the repo says 8 '
        "(config/indicators.yaml less the nation's theme_gaps in config/nations.yaml)"
    ]
    # Years, other censuses' zones and part of a nation are not the nation's count
    other = (
        "still on the 2011 Data Zones; 2022 Small Areas in Ireland; the 6,976 Data Zones of "
        "2011; the 1,200 Small Areas in Dublin; a few hundred residents in Ireland and "
        "Scotland, scored 0–100 on nine themes"
    )
    assert [c.said for c in claims_in(other, KNOWN)] == ["nine themes"]


def test_a_number_that_counts_some_of_them_is_left_alone():
    text = (
        "Five Dublin areas were scored on two themes; two indicators stand in for sources "
        "Scotland lacks; every pub rests on two sources; add two eval cases; the other three "
        "languages; 14 indicators in the safety theme; 16 places in Scotland; rounded to 2 "
        "decimal places; 622 of the 628 areas in the top 1%."
    )
    assert claims_in(text, KNOWN) == []


def test_the_lists_of_pass_counts_and_run_dates_are_checked():
    right = (
        "run on three models (56, 56 and 52 passed on 4 November). One full run each on "
        "4 November 2026; the misses on 4 November 2026:"
    )
    assert len(claims_in(right, KNOWN)) == 5
    assert problems(right) == []
    assert problems("run on two models (56, 55 and 52 passed on 3 November)") == [
        'README.md:1: says "run on two models", the repo says 3 (docs/evals/*.json)',
        'README.md:1: says "(56, 55 and 52 passed", the repo says 56, 56, 52 (docs/evals/*.json)',
        'README.md:1: says "passed on 3 November", the repo says 4 November 2026 '
        "(docs/evals/*.json)",
    ]


# --- models -----------------------------------------------------------------------------

RESULTS = """\
| Model | Passed | Turn p50 | Turn p95 | Cost per question | Reply words (median) | Run |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | 56/58 | 5.58 s | 11.48 s | $0.0274 | 82 | 2026-11-04 |
| Claude Haiku 5.5 (the default) | 56/58 | 3.56 s | 7.33 s | $0.0007 | 69 | 2026-11-04 |
| Qwen 3.8 27B (open weights) | 52/58 | 5.56 s | 13.38 s | $0.0021 | 67.5 | 2026-11-04 |
| A model with no report | 1/58 | 99 s | 99 s | $9 | 1 | 2020-01-01 |
"""
SUMMARY = """\
| Model (via OpenRouter) | Eval cases passed | Typical answer | Cost per question |
|---|---|---|---|
| Claude Haiku 5.5 (default) | 56/58 | 4 s | about $0.001 |
| Qwen 3.8 27B (fallback, open weights) | 52/58 | 6 s | about $0.002 |
| Claude Opus 5.5 | 56/58 | 6 s | about $0.03 |
"""


def test_a_results_table_is_read_against_each_models_report():
    assert len(claims_in(RESULTS, KNOWN)) == 18
    assert problems(RESULTS) == []
    # The README rounds: 3.56 s is "4 s", $0.0007 is "about $0.001"
    assert len(claims_in(SUMMARY, KNOWN)) == 9
    assert problems(SUMMARY) == []


def test_a_stale_cell_of_a_results_table_is_reported():
    stale = RESULTS.replace("| 56/58 | 3.56 s", "| 57/58 | 4.10 s").replace(
        "67.5 | 2026-11-04", "67.5 | 2026-10-21"
    )
    assert problems(stale) == [
        'README.md:4: says "57/58", the repo says 56/58 (docs/evals/haiku.json)',
        'README.md:4: says "4.10 s", the repo says 3.56 s (docs/evals/haiku.json)',
        'README.md:5: says "2026-10-21", the repo says 2026-11-04 (docs/evals/qwen.json)',
    ]
    assert problems(SUMMARY.replace("| 6 s | about $0.03 |", "| 7 s | about $0.04 |")) == [
        'README.md:5: says "7 s", the repo says 5.58 s (docs/evals/opus.json)',
        'README.md:5: says "about $0.04", the repo says $0.0274 (docs/evals/opus.json)',
    ]


def test_what_is_said_of_a_model_after_its_name_is_checked():
    right = (
        "Claude Haiku 5.5 answers in about 4 seconds for about $0.001 a\n# question,\n"
        "and Opus 5.5 in about 6 ([evals](evals.md)). Claude Opus 5.5 answers better and "
        "costs about $0.03 a question (Haiku's two misses passed three reruns each)."
    )
    assert len(claims_in(right, KNOWN)) == 5
    assert problems(right) == []
    stale = right.replace("in about 6", "in about 7").replace("two misses", "three misses")
    assert problems(stale) == [
        'README.md:3: says "in about 7", the repo says 5.58 s (docs/evals/opus.json)',
        'README.md:3: says "\'s three misses", the repo says 2 (docs/evals/haiku.json)',
    ]


def test_other_numbers_near_a_models_name_are_not_read():
    text = (
        "Claude Haiku 5.5 answers faster than Opus 5.5 at about 1/40 of the cost. Opus's "
        "replies had a median of 112 words over 6 sentences. Haiku 4.5 answered in about 9 "
        "seconds in October, and GPT-5 in about 12 seconds. The $1 budget buys about thirty "
        "Opus questions. Qwen read it as a hint in about 20% of runs."
    )
    assert claims_in(text, KNOWN) == []


CATEGORIES = """\
| Category | Cases | What is checked |
|---|---|---|
| Ranking | 14 | Weights set before ranking |
| Look-ups | 15 | The right tool and arguments |
| Grounding | 2 (+29) | Every number in a reply appears in a tool result (checked on 31 cases) |
| A new category | 5 | Not in cases.yaml yet |
"""


def test_the_eval_categories_table_is_read_against_the_cases():
    assert len(claims_in(CATEGORIES, KNOWN)) == 4
    assert problems(CATEGORIES) == []
    stale = CATEGORIES.replace("| 14 |", "| 12 |").replace("2 (+29)", "2 (+26)")
    assert problems(stale) == [
        'README.md:3: says "12", the repo says 14 (api/evals/cases.yaml)',
        'README.md:5: says "2 (+26)", the repo says 2 (+29) (api/evals/cases.yaml)',
    ]


# --- the check ----------------------------------------------------------------------------


def test_a_line_can_opt_out():
    text = (
        "95 indicators <!-- claims: ignore -->\n95 indicators\n# 95 indicators  # claims: ignore\n"
    )
    assert [m.line for m in scan(text, KNOWN, "README.md")] == [2]


def test_the_changelog_and_the_generated_source_list_are_not_scanned(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("62,833 neighbourhoods and 112 indicators\n")
    (tmp_path / "docs" / "guide.md").write_text("Nine themes.\n")
    (tmp_path / "CHANGELOG.md").write_text("## [0.1.0]\n\n- 33,755 neighbourhoods, 64 indicators\n")
    (tmp_path / "docs" / "data-sources.md").write_text("95 datasets, all under open licences.\n")
    assert [p.name for p in scanned_files(tmp_path)] == ["README.md", "guide.md"]
    assert check_claims(tmp_path, KNOWN) == []

    (tmp_path / "docs" / "guide.md").write_text("Intro.\n\nEight themes.\n")
    assert check_claims(tmp_path, KNOWN) == [
        'docs/guide.md:3: says "Eight themes", the repo says 9 (config/indicators.yaml)'
    ]
    assert "CHANGELOG.md" not in {p.name for p in scanned_files(get_project_root())}


def test_the_docs_state_what_the_repository_computes():
    """The guard CI gets: no number in the real docs contradicts the real config, cases
    and eval reports. When this fails, correct the sentence the message names."""
    assert check_claims() == []


def test_the_check_still_reads_the_real_docs():
    """An empty result must mean "all agree", not "nothing was read": the check read 97
    numbers in November 2026. If a rewording took a file's numbers out of its sight,
    teach lix_pipeline/claims.py the new wording (or lower the floor, if they are gone)."""
    root, known = get_project_root(), facts()
    read = {p.name: claims_in(p.read_text(), known) for p in scanned_files(root)}
    assert sum(len(claims) for claims in read.values()) >= 50
    assert read["README.md"] and read["evals.md"]


# --- the command --------------------------------------------------------------------------


@pytest.fixture
def stale_claim(monkeypatch):
    line = 'README.md:15: says "61,000 neighbourhoods", the repo says 62,833 (config/nations.yaml)'
    monkeypatch.setattr("lix_pipeline.claims.check_claims", lambda: [line])
    monkeypatch.setattr("lix_pipeline.docs.stale_docs", lambda: [])
    monkeypatch.setattr("lix_pipeline.docs.write_docs", lambda: ["docs/data-sources.md"])
    return line


def test_lix_docs_check_fails_on_a_stale_claim_and_prints_it(stale_claim, capsys):
    from lix_pipeline.cli import _docs

    assert _docs(argparse.Namespace(check=True)) == 1
    assert stale_claim in capsys.readouterr().out.splitlines()


def test_lix_docs_warns_of_a_stale_claim_without_failing(stale_claim, capsys):
    from lix_pipeline.cli import _docs

    assert _docs(argparse.Namespace(check=False)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["wrote docs/data-sources.md", f"warning: {stale_claim}"]


def test_lix_docs_check_passes_when_nothing_is_stale(monkeypatch, capsys):
    from lix_pipeline.cli import _docs

    # The generated files have their own step in CI; here, the real claims
    monkeypatch.setattr("lix_pipeline.docs.stale_docs", lambda: [])
    assert _docs(argparse.Namespace(check=True)) == 0
    assert "the counts the docs state match the repository" in capsys.readouterr().out
