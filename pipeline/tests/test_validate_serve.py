"""``lix validate serve`` on the committed demo dataset (fixtures/demo, `make demo-data`)."""

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from lix_core.config import SERVE_SCHEMA_VERSION
from lix_core.paths import get_project_root
from lix_pipeline.qa.validate import validate_serve

DEMO = get_project_root() / "fixtures" / "demo" / "serve"


def _require_demo() -> None:
    if not DEMO.is_dir():
        pytest.skip("demo dataset not built (make demo-data)")
    version = json.loads((DEMO / "manifest.json").read_text()).get("schema_version")
    if version != SERVE_SCHEMA_VERSION:
        pytest.skip(f"demo dataset is schema v{version}, code reads v{SERVE_SCHEMA_VERSION}")


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    _require_demo()
    out = tmp_path / "serve"
    shutil.copytree(DEMO, out)
    return out


def test_demo_dataset_is_valid():
    _require_demo()
    assert validate_serve(DEMO) == []


def test_refuses_another_schema_version(tmp_path: Path):
    _require_demo()
    out = tmp_path / "serve"
    shutil.copytree(DEMO, out)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["schema_version"] = 1
    (out / "manifest.json").write_text(json.dumps(manifest))
    (problem,) = validate_serve(out)
    assert problem.startswith(f"manifest schema_version 1; this code reads v{SERVE_SCHEMA_VERSION}")


def test_flags_a_file_changed_after_the_manifest(demo_copy):
    (demo_copy / "places.parquet").write_bytes(b"not what the manifest says")
    assert any("places.parquet doesn't match" in p for p in validate_serve(demo_copy))


def test_flags_missing_files(demo_copy):
    (demo_copy / "tiles" / "lsoa.pmtiles").unlink()
    assert validate_serve(demo_copy) == [f"missing tiles/lsoa.pmtiles in {demo_copy}"]


def test_flags_row_count_and_low_coverage(demo_copy):
    path = demo_copy / "lsoa_features.parquet"
    features = pl.read_parquet(path)
    manifest = json.loads((demo_copy / "manifest.json").read_text())
    scored = manifest["scored_indicators"][0]
    features = features.head(100).with_columns(pl.lit(None, pl.Float64).alias(f"n__{scored}"))
    features.write_parquet(path)
    problems = validate_serve(demo_copy)
    assert any("rows, expected" in p for p in problems)
    assert any(p.startswith(f"{scored} covers 0.0%") for p in problems)


def test_staleness_follows_the_publishing_cadence():
    from datetime import datetime, timezone

    from lix_pipeline.serve.scores import is_stale

    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    assert not is_stale("2026-09-20T00:00:00+00:00", "monthly", now)
    assert is_stale("2026-07-01T00:00:00+00:00", "monthly", now)
    assert not is_stale("2020-01-01T00:00:00+00:00", "static", now)
    assert not is_stale(None, "monthly", now)
