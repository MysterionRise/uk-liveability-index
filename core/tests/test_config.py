"""Tests for registry loading and validation."""

import pytest
import yaml
from pydantic import ValidationError

from lix_core.config import (
    DatasetSpec,
    IndicatorCatalogue,
    catalogue_problems,
    coverage_problems,
    indicator_coverage,
    load_indicators,
    load_registry,
)
from lix_core.paths import get_project_root

BASE = dict(
    title="T",
    theme="geography",
    format="csv",
    description="d",
    licence="OGL-3.0",
    attribution="a",
    cadence="static",
)


def test_real_registry_is_valid():
    registry = load_registry()
    assert "nspl" in registry
    # YAML anchors live under x- keys and are not datasets
    assert not any(slug.startswith("x-") for slug in registry)


def test_every_dataset_has_open_licence_and_attribution():
    # Copernicus-DEM: GLO-30's licence allows any use, commercial included, and
    # redistribution, on condition of its notice and disclaimer (v0.7.0, #128)
    open_licences = ("OGL", "ODbL", "CC-BY", "CC0", "CDLA", "Apache", "OPL", "Copernicus-DEM")
    for slug, spec in load_registry().items():
        assert spec.licence.startswith(open_licences), f"{slug}: {spec.licence}"
        assert spec.attribution.strip(), slug


def test_access_types_are_discriminated():
    spec = DatasetSpec(**BASE, access={"type": "http", "url": "https://x"})
    assert spec.access.url == "https://x"
    with pytest.raises(ValidationError):
        DatasetSpec(**BASE, access={"type": "ftp", "url": "x"})


def test_unknown_fields_rejected():
    # A typo such as "extracts" must fail loudly rather than be ignored
    with pytest.raises(ValidationError):
        DatasetSpec(**BASE, access={"type": "http", "url": "u"}, extracts=["*.csv"])


def test_arcgis_defaults():
    spec = DatasetSpec(**BASE, access={"type": "arcgis_item", "pin": "abc"})
    assert spec.access.mode == "data"
    assert spec.access.out_sr == 27700


def test_coverage_defaults_to_england_and_rejects_unknown_nations():
    spec = DatasetSpec(**BASE, access={"type": "http", "url": "https://x"})
    assert spec.coverage == ["E"]
    with pytest.raises(ValidationError):
        DatasetSpec(**BASE, access={"type": "http", "url": "u"}, coverage=["E", "X"])


def test_every_real_dataset_declares_coverage_explicitly():
    import yaml

    from lix_core.paths import get_project_root

    raw = yaml.safe_load((get_project_root() / "config" / "datasets.yaml").read_text())
    missing = [s for s, e in raw.items() if not s.startswith("x-") and "coverage" not in e]
    assert not missing, missing


def _catalogue(*indicators: dict) -> IndicatorCatalogue:
    base = dict(
        theme="safety", label="l", description="d", unit="score", direction="lower_better",
        sources=["a"], builder="m:f",
    )  # fmt: skip
    return IndicatorCatalogue(
        themes={"safety": {"label": "Safety", "description": "d"}},
        indicators=[{**base, **i} for i in indicators],
    )


def _registry(**coverage: list[str]) -> dict[str, DatasetSpec]:
    return {
        slug: DatasetSpec(**BASE, access={"type": "http", "url": "https://x"}, coverage=cov)
        for slug, cov in coverage.items()
    }


def test_indicator_coverage_is_the_intersection_of_its_sources():
    registry = _registry(a=["E", "W", "S"], b=["W", "E"])
    cat = _catalogue({"id": "x", "sources": ["a", "b"]}, {"id": "y", "coverage": ["W"]})
    assert indicator_coverage(cat.by_id()["x"], registry) == ["E", "W"]
    assert indicator_coverage(cat.by_id()["y"], registry) == ["W"]


def test_catalogue_rejects_coverage_beyond_sources_and_double_scoring_per_nation():
    registry = _registry(a=["E", "W"], b=["N"])
    cat = _catalogue({"id": "x", "coverage": ["E", "S"]})
    assert catalogue_problems(cat, registry) == ["x: coverage ['S'] beyond its sources' ['E', 'W']"]
    # Two scored indicators may share an overlap group when their nations differ
    cat = _catalogue(
        {"id": "x", "overlap_group": "g"}, {"id": "z", "overlap_group": "g", "sources": ["b"]}
    )
    assert catalogue_problems(cat, registry) == []
    cat = _catalogue({"id": "x", "overlap_group": "g"}, {"id": "z", "overlap_group": "g"})
    assert catalogue_problems(cat, registry) == [
        "overlap group g scores ['x', 'z'] in E",
        "overlap group g scores ['x', 'z'] in W",
    ]


def test_coverage_problems_report_thin_themes_per_nation():
    registry = _registry(a=["E", "W"], b=["E"])
    cat = _catalogue({"id": "x", "weight": 1.0}, {"id": "y", "weight": 3.0, "sources": ["b"]})
    assert coverage_problems(cat, registry, ("E",)) == []
    (problem,) = coverage_problems(cat, registry, ("E", "W"))
    assert problem.startswith("safety keeps 25% of its weight in Wales")
    assert "['y']" in problem


def test_real_catalogue_covers_every_theme_in_england():
    assert coverage_problems(load_indicators(), load_registry(), ("E",)) == []
    nation_benchmarked = {i.id for i in load_indicators().indicators if i.benchmark == "nation"}
    assert {"crime_violence", "house_price", "income_deprivation"} <= nation_benchmarked
    assert "no2" not in nation_benchmarked


def test_explicit_coverage_may_exceed_a_helper_source():
    registry = _registry(a=["E", "W"], b=["E"])
    cat = _catalogue({"id": "x", "coverage": ["E", "W"], "sources": ["a", "b"]})
    assert catalogue_problems(cat, registry) == []
    assert indicator_coverage(cat.by_id()["x"], registry) == ["E", "W"]


def test_label_catalogues_load_and_the_welsh_draft_is_complete():
    from lix_core.config import catalogue_gaps, load_label_catalogues, load_weights

    catalogues = load_label_catalogues()
    assert "cy" in catalogues and catalogues["cy"]["_meta"]["status"] in ("draft", "reviewed")
    gaps = catalogue_gaps(load_indicators(), load_weights(), catalogues["cy"])
    assert gaps == [], gaps[:5]
    assert catalogue_gaps(load_indicators(), load_weights(), {}) != []


def test_every_unit_has_a_code():
    from lix_core.config import UNIT_CODES

    codes = {i.unit_code for i in load_indicators().indicators}
    assert codes <= set(UNIT_CODES.values())
    assert len(set(UNIT_CODES.values())) == len(UNIT_CODES)


def test_substitutes_count_towards_their_targets_at_the_gate():
    registry = _registry(a=["E", "W"], b=["S"])
    cat = _catalogue(
        {"id": "x", "weight": 1.0},
        {"id": "y", "weight": 3.0},
        {"id": "z", "weight": 2.0, "sources": ["b"], "substitute_for": ["y"]},
    )
    assert catalogue_problems(cat, registry) == []
    # S has z (2, capped at y's 3) of the canonical 4 → 50 %, enough; x stays uncovered
    assert coverage_problems(cat, registry, ("E", "S")) == []
    cat = _catalogue(
        {"id": "x", "weight": 1.0},
        {"id": "y", "weight": 3.0},
        {"id": "z", "weight": 1.5, "sources": ["b"], "substitute_for": "y"},
    )
    (problem,) = coverage_problems(cat, registry, ("S",))
    assert problem.startswith("safety keeps 38% of its weight in Scotland")
    assert "['x']" in problem and "'y'" not in problem
    # A declared gap passes
    assert coverage_problems(cat, registry, ("S",), {"S": ["safety"]}) == []


def test_substitute_validation():
    registry = _registry(a=["E", "W"], b=["S"])
    cat = _catalogue({"id": "x"}, {"id": "z", "sources": ["b"], "substitute_for": ["nope"]})
    assert catalogue_problems(cat, registry) == ["z: substitute_for unknown indicator 'nope'"]
    # Both must be scored in the same theme
    cat = _catalogue(
        {"id": "x", "role": "context"}, {"id": "z", "sources": ["b"], "substitute_for": ["x"]}
    )
    assert catalogue_problems(cat, registry) == [
        "z: substitute_for x needs both scored in one theme"
    ]
    # And never cover the same nation
    cat = _catalogue({"id": "x"}, {"id": "z", "substitute_for": ["x"]})
    assert catalogue_problems(cat, registry) == ["z: substitutes for x but both cover ['E', 'W']"]


def test_level_words_are_catalogue_gaps_when_nations_are_given():
    from lix_core.config import catalogue_gaps, load_nations, load_weights

    labels = {"levels": {"E": {"low": {"one": "a", "other": "b"}}}}
    gaps = catalogue_gaps(load_indicators(), load_weights(), labels, load_nations().nations)
    assert "levels.E.low" not in gaps
    assert "levels.E.mid" in gaps and "levels.W.low" in gaps


def test_catalogue_gaps_flag_an_entry_split_by_an_unquoted_comma():
    from lix_core.config import catalogue_gaps, load_indicators, load_weights

    catalogue, weights = load_indicators(), load_weights()
    labels = yaml.safe_load(
        (get_project_root() / "config" / "i18n" / "cy.yaml").read_text(encoding="utf-8")
    )
    assert not [g for g in catalogue_gaps(catalogue, weights, labels) if "unexpected" in g]
    broken = yaml.safe_load(
        "presets:\n  family: {label: Teulu, description: Ysgolion da, diogelwch.}\n"
    )
    labels["presets"]["family"] = broken["presets"]["family"]
    gaps = catalogue_gaps(catalogue, weights, labels)
    assert any(g.startswith("presets.family: unexpected fields") for g in gaps)
