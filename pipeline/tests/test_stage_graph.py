"""The stager dependency graph: declarations match the code, closures and fingerprints."""

import json
from pathlib import Path

import pytest

from lix_core.config import load_registry
from lix_pipeline.stage import STAGE_INPUTS, STAGE_READS, stagers
from lix_pipeline.stage import graph as g


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture(scope="module")
def all_stagers():
    return stagers()


def test_declared_staged_reads_match_the_code(all_stagers):
    """Every staged table a stager's source reads is declared, and nothing more."""
    problems = []
    for slug, fn in all_stagers.items():
        staged, _ = g.derived_reads(fn)
        declared = set(STAGE_READS.get(slug, []))
        if staged != declared:
            problems.append(f"{slug}: code reads {sorted(staged)}, declared {sorted(declared)}")
    assert not problems, "\n".join(problems)


def test_declared_raw_inputs_cover_the_code(all_stagers, registry):
    """Every raw dataset a stager's source reads (directly or through a helper that reads
    the boundaries or the NSPL documents) is among its declared inputs."""
    problems = []
    for slug, fn in all_stagers.items():
        _, raw = g.derived_reads(fn)
        declared = set(g.expand_inputs(STAGE_INPUTS.get(slug, [slug]), registry))
        missing = raw - declared
        if missing:
            problems.append(f"{slug}: reads {sorted(missing)} but declares {sorted(declared)}")
    assert not problems, "\n".join(problems)


def test_every_declared_read_is_a_stager(all_stagers):
    unknown = {t for reads in STAGE_READS.values() for t in reads} - set(all_stagers)
    assert not unknown


def test_stagers_run_after_the_tables_they_read(all_stagers):
    """stagers() is the run order, so a reader must come after each table it reads:
    the planner's one-pass cascade and the rehash walk the same order (#202)."""
    order = list(all_stagers)
    early = [
        f"{slug} (position {order.index(slug)}) reads {table} (position {order.index(table)})"
        for slug in order
        for table in STAGE_READS.get(slug, [])
        if order.index(table) > order.index(slug)
    ]
    assert not early, "\n".join(early)


def test_raw_inputs_close_over_staged_reads(registry):
    # flood reads the staged UPRN table, whose stager reads the raw boundaries
    inputs = g.raw_inputs("flood", registry)
    assert {"os_open_uprn", "lsoa_boundaries", "nspl", "voa_ctsop", "nrw_fraw_sea"} <= set(inputs)
    # osm_* expands to every extract in the registry
    assert {"osm_england", "osm_wales"} <= set(g.raw_inputs("osm_pois", registry))
    assert {"osm_england", "osm_wales"} <= set(g.raw_inputs("childcare", registry))


def test_inputs_for_drop_datasets_outside_the_nations(registry):
    england = g.inputs_for("noise", registry, ["E"])
    both = g.inputs_for("noise", registry, ["E", "W"])
    assert "wg_noise_road_lden" not in england and "wg_noise_road_lden" in both
    assert "defra_noise_road_lden" in england


def test_covers_and_has_inputs(registry, tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    assert not g.covers("wg_ks4_la", registry, ["E"])
    assert g.covers("wg_ks4_la", registry, ["E", "W"])
    assert not g.has_inputs("population", registry, ["E"])
    (tmp_path / "raw" / "pop_lsoa_mye").mkdir(parents=True)
    (tmp_path / "raw" / "pop_lsoa_mye" / ".meta.json").write_text("{}")
    assert g.has_inputs("population", registry, ["E"])


def test_a_keyed_source_without_its_key_does_not_hold_a_stager_back(
    registry, tmp_path, monkeypatch
):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OCM_API_KEY", raising=False)
    for slug in g.inputs_for("chargers", registry, ["I"]):
        if slug.startswith("open_charge_map"):
            continue
        (tmp_path / "raw" / slug).mkdir(parents=True, exist_ok=True)
        (tmp_path / "raw" / slug / ".meta.json").write_text("{}")
    assert g.has_inputs("chargers", registry, ["I"])  # Open Charge Map is skipped without a key
    monkeypatch.setenv("OCM_API_KEY", "set")
    assert not g.has_inputs("chargers", registry, ["I"])  # with a key it must be fetched


LOCK = {
    "a": {"fetched": {"sha256": "aa"}},
    "b": {"fetched": {"sha256": "bb"}},
    "m": {"fetched": {"files": {"x.tif": "11", "y.tif": "22"}}},
}


def test_fingerprint_and_source_checksum():
    digest, parts = g.fingerprint(["b", "a", "m", "missing"], LOCK)
    assert parts["a"] == "aa" and parts["missing"] is None
    assert parts["m"] == g.source_checksum("m", LOCK) and len(parts["m"]) == 64
    same, _ = g.fingerprint(["a", "b", "m", "missing"], LOCK)
    assert digest == same  # order does not matter
    changed, _ = g.fingerprint(["a", "b", "m"], {**LOCK, "a": {"fetched": {"sha256": "a2"}}})
    assert changed != digest


def test_sidecar_round_trip_and_stale_reason(tmp_path: Path):
    path = tmp_path / "t.meta.json"
    digest, parts = g.fingerprint(["a", "b"], LOCK)
    assert g.stale_reason(path, digest, parts) == "never built"

    g.write_sidecar(path, "t", digest, parts, rows=3)
    assert json.loads(path.read_text())["rows"] == 3
    assert g.stale_reason(path, digest, parts) is None

    new_lock = {**LOCK, "b": {"fetched": {"sha256": "b2"}}}
    digest2, parts2 = g.fingerprint(["a", "b"], new_lock)
    assert g.stale_reason(path, digest2, parts2) == "inputs changed: b"


def test_a_module_constant_is_part_of_the_code_hash(monkeypatch):
    """The noise stager reads its file patterns from NOISE_SOURCES: a corrected pattern
    must re-stage the table (the v0.6.3 release pass fixed one and nothing cascaded)."""
    from lix_pipeline.stage import graph, noise

    graph._code_hash.cache_clear()
    before = graph.stager_code_hash("noise")
    monkeypatch.setattr(noise, "NIGHT_DB", noise.NIGHT_DB + 1)
    graph._code_hash.cache_clear()
    after = graph.stager_code_hash("noise")
    graph._code_hash.cache_clear()
    assert before != after
    assert graph._stable_repr({"a": frozenset({"y", "x"}), "b": (1, 2.5, None)}) == (
        "{'a': {'x', 'y'}, 'b': [1, 2.5, None]}"
    )
