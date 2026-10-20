"""The QA report's warnings: a nation holding a tail, a constant scored indicator, an
Irish sub-district with no crime at all."""

import polars as pl

from lix_pipeline.qa.report import constant_warnings, tail_warnings, zero_crime_warnings


def test_tail_warnings_name_the_nation_that_holds_a_tail_unless_it_compares_within():
    n = 400
    pct = [i / (n - 1) * 100 for i in range(n)]
    nation = ["I" if i >= n - 4 or i < 4 else "E" for i in range(n)]
    features = pl.DataFrame({"overall_pct_all": pct, "nation": nation})
    warnings = tail_warnings(features, {"E": "all", "I": "all"})
    assert len(warnings) == 2 and all(w.startswith("I holds 100%") for w in warnings)
    assert tail_warnings(features, {"E": "all", "I": "nation"}) == []


def test_constant_warnings_flag_a_scored_indicator_stuck_at_one_value():
    features = pl.DataFrame(
        {"raw__leisure": [0.0] * 8 + [0.2, 0.4], "raw__no2": list(range(10))}
    )  # fmt: skip
    assert constant_warnings(features, ["leisure", "no2"]) == [
        "leisure reads 0.0 for 80% of the areas that have it"
    ]


def test_zero_crime_warnings_find_a_populous_irish_district_with_no_crime():
    features = pl.DataFrame(
        {
            "nation": ["I", "I", "I", "E"],
            "msoa21cd": ["I02000001", "I02000001", "I02000002", "E02000001"],
            "population": [4000, 3000, 300, 9000],
            "raw__crime_violence": [0.0, 0.0, 0.0, 0.0],
            "raw__crime_burglary": [0.0, 0.0, 0.0, 0.0],
        }
    )
    out = zero_crime_warnings(features)
    assert len(out) == 1 and out[0].startswith("I02000001: 2 Irish areas with 7,000 people")


def test_every_indicator_source_feeds_a_nation_it_covers():
    """The shipped catalogue names no source for a nation an indicator excludes (#152)."""
    from lix_core.config import load_indicators, load_registry
    from lix_pipeline.qa.validate import orphan_source_problems

    assert orphan_source_problems(load_indicators(), load_registry()) == []


def test_docs_check_ignores_the_data_for_column_only():
    from lix_pipeline.docs import _without_period

    a = "| `x` | [T](u) | what | England | timetable for 3 November 2026 | daily | OGL-3.0 |"
    b = "| `x` | [T](u) | what | England | downloaded 20 October 2026 | daily | OGL-3.0 |"
    c = "| `x` | [T](u) | what | Wales | downloaded 20 October 2026 | daily | OGL-3.0 |"
    assert _without_period(a) == _without_period(b)
    assert _without_period(a) != _without_period(c)
    assert _without_period("## Heading\ntext") == "## Heading\ntext"
