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

        digest, parts = graph.stage_fingerprint("population", registry, ["E"], lock)
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

    def test_a_table_built_for_other_nations_is_stale(self, registry, data):
        lock = {"pop_lsoa_mye": {"fetched": {"sha256": "p1"}}}
        _raw(data, "pop_lsoa_mye")
        digest, parts = graph.stage_fingerprint("population", registry, ["E"], lock)
        # England and Wales share the population file, so widening from E to E,W changes
        # no input, only the nation set
        graph.write_sidecar(
            graph.sidecar_path("population"), "population", digest, parts, nations=["E"]
        )
        assert p.stage_steps(lock, registry, ["E"], ["population"])[0] == []
        todo, _ = p.stage_steps(lock, registry, ["E", "W"], ["population"])
        assert [(s.name, s.reason) for s in todo] == [("population", "nations changed: E → E,W")]
        # A sidecar from before the field was recorded is not disturbed
        graph.write_sidecar(graph.sidecar_path("population"), "population", digest, parts)
        assert p.stage_steps(lock, registry, ["E", "W"], ["population"])[0] == []

    def test_a_changed_upstream_table_restages_its_readers(self, registry, data):
        """flood reads the staged UPRN table: a new UPRN file re-stages flood too."""
        inputs = graph.raw_inputs("flood", registry)
        _raw(data, *inputs)
        lock = {s: {"fetched": {"sha256": f"{s}-1"}} for s in inputs}
        for slug in ("os_open_uprn", "flood"):
            digest, parts = graph.stage_fingerprint(slug, registry, ["E", "W"], lock)
            graph.write_sidecar(graph.sidecar_path(slug), slug, digest, parts)
        assert p.stage_steps(lock, registry, ["E", "W"], ["os_open_uprn", "flood"])[0] == []

        lock["os_open_uprn"] = {"fetched": {"sha256": "uprn-2"}}
        todo, _ = p.stage_steps(lock, registry, ["E", "W"], ["os_open_uprn", "flood"])
        assert [s.name for s in todo] == ["os_open_uprn", "flood"]

    def test_a_restaged_table_cascades_to_its_readers(self, registry, data, monkeypatch):
        """#190: a table re-staged for its code, not its raw file, re-stages the stagers
        that read it (homes reads nspl; geo_lsoa reads both), while the same table
        re-staged by hand from the same inputs and code cascades nowhere."""
        monkeypatch.setattr(graph, "stager_code_hash", lambda slug: f"code-{slug}")
        order = ["nspl", "homes", "geo_lsoa"]
        inputs = {s for slug in order for s in graph.raw_inputs(slug, registry)}
        _raw(data, *inputs)
        lock = {s: {"fetched": {"sha256": f"{s}-1"}} for s in inputs}
        for slug in order:
            graph.record_staged(slug, registry, ["E"], lock)
        assert p.stage_steps(lock, registry, ["E"], order)[0] == []

        graph.record_staged("nspl", registry, ["E"], lock)  # by hand, nothing changed
        assert p.stage_steps(lock, registry, ["E"], order)[0] == []

        monkeypatch.setattr(
            graph, "stager_code_hash", lambda slug: "code-2" if slug == "nspl" else f"code-{slug}"
        )
        todo, _ = p.stage_steps(lock, registry, ["E"], order)
        assert [(s.name, s.reason) for s in todo] == [
            ("nspl", "code changed"),
            ("homes", "inputs restaged: nspl"),
            ("geo_lsoa", "inputs restaged: nspl, homes"),
        ]
        # Re-staged by hand under the new code: its sidecar moved, so its readers see it
        graph.record_staged("nspl", registry, ["E"], lock)
        todo, _ = p.stage_steps(lock, registry, ["E"], order)
        assert [(s.name, s.reason) for s in todo] == [
            ("homes", "staged inputs changed: nspl"),
            ("geo_lsoa", "staged inputs changed: nspl"),
        ]

    def test_dependents_follow_the_read_graph_in_run_order(self):
        from lix_pipeline.stage import stagers

        order = list(stagers())
        deps = graph.dependents("nspl", order)
        assert {"homes", "geo_lsoa", "flood", "deprivation", "schools", "noise"} <= set(deps)
        assert "nspl" not in deps and "iod_2025" not in deps
        assert deps == [s for s in order if s in set(deps)]
        assert graph.dependents("night_lights", order) == []

    def test_rehash_brings_old_sidecars_under_the_current_scheme(self, registry, data, monkeypatch):
        monkeypatch.setattr(graph, "stager_code_hash", lambda slug: "c")
        inputs = graph.raw_inputs("homes", registry)
        _raw(data, *inputs)
        lock = {s: {"fetched": {"sha256": "1"}} for s in inputs}
        for slug in ("nspl", "homes"):  # sidecars of the old scheme: raw checksums only
            digest, parts = graph.fingerprint(graph.inputs_for(slug, registry, ["E"]), lock)
            graph.write_sidecar(
                graph.sidecar_path(slug), slug, digest, parts, nations=["E"], rows=3
            )
        todo, _ = p.stage_steps(lock, registry, ["E"], ["nspl", "homes"])
        assert [s.reason for s in todo] == [
            "code changed",
            "staged inputs changed: nspl; code changed",
        ]
        assert graph.rehash_sidecars(["nspl", "homes"], registry, ["E"], lock) == (
            ["nspl", "homes"],
            {},
        )
        assert p.stage_steps(lock, registry, ["E"], ["nspl", "homes"])[0] == []
        assert graph.read_sidecar(graph.sidecar_path("homes"))["rows"] == 3

        # A stager edited since its table was built is stale, not mis-hashed: the rehash
        # leaves it (and the cascade to its readers) for the refresh (#215)
        monkeypatch.setattr(graph, "stager_code_hash", lambda slug: "c2" if slug == "nspl" else "c")
        done, refused = graph.rehash_sidecars(["nspl", "homes"], registry, ["E"], lock)
        assert done == ["homes"] and refused == {"nspl": "code"}
        todo, _ = p.stage_steps(lock, registry, ["E"], ["nspl", "homes"])
        assert [(s.name, s.reason) for s in todo] == [
            ("nspl", "code changed"),
            ("homes", "inputs restaged: nspl"),
        ]
        lock["nspl"] = {"fetched": {"sha256": "2"}}  # a raw input moved since homes was built
        assert "nspl" in graph.rehash_sidecars(["homes"], registry, ["E"], lock)[1]["homes"]

    def test_the_code_hash_follows_the_stager_and_what_it_calls(self):
        """Every stager hashes; the hash is stable within a process and differs between
        stagers that share no code."""
        assert graph.stager_code_hash("nspl") == graph.stager_code_hash("nspl")
        assert graph.stager_code_hash("nspl") != graph.stager_code_hash("homes")
        assert graph.stager_code_hash("no_such_stager") == "missing"


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

        graph.write_sidecar(
            p.indicators_sidecar(), "indicators", "", {},
            indicators=p.indicator_fingerprints(lock, self.SPECS), nations=["E", "W"],
        )  # fmt: skip
        assert p.indicator_steps(lock, self.SPECS, nations=["E", "W"]) == []
        steps = p.indicator_steps(lock, self.SPECS, nations=["E", "W", "S"])
        assert [s.reason for s in steps] == ["nations changed: E,W → E,W,S"] * 2

    def test_restaged_inputs_rebuild_their_indicators(self, registry, data):
        """A stager that runs again for its boundaries or nations, not its raw file,
        still makes the indicators reading its table stale (v0.5.0's first refresh
        kept the crime, air and broadband tables' Northern Irish rows out of the
        indicators because police.uk's and Defra's files had not changed)."""
        specs = [
            IndicatorSpec(id="crime", theme="safety", label="C", description="", unit="n",
                          direction="lower_better", sources=["police_crime"],
                          builder="common:column"),
            IndicatorSpec(id="bounds", theme="safety", label="B", description="", unit="n",
                          direction="lower_better", sources=["lsoa_boundaries"],
                          builder="common:column"),
            IndicatorSpec(id="other", theme="safety", label="O", description="", unit="n",
                          direction="lower_better", sources=["s1"], builder="common:column"),
        ]  # fmt: skip
        stage = [p.Step("police_crime", "nations changed: E,W → E,W,N")]
        steps = p.restaged_indicator_steps(stage, registry, ["E", "W", "N"], specs, {"bounds"})
        assert [(s.name, s.reason) for s in steps] == [("crime", "inputs restaged: police_crime")]
        assert p.restaged_indicator_steps([], registry, ["E"], specs, set()) == []

    def test_a_restaged_table_touches_only_its_readers(self, registry):
        """The November 2026 refresh re-staged flood and then rebuilt 50 indicators
        (population_density, sunshine, house_price…) that never read it, because every
        indicator lists NSPL and flood reads homes, which reads NSPL (#205)."""
        spec = lambda id, *sources: IndicatorSpec(  # noqa: E731
            id=id, theme="environment", label=id, description="", unit="n",
            direction="lower_better", sources=list(sources), builder="common:column",
        )  # fmt: skip
        specs = [
            spec("flood_risk", "ea_flood_postcodes", "nrw_fraw_rivers", "nspl", "te_buildings"),
            spec("population_density", "pop_lsoa_mye", "nspl", "lsoa_boundaries"),
            spec("tree_canopy", "esa_worldcover", "nspl", "te_buildings"),
        ]
        nations = ["E", "W", "S", "N", "I"]
        steps = p.restaged_indicator_steps(
            [p.Step("flood", "code changed")], registry, nations, specs, set()
        )
        assert [(s.name, s.reason) for s in steps] == [("flood_risk", "inputs restaged: flood")]

        # A re-staged homes table moves every home-based indicator, and its readers
        # (planned by stage_steps in the same run) cascade through their own inputs
        stage = [p.Step("homes", "inputs changed: nspl"), p.Step("flood", "inputs restaged: homes")]
        steps = p.restaged_indicator_steps(stage, registry, nations, specs, set())
        assert {s.name for s in steps} == {"flood_risk", "population_density", "tree_canopy"}
        assert (
            dict((s.name, s.reason) for s in steps)["flood_risk"] == "inputs restaged: flood, homes"
        )

    def test_a_table_restaged_by_hand_makes_its_indicators_stale(self, registry, data):
        """On 27 October 2026 the noise table was re-staged by hand and the next refresh
        planned none of the five noise indicators: the indicator sidecar knew the raw
        checksums only (#215). It records the staged tables' fingerprints now."""
        nations = ["E", "N"]
        spec = IndicatorSpec(
            id="aircraft_noise", theme="environment", label="A", description="", unit="%",
            direction="lower_better", sources=["defra_noise_air_lden", "daera_noise_r3", "nspl"],
            builder="environment:percent", params={"slug": "noise", "column": "share_air_55"},
        )  # fmt: skip
        other = IndicatorSpec(
            id="tree_canopy", theme="environment", label="T", description="", unit="%",
            direction="higher_better", sources=["esa_worldcover", "nspl"],
            builder="common:column", params={"slug": "tree_cover", "column": "canopy_pct"},
        )  # fmt: skip
        feeds = p.table_feeds(registry, nations)
        assert p.indicator_tables(spec, feeds) == ["homes", "noise", "nspl"]
        for table in ("noise", "homes", "nspl", "tree_cover"):
            graph.write_sidecar(graph.sidecar_path(table), table, f"{table}-1", {})
        lock = {}
        graph.write_sidecar(
            p.indicators_sidecar(), "indicators", "", {}, nations=nations,
            indicators=p.indicator_fingerprints(lock, [spec, other], registry, nations),
        )  # fmt: skip
        assert p.indicator_steps(lock, [spec, other], nations=nations, registry=registry) == []

        graph.write_sidecar(graph.sidecar_path("noise"), "noise", "noise-2", {})
        steps = p.indicator_steps(lock, [spec, other], nations=nations, registry=registry)
        assert [(s.name, s.reason) for s in steps] == [
            ("aircraft_noise", "staged inputs changed: noise")
        ]

    def test_serve_and_tiles_steps(self, data, tmp_path):
        root = tmp_path / "repo"
        (root / "config" / "i18n").mkdir(parents=True)
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
        # A corrected translation re-scores too: the catalogues ride the manifest (#197)
        digest, parts = p.serve_fingerprint(root)
        graph.write_sidecar(p.serve_sidecar(), "serve", digest, parts)
        (root / "config" / "i18n" / "cy.yaml").write_text("themes: {}\n")
        assert p.serve_step(root=root).reason == "inputs changed: config/i18n/cy.yaml"

        lock = {s: {"fetched": {"sha256": "b"}} for s in p.tile_inputs()}
        assert p.tiles_step(lock, ["E"]).reason == "never built"
        digest, parts = p.tiles_fingerprint(lock, ["E"])
        graph.write_sidecar(p.tiles_sidecar(), "tiles", digest, parts)
        assert p.tiles_step(lock, ["E"]) is None
        assert p.tiles_step(lock, ["E", "W"]).reason == "inputs changed: nations"
        # The hover names come from geo_lsoa: a rename re-cuts the tiles (#194's names
        # shipped in a pack whose tiles still said "Cong/Ross 001")
        (data / "staged").mkdir(exist_ok=True)
        graph.write_sidecar(graph.sidecar_path("geo_lsoa"), "geo_lsoa", "renamed", {})
        assert p.tiles_step(lock, ["E"]).reason == "staged inputs changed: geo_lsoa"


def test_plan_renders_and_knows_when_it_is_empty(registry, data):
    lock = {}
    plan = p.make_plan(lock, registry, ["E"], ["population"], [], root=data)
    text = plan.render()
    assert "resolve and revalidate" in text and "skip      population" in text
    assert plan.serve.reason == "never built" and not plan.empty()
    assert json.loads(json.dumps(plan.to_dict()))["nations"] == ["E"]
