"""The refresh planner: pure decisions from a lockfile and sidecars, no network."""

import json
from pathlib import Path

import pytest

from lix_core.config import IndicatorSpec, load_registry
from lix_pipeline.refresh import plan as p
from lix_pipeline.stage import graph


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture
def data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    return tmp_path


def _raw(data: Path, *slugs: str) -> None:
    for slug in slugs:
        (data / "raw" / slug).mkdir(parents=True, exist_ok=True)
        (data / "raw" / slug / ".meta.json").write_text("{}")


class TestFetchSteps:
    def test_reasons(self, registry):
        lock = {
            "nspl": {"resolved": {"version": "v2"}, "fetched": {"version": "v1", "sha256": "a"}},
            "msoa_names": {"resolved": {"version": "v1"}, "fetched": {"version": "v1"}},
            "viirs_vnl": {"fetched": {"files": {"x.tif": "1"}}},
        }
        steps = {
            s.name: s.reason
            for s in p.fetch_steps(lock, registry, ["nspl", "msoa_names", "viirs_vnl", "gias"])
        }
        assert steps["nspl"] == "new edition v1 → v2"
        assert steps["msoa_names"] == "revalidate"
        assert steps["viirs_vnl"] == "manual: files checked"
        assert steps["gias"] == "never fetched"

    def test_scope_follows_the_nations(self, registry):
        england = set(p.slugs_in_scope(registry, ["E"]))
        both = set(p.slugs_in_scope(registry, ["E", "W"]))
        assert "wg_schools" not in england and "wg_schools" in both and "nspl" in england


class TestStageSteps:
    def test_skipped_stale_and_current(self, registry, data):
        lock = {"pop_lsoa_mye": {"fetched": {"sha256": "p1"}}}
        order = ["population", "wg_ks4_la"]

        todo, skipped = p.stage_steps(lock, registry, ["E"], order)
        assert [s.name for s in todo] == []
        assert {s.name: s.reason for s in skipped} == {
            "population": "inputs not fetched",
            "wg_ks4_la": "covers no active nation",
        }

        _raw(data, "pop_lsoa_mye")
        todo, _ = p.stage_steps(lock, registry, ["E"], order)
        assert [(s.name, s.reason) for s in todo] == [("population", "never built")]

        digest, parts = graph.fingerprint(graph.inputs_for("population", registry, ["E"]), lock)
        graph.write_sidecar(graph.sidecar_path("population"), "population", digest, parts)
        todo, _ = p.stage_steps(lock, registry, ["E"], order)
        assert todo == []

        newer = {"pop_lsoa_mye": {"fetched": {"sha256": "p2"}}}
        todo, _ = p.stage_steps(newer, registry, ["E"], order)
        assert [(s.name, s.reason) for s in todo] == [
            ("population", "inputs changed: pop_lsoa_mye")
        ]
        assert [s.reason for s in p.stage_steps(newer, registry, ["E"], order, force=True)[0]] == [
            "forced"
        ]

    def test_a_changed_upstream_table_restages_its_readers(self, registry, data):
        """flood reads the staged UPRN table: a new UPRN file re-stages flood too."""
        inputs = graph.raw_inputs("flood", registry)
        _raw(data, *inputs)
        lock = {s: {"fetched": {"sha256": f"{s}-1"}} for s in inputs}
        for slug in ("os_open_uprn", "flood"):
            digest, parts = graph.fingerprint(graph.inputs_for(slug, registry, ["E", "W"]), lock)
            graph.write_sidecar(graph.sidecar_path(slug), slug, digest, parts)
        assert p.stage_steps(lock, registry, ["E", "W"], ["os_open_uprn", "flood"])[0] == []

        lock["os_open_uprn"] = {"fetched": {"sha256": "uprn-2"}}
        todo, _ = p.stage_steps(lock, registry, ["E", "W"], ["os_open_uprn", "flood"])
        assert [s.name for s in todo] == ["os_open_uprn", "flood"]


class TestIndicatorsServeTiles:
    SPECS = [
        IndicatorSpec(id="a", theme="housing", label="A", description="", unit="count",
                      direction="higher_better", sources=["s1"], builder="common:column"),
        IndicatorSpec(id="b", theme="housing", label="B", description="", unit="count",
                      direction="higher_better", sources=["s1", "s2"], builder="common:column"),
    ]  # fmt: skip

    def test_indicator_steps(self, data):
        lock = {"s1": {"fetched": {"sha256": "1"}}, "s2": {"fetched": {"sha256": "2"}}}
        assert [s.reason for s in p.indicator_steps(lock, self.SPECS)] == ["never built"] * 2

        graph.write_sidecar(
            p.indicators_sidecar(), "indicators", "", {},
            indicators=p.indicator_fingerprints(lock, self.SPECS),
        )  # fmt: skip
        assert p.indicator_steps(lock, self.SPECS) == []

        lock["s2"] = {"fetched": {"sha256": "2b"}}
        steps = p.indicator_steps(lock, self.SPECS)
        assert [(s.name, s.reason) for s in steps] == [("b", "sources changed: s2")]

    def test_serve_and_tiles_steps(self, data, tmp_path):
        root = tmp_path / "repo"
        (root / "config").mkdir(parents=True)
        for name in p.SERVE_CONFIG:
            (root / "config" / name).write_text(f"{name}: 1\n")
        (data / "indicators").mkdir()
        (data / "indicators" / "long.parquet").write_bytes(b"PAR1")

        assert p.serve_step(root=root).reason == "never built"
        digest, parts = p.serve_fingerprint(root)
        graph.write_sidecar(p.serve_sidecar(), "serve", digest, parts)
        assert p.serve_step(root=root) is None
        (root / "config" / "weights.yaml").write_text("weights: 2\n")
        assert p.serve_step(root=root).reason == "inputs changed: config/weights.yaml"

        lock = {s: {"fetched": {"sha256": "b"}} for s in p.tile_inputs()}
        assert p.tiles_step(lock, ["E"]).reason == "never built"
        digest, parts = p.tiles_fingerprint(lock, ["E"])
        graph.write_sidecar(p.tiles_sidecar(), "tiles", digest, parts)
        assert p.tiles_step(lock, ["E"]) is None
        assert p.tiles_step(lock, ["E", "W"]).reason == "inputs changed: nations"


def test_plan_renders_and_knows_when_it_is_empty(registry, data):
    lock = {}
    plan = p.make_plan(lock, registry, ["E"], ["population"], [], root=data)
    text = plan.render()
    assert "resolve and revalidate" in text and "skip      population" in text
    assert plan.serve.reason == "never built" and not plan.empty()
    assert json.loads(json.dumps(plan.to_dict()))["nations"] == ["E"]
