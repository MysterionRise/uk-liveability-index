"""Tests for the OS Open UPRN stager on a tiny CSV and two square LSOAs."""

from functools import partial
from pathlib import Path

import geopandas as gpd
import polars as pl
from shapely.geometry import box

from lix_pipeline.geo.joins import points_to_lsoa
from lix_pipeline.stage import uprn


def test_points_land_in_their_lsoa_and_others_are_dropped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    raw = tmp_path / "raw" / "os_open_uprn"
    raw.mkdir(parents=True)
    (raw / "osopenuprn_202609.csv").write_text(
        "UPRN,X_COORDINATE,Y_COORDINATE,LATITUDE,LONGITUDE\n"
        "1,500.0,500.0,51.0,-1.0\n"
        "2,1500.0,500.0,51.0,-1.0\n"
        "3,9000.0,500.0,51.0,-1.0\n"  # outside both squares
    )
    squares = gpd.GeoDataFrame(
        {"lsoa21cd": ["E01000001", "E01000002"]},
        geometry=[box(0, 0, 1000, 1000), box(1000, 0, 2000, 1000)],
        crs=27700,
    )
    monkeypatch.setattr(uprn, "points_to_lsoa", partial(points_to_lsoa, polygons=squares))
    monkeypatch.setattr(uprn, "CHUNK", 2)  # exercise the chunking
    df = uprn.stage_os_open_uprn().collect()
    assert df.rows() == [(1, 500, 500, "E01000001"), (2, 1500, 500, "E01000002")]
    assert df.schema["x"] == pl.Int32
