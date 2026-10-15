"""The serve layout of v0.3.0: quality codes, nation-level areas, coverage per nation."""

import polars as pl
import pytest

from lix_core.config import SERVE_SCHEMA_VERSION, IndicatorSpec, load_indicators
from lix_core.quality import QUALITY_CODE
from lix_pipeline import indicators as ind
from lix_pipeline.qa.validate import indicator_coverage_problems
from lix_pipeline.serve.lookups import build_areas
from lix_pipeline.serve.scores import MISSING_U16, compact_scores


def _features(n: int = 4) -> pl.DataFrame:
    scored = [i.id for i in load_indicators().scored()]
    df = pl.DataFrame(
        {
            "lsoa21cd": ["E01000001", "E01000002", "W01000001", "W01000002"][:n],
            "msoa21cd": ["E02000001", "E02000001", "W02000001", "W02000001"][:n],
            "msoa_name": ["Head", "Head", "Cathays", "Cathays"][:n],
            "lad_cd": ["E08000035", "E08000035", "W06000015", "W06000015"][:n],
            "lad_nm": ["Leeds", "Leeds", "Cardiff", "Cardiff"][:n],
            "rgn_cd": ["E12000003", "E12000003", "W92000004", "W92000004"][:n],
            "rgn_nm": ["Yorkshire and The Humber"] * 2 + ["Wales"] * 2,
            "nation": ["E", "E", "W", "W"][:n],
            "ctry_cd": ["E92000001", "E92000001", "W92000004", "W92000004"][:n],
            "ruc_class": ["urban", "town", "urban", "rural"][:n],
            "urban": [True, False, True, False][:n],
            "population": [1500, 1600, 1700, 1800][:n],
            "bbox_w": [-1.6, -1.5, -3.2, -3.1][:n],
            "bbox_s": [53.8, 53.8, 51.4, 51.5][:n],
            "bbox_e": [-1.5, -1.4, -3.1, -3.0][:n],
            "bbox_n": [53.9, 53.9, 51.5, 51.6][:n],
        }
    )
    for iid in scored:
        df = df.with_columns(
            pl.Series(f"n__{iid}", [10.0, 20.0, None, 40.0][:n]),
            pl.Series(f"q__{iid}", ["ok", "imputed", "missing", "low_n"][:n]),
        )
    return df


class TestCompactScores:
    def test_quality_codes_and_grouping_columns(self):
        out = compact_scores(_features())
        first = load_indicators().scored()[0].id
        assert out.schema[f"q__{first}"] == pl.UInt8
        expected = [QUALITY_CODE[q] for q in ("ok", "imputed", "missing", "low_n")]
        assert out[f"q__{first}"].to_list() == expected
        assert out[first].to_list() == [1000, 2000, MISSING_U16, 4000]
        assert out["nation"].to_list() == ["E", "E", "W", "W"]
        assert out["ruc_class"].to_list() == ["urban", "town", "urban", "rural"]
        assert "quality_flags" not in out.columns and "ruc21cd" not in out.columns


class TestAreas:
    def test_nation_level_and_no_pseudo_region(self):
        areas = build_areas(_features())
        by_level = {lvl: df for (lvl,), df in areas.partition_by("level", as_dict=True).items()}
        assert set(by_level) == {"msoa", "lad", "region", "nation"}
        assert by_level["region"]["code"].to_list() == ["E12000003"]  # Wales has none
        nation = by_level["nation"].sort("code")
        assert nation["code"].to_list() == ["E92000001", "W92000004"]
        assert nation["name"].to_list() == ["England", "Wales"]
        assert nation["lsoas"].to_list() == [2, 2]
        assert set(areas["nation"]) == {"E", "W"}


class TestNotAvailable:
    def test_values_outside_coverage_become_not_available(self, monkeypatch):
        spec = IndicatorSpec(
            id="x", theme="safety", label="l", description="d", unit="u",
            direction="lower_better", sources=["police_crime"], builder="m:f",
        )  # fmt: skip

        class Ctx:
            geo = pl.DataFrame(
                {"lsoa21cd": ["E01000001", "W01000001", "W01000002"], "nation": ["E", "W", "W"]}
            )

        values = pl.DataFrame({"lsoa21cd": ["E01000001", "W01000001"], "value": [1.0, 2.0]})
        monkeypatch.setattr(ind, "_builder", lambda spec: lambda ctx, **params: values)
        out = ind.build_indicator(spec, Ctx(), coverage=["E"])
        assert out["value"].to_list() == [1.0, None, None]
        assert out["quality"].to_list() == ["ok", "not_available", "not_available"]
        # Without a coverage list the builder's values stand, and a gap is "missing"
        out = ind.build_indicator(spec, Ctx())
        assert out["quality"].to_list() == ["ok", "ok", "missing"]


class TestCoverageProblems:
    def test_per_nation_checks(self):
        features = _features().with_columns(
            pl.Series("n__a", [1.0, 2.0, None, None]),
            pl.Series("q__a", ["ok", "ok", "not_available", "not_available"]),
            pl.Series("n__b", [1.0, 2.0, 3.0, None]),
            pl.Series("q__b", ["ok", "ok", "ok", "missing"]),
        )
        problems = indicator_coverage_problems(
            features, ["a", "b"], {"a": ["E"], "b": ["E", "W"]}, ["E", "W"]
        )
        assert problems == ["b covers 50.0% of Wales (< 99%)"]
        problems = indicator_coverage_problems(features, ["a"], {"a": ["E"]}, ["E", "W"])
        assert problems == []
        features = features.with_columns(pl.Series("q__a", ["ok", "ok", "ok", "not_available"]))
        problems = indicator_coverage_problems(features, ["a"], {"a": ["E"]}, ["W"])
        assert problems == [
            "a is not built for Wales but has 0 values and 1 rows not flagged not_available"
        ]


def test_manifest_has_geography_and_quality_levels():
    from lix_core.quality import QUALITY_LEVELS
    from lix_pipeline.serve.scores import manifest

    m = manifest({}, lsoa_count=4, area_counts={"E": 2, "W": 2})
    assert m["schema_version"] == SERVE_SCHEMA_VERSION
    assert m["geography"]["active"] == ["E", "W"]
    assert m["geography"]["area_counts"] == {"E": 2, "W": 2}
    assert m["geography"]["nations"]["W"]["levels"]["low"]["count"] == 1917
    assert m["geography"]["country"]["currency"] == "GBP"
    assert m["quality_levels"] == list(QUALITY_LEVELS)
    assert {lang["code"] for lang in m["languages"]} >= {"en", "cy"}
    assert m["i18n"]["cy"]["themes"]["safety"]["label"] == "Diogelwch"
    assert "_meta" not in m["i18n"]["cy"]
    crime = next(i for i in m["indicators"] if i["id"] == "crime_violence")
    assert crime["unit_code"] == "per_1000_year"
    assert crime["benchmark"] == "nation" and crime["coverage"] == ["E", "W", "N"]
    no2 = next(i for i in m["indicators"] if i["id"] == "no2")
    # Declared coverage is the indicator's, not the build's: Defra's grid covers the UK
    assert no2["benchmark"] == "uk" and no2["coverage"] == ["E", "W", "S", "N"]


@pytest.fixture(autouse=True)
def _both_nations(monkeypatch):
    monkeypatch.setenv("LIX_NATIONS", "E,W")


def test_stale_reason_tells_a_newer_edition_from_an_old_download():
    from datetime import datetime, timezone

    from lix_pipeline.serve.scores import stale_reason

    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    fresh = {
        "resolved": {"version": "v1"},
        "fetched": {"version": "v1", "fetched_at": "2026-10-01"},
    }
    assert stale_reason(fresh, "monthly", now) == "none"
    newer = {
        "resolved": {"version": "v2"},
        "fetched": {"version": "v1", "fetched_at": "2026-10-01"},
    }
    assert stale_reason(newer, "monthly", now) == "newer_edition"
    old = {"resolved": {"version": "v1"}, "fetched": {"version": "v1", "fetched_at": "2026-01-01"}}
    assert stale_reason(old, "monthly", now) == "older_than_cadence"
    assert stale_reason(old, "static", now) == "none"
    # A fetched section without a version (older lockfiles) cannot be a newer edition
    legacy = {"resolved": {"version": "v2"}, "fetched": {"fetched_at": "2026-10-01"}}
    assert stale_reason(legacy, "monthly", now) == "none"


def test_correlation_matrix_has_no_nan_for_indicators_that_share_no_area():
    import json

    import polars as pl

    from lix_pipeline.serve.scores import correlation_matrix

    # crime_violence covers England and Wales, recorded_crime Scotland: no row has both
    features = pl.DataFrame(
        {
            "n__crime_violence": [0.1, 0.5, 0.9, None, None],
            "n__recorded_crime": [None, None, None, 0.2, 0.8],
            "n__no2": [0.2, 0.4, 0.8, 0.3, 0.7],
        }
    )
    c = correlation_matrix(features)
    assert c["ids"] == ["no2", "recorded_crime", "crime_violence"] or set(c["ids"]) == {
        "no2",
        "recorded_crime",
        "crime_violence",
    }
    i, j = c["ids"].index("crime_violence"), c["ids"].index("recorded_crime")
    assert c["rho"][i][j] is None and c["rho"][j][i] is None
    assert c["rho"][i][i] == 1.0
    json.dumps(c, allow_nan=False)  # must be valid JSON for the browser
