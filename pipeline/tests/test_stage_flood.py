"""Tests for the flood concept table: the English join and the Welsh estimate from NRW areas."""

from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.stage.environment import stage_flood


@pytest.fixture
def data(tmp_path: Path, monkeypatch) -> Path:
    """England and Wales active; a staged NSPL, VOA stock and English flood tables."""
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIX_NATIONS", "E,W")
    staged = tmp_path / "staged"
    staged.mkdir()
    # Two Welsh postcodes in W01000001: one inside a surface-water area, one outside
    pl.DataFrame(
        {
            "postcode": ["AB1 2CD", "CF10 1AA", "CF10 1AB"],
            "postcode_norm": ["AB12CD", "CF101AA", "CF101AB"],
            "east1m": [530000, 318000, 318500],
            "north1m": [180000, 176000, 176500],
            "lsoa21cd": ["E01000001", "W01000001", "W01000001"],
            "ctry_cd": ["E92000001", "W92000004", "W92000004"],
            "usrtypind": ["0", "0", "0"],
            "live": [True, True, True],
        }
    ).write_parquet(staged / "nspl.parquet")
    pl.DataFrame({"lsoa21cd": ["E01000001", "W01000001"], "dwellings": [100, 40]}).write_parquet(
        staged / "voa_ctsop.parquet"
    )
    pl.DataFrame(
        {
            # The English file also lists a Welsh LSOA through a postcode straddling the border
            "lsoa21cd": ["E01000001", "W01000001"],
            "res_high": [5, 1],
            "res_medium": [3, 0],
            "res_low": [2, 0],
            "res_verylow": [0, 0],
        }
    ).write_parquet(staged / "ea_flood_postcodes.parquet")
    pl.DataFrame(
        {
            "lsoa21cd": ["E01000001"],
            "any_high": [12],
            "any_medium": [8],
            "any_low": [4],
            "share_groundwater": [0.0],
        }
    ).write_parquet(staged / "ea_flood_all_postcodes.parquet")
    # NRW layers: rivers and sea cover nothing; surface water covers CF10 1AA at high risk
    far_away = ("Low", box(0, 0, 1, 1))
    for slug, (risk, poly) in {
        "nrw_fraw_rivers": far_away,
        "nrw_fraw_sea": far_away,
        "nrw_fraw_surface_water": ("High", box(317900, 175900, 318100, 176100)),
    }.items():
        gdf = gpd.GeoDataFrame({"risk": [risk]}, geometry=[poly], crs=27700)
        out = tmp_path / "raw" / slug / f"{slug}.gpkg"
        out.parent.mkdir(parents=True)
        gdf.to_file(out, driver="GPKG")
    return tmp_path


def test_flood_unions_england_counts_and_estimates_wales(data):
    df = stage_flood().collect().sort("lsoa21cd")
    assert df["lsoa21cd"].to_list() == ["E01000001", "W01000001"]  # one row per LSOA
    england = df.row(0, named=True)
    assert england["lsoa21cd"] == "E01000001"
    assert (
        england["res_high"],
        england["res_medium"],
        england["any_high"],
        england["any_medium"],
    ) == (5, 3, 12, 8)
    assert england["estimated"] is False
    wales = df.row(1, named=True)
    # Half the LSOA's postcodes sit in the high surface-water area: 20 of 40 homes
    assert wales["lsoa21cd"] == "W01000001"
    assert (wales["res_high"], wales["res_medium"], wales["any_high"], wales["any_medium"]) == (
        0,
        0,
        20,
        0,
    )
    assert wales["estimated"] is True
