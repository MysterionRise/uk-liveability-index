"""Scotland's backbone pieces: NRS population tidy-up, the Data Zone lookup, per-nation
polygons and the language columns of places."""

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.geo.boundaries import load_boundaries, tier_polygons
from lix_pipeline.stage.population import median_age_from_counts, tidy_sape


def test_tidy_sape_counts_children_pensioners_and_median_age():
    ages = {f"Age {a}": [1, 0] for a in range(90)}
    ages["Age 90 and over"] = [1, 0]
    ages["Age 10"] = [5, 10]  # one zone young, the other all ten-year-olds
    df = pl.DataFrame(
        {
            "Data zone code": ["S01000001", "S01000002"],
            "Data zone name": ["A", "B"],
            "Sex": ["Persons", "Persons"],
            "Total population": [95, 10],
            **ages,
        }
    )
    both = pl.concat([df, df.with_columns(pl.lit("Females").alias("Sex"))])
    out = tidy_sape(both, 2024)
    assert out.height == 2 and out["pop_year"].to_list() == [2024, 2024]
    a, b = out.sort("lsoa21cd").rows(named=True)
    assert a["population"] == 95 and a["pop_children_0_15"] == 20 and a["pop_65_plus"] == 26
    assert b["population"] == 10 and b["pop_children_0_15"] == 10 and b["pop_65_plus"] == 0
    assert b["median_age"] == pytest.approx(10.5)
    assert 40 < a["median_age"] < 50


def test_median_age_interpolates_within_the_year():
    df = pl.DataFrame({"Age 0": [2.0], "Age 1": [2.0], "Age 2": [0.0]})
    assert median_age_from_counts(df, ["Age 0", "Age 1", "Age 2"]).to_list() == [1.0]


def test_tier_polygons_reads_each_nations_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    ew = tmp_path / "raw" / "lsoa_boundaries"
    sc = tmp_path / "raw" / "sg_dz22_boundaries"
    ew.mkdir(parents=True), sc.mkdir(parents=True)
    gpd.GeoDataFrame(
        {"LSOA21CD": ["E01000001", "W01000001", "S01999999"]},
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(20, 0, 30, 10)],
        crs=27700,
    ).to_file(ew / "lsoa_boundaries.gpkg", driver="GPKG")
    gpd.GeoDataFrame(
        {"dzcode": ["S01000001"], "dzname": ["Culter - 01"]},
        geometry=[box(100, 0, 110, 10)],
        crs=27700,
    ).to_file(sc / "SG_DataZone_Bdry_2022.shp")
    # The shapefile's code column is found by name, case apart
    assert load_boundaries("sg_dz22_boundaries", code_column="DZCode").index.to_list() == [
        "S01000001"
    ]
    polys = tier_polygons("low", ("E", "S"))
    assert polys.index.to_list() == ["E01000001", "S01000001"]  # W and the stray S row dropped
    assert polys.crs.to_epsg() == 27700


def test_places_language_columns():
    from lix_pipeline.stage.geo import _name_in

    df = pl.DataFrame(
        {
            "NAME1": ["Caerdydd", "Achadh na Sine", "Leeds"],
            "NAME1_LANG": ["cym", "gla", None],
            "NAME2": ["Cardiff", "Achnasheen", None],
            "NAME2_LANG": ["eng", "eng", None],
        }
    )
    out = df.select(_name_in("cym").alias("cy"), _name_in("gla").alias("gd"))
    assert out["cy"].to_list() == ["Caerdydd", None, None]
    assert out["gd"].to_list() == [None, "Achadh na Sine", None]
