"""One grid for every nation: the NSPL stage recomputes coordinates published on another
grid, and boundary files are reprojected on load."""

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.geo.boundaries import load_boundaries
from lix_pipeline.geo.nspl import read_nspl_raw
from lix_pipeline.stage.nspl import reproject_inputs

# Belfast (BT9 6AB): Irish Grid eastings/northings as the NSPL gives them, and lon/lat
BT_ROW = (
    "BT9 6AB",
    "",
    "N20001123",
    "N09000003",
    "N92000002",
    "N99999999",
    "N21000258",
    "N99999999",
    "N23000001",
    54.5737,
    -5.9469,
)
NI_BBOX = (-8.2, 54.0, -5.4, 55.4)


@pytest.fixture
def nspl_with_belfast(tmp_path, nspl_parts):
    header, rows, line = nspl_parts
    path = tmp_path / "NSPL_AUG_2026_UK.csv"
    lines = [line(r) for r in rows[:2]]
    # The shared line builder writes British National Grid numbers; give the Belfast row
    # the Irish Grid ones the real file carries
    bt = line(BT_ROW).replace('"529090"', '"333230"', 1).replace('"179645"', '"372800"', 1)
    path.write_text("\n".join([header, *lines, bt]) + "\n")
    return path


def test_reproject_inputs_moves_only_the_declared_nations(nspl_with_belfast):
    raw = read_nspl_raw(nspl_with_belfast).collect()
    before = raw.filter(pl.col("postcode") == "SW1A 1AA").row(0, named=True)
    out = reproject_inputs(raw.lazy(), grids={29903: ["N"]}, bboxes={"N": NI_BBOX}).collect()
    london = out.filter(pl.col("postcode") == "SW1A 1AA").row(0, named=True)
    belfast = out.filter(pl.col("postcode") == "BT9 6AB").row(0, named=True)
    assert (london["east1m"], london["north1m"]) == (before["east1m"], before["north1m"])
    assert 100_000 < belfast["east1m"] < 200_000  # British National Grid, west of Britain
    assert 500_000 < belfast["north1m"] < 560_000
    assert out.schema["east1m"] == pl.Int32 and out.schema["north1m"] == pl.Int32


def test_reproject_inputs_is_a_no_op_without_other_grids(nspl_with_belfast):
    raw = read_nspl_raw(nspl_with_belfast)
    assert reproject_inputs(raw, grids={}).collect().equals(raw.collect())


def test_reproject_inputs_refuses_points_outside_the_nation(nspl_with_belfast):
    raw = read_nspl_raw(nspl_with_belfast)
    with pytest.raises(ValueError, match="outside the nation's bounding box"):
        reproject_inputs(raw, grids={29903: ["N"]}, bboxes={"N": (-1.0, 51.0, 0.0, 52.0)})


def test_reproject_inputs_leaves_unlocated_postcodes_alone(nspl_with_belfast):
    raw = read_nspl_raw(nspl_with_belfast).collect()
    raw = raw.with_columns(
        pl.when(pl.col("postcode") == "BT9 6AB").then(99.999999).otherwise("lat").alias("lat"),
        pl.when(pl.col("postcode") == "BT9 6AB").then(0.0).otherwise("long").alias("long"),
    )
    out = reproject_inputs(raw.lazy(), grids={29903: ["N"]}, bboxes={"N": NI_BBOX}).collect()
    belfast = out.filter(pl.col("postcode") == "BT9 6AB").row(0, named=True)
    assert belfast["east1m"] == 333230  # untouched: the NSPL's "no grid reference" marker


def test_load_boundaries_reprojects_to_the_working_crs(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    folder = tmp_path / "raw" / "ni_boundaries"
    folder.mkdir(parents=True)
    # A square around Belfast City Hall on Irish Grid
    gpd.GeoDataFrame(
        {"SDZ2021_cd": ["N21000258"]},
        geometry=[box(333_000, 374_000, 334_000, 375_000)],
        crs=29903,
    ).to_file(folder / "SDZ2021.shp")
    gdf = load_boundaries("ni_boundaries", code_column="SDZ2021_cd")
    assert gdf.crs.to_epsg() == 27700
    assert gdf.index.to_list() == ["N21000258"]
    minx, miny, maxx, maxy = gdf.total_bounds
    assert 100_000 < minx < 200_000 and 500_000 < miny < 560_000
    assert 900 < maxx - minx < 1_100  # a kilometre stays a kilometre


def test_load_boundaries_assumes_the_working_crs_when_a_file_has_none(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    folder = tmp_path / "raw" / "bare"
    folder.mkdir(parents=True)
    gpd.GeoDataFrame({"LSOA21CD": ["E01000001"]}, geometry=[box(0, 0, 10, 10)]).to_file(
        folder / "bare.gpkg", driver="GPKG"
    )
    assert load_boundaries("bare").crs.to_epsg() == 27700
