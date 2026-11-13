"""Each British small area's ceremonial county (#233): the county its centre lies in,
under the name people say."""

import geopandas as gpd
import polars as pl
from shapely.geometry import box

from lix_pipeline.stage.counties import area_counties


def _counties() -> gpd.GeoDataFrame:
    # Three counties in a row, the borders at x = 100 and x = 200
    return gpd.GeoDataFrame(
        {"Name": ["Durham", "North Yorkshire", "Tyne & Wear"]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100), box(200, 0, 300, 100)],
        crs=27700,
    )


def _areas(rows: list[tuple[str, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["lsoa21cd", "x", "y"], orient="row")


def test_a_small_area_takes_the_county_its_centre_lies_in():
    # One council the border divides, as the Tees divides Stockton-on-Tees: its areas go
    # each to their own county (a first version gave the whole council the county of two
    # thirds of its residents, and Yarm was in County Durham)
    out = area_counties(
        _areas([("E01000001", 40.0, 50.0), ("E01000002", 60.0, 50.0), ("E01000003", 150.0, 50.0)]),
        _counties(),
    )
    assert out.rows() == [
        ("E01000001", "County Durham"),
        ("E01000002", "County Durham"),
        ("E01000003", "North Yorkshire"),
    ]


def test_the_layers_names_become_the_ones_people_say():
    counties = gpd.GeoDataFrame(
        {"Name": ["City and County of the City of London", "Tyne & Wear", "Clackmannan"]},
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(20, 0, 30, 10)],
        crs=27700,
    )
    out = area_counties(
        _areas([("E01000001", 5.0, 5.0), ("E01000002", 15.0, 5.0), ("S01000001", 25.0, 5.0)]),
        counties,
    )
    assert out["county"].to_list() == ["Greater London", "Tyne and Wear", "Clackmannanshire"]


def test_a_centre_just_offshore_takes_the_nearest_county():
    out = area_counties(
        _areas([("E01000001", -5.0, 50.0), ("E01000002", 305.0, 50.0)]), _counties()
    )
    assert out["county"].to_list() == ["County Durham", "Tyne and Wear"]


def test_a_centre_on_a_border_gets_one_county():
    out = area_counties(_areas([("E01000001", 100.0, 50.0)]), _counties())
    assert out.height == 1
