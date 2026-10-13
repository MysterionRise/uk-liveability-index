"""Curated lists: a file in the repository fetched like a download, with a checksum."""

import json

import polars as pl

from lix_core.config import DatasetSpec
from lix_pipeline.fetch import check_source, fetch, resolve


def _spec(path) -> DatasetSpec:
    return DatasetSpec(
        title="A&E sites",
        theme="health",
        coverage=["E"],
        access={"type": "curated", "path": str(path)},
        format="csv",
        description="d",
        licence="OGL-3.0",
        attribution="a",
        cadence="adhoc",
    )


def test_curated_list_is_copied_and_locked(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("lix_pipeline.fetch.lock.lock_path", lambda: tmp_path / "lock.json")
    csv = tmp_path / "sites.csv"
    csv.write_text(
        "name,nation,type,postcode,lat,lon,source,checked_on\nA,E,type1,,51.5,-0.1,osm,2026-10-13\n"
    )
    spec = _spec(csv)
    resolved = resolve("ae_sites", spec, session=None)
    assert resolved["url"] is None and len(resolved["version"]) == 16
    out = fetch("ae_sites", registry={"ae_sites": spec})
    copied = tmp_path / "data" / "raw" / "ae_sites" / "ae_sites.csv"
    assert copied.read_text() == csv.read_text()
    assert out["version"] == resolved["version"] and out["changed"]
    meta = json.loads((copied.parent / ".meta.json").read_text())
    assert meta["completed"] and meta["sha256"] == out["sha256"]
    # Unchanged on a second fetch; edited rows make a new edition
    assert not fetch("ae_sites", registry={"ae_sites": spec})["changed"]
    csv.write_text(csv.read_text() + "B,E,other,,52.0,-1.0,osm,2026-10-13\n")
    assert fetch("ae_sites", registry={"ae_sites": spec})["changed"]
    assert pl.read_csv(copied).height == 2
    status = check_source("ae_sites", spec, session=None)
    assert status["status"] == "ok" and status["note"].startswith("curated list")
