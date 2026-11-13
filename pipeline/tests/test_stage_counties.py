"""Each British council's ceremonial county (#233): the share of its residents in each
county, and the rules that turn shares into the name people say."""

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.stage.counties import council_counties, county_shares


def _counties() -> gpd.GeoDataFrame:
    # Two counties side by side, the border at x = 100
    return gpd.GeoDataFrame(
        {"Name": ["Devon", "Durham"]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100)],
        crs=27700,
    )


def test_a_councils_share_is_its_residents_in_each_county():
    areas = pl.DataFrame({
        "lad_cd": ["E07000001", "E07000001", "E06000004", "E06000004", "E06000004"],
        "x": [10.0, 50.0, 150.0, 160.0, 40.0],
        "y": [10.0, 50.0, 50.0, 50.0, 50.0],
        "population": [1000, 3000, 4000, 3000, 3000],
    })  # fmt: skip
    shares = county_shares(areas, _counties()).sort("lad_cd", "county")
    assert shares.rows() == [
        ("E06000004", "Devon", pytest.approx(0.3)),
        ("E06000004", "Durham", pytest.approx(0.7)),
        ("E07000001", "Devon", pytest.approx(1.0)),
    ]


def test_a_centre_just_offshore_takes_the_nearest_county():
    areas = pl.DataFrame({"lad_cd": ["E07000001"], "x": [-5.0], "y": [50.0], "population": [10]})
    assert county_shares(areas, _counties()).rows() == [("E07000001", "Devon", 1.0)]


def _shares(rows: list[tuple[str, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["lad_cd", "county", "share"], orient="row")


def _names(names: dict[str, str]) -> pl.DataFrame:
    return pl.DataFrame({"lad_cd": list(names), "lad_nm": list(names.values())})


def test_a_council_takes_the_county_that_holds_two_thirds_of_it():
    shares = _shares([
        ("E06000004", "Durham", 0.70), ("E06000004", "North Yorkshire", 0.30),
        ("S12000017", "Inverness", 0.50), ("S12000017", "Ross and Cromarty", 0.30),
        ("E07000040", "Devon", 1.0),
    ])  # fmt: skip
    names = _names(
        {"E06000004": "Stockton-on-Tees", "S12000017": "Highland", "E07000040": "East Devon"}
    )
    out = council_counties(shares, names)
    # Highland is not "Inverness": a lieutenancy that names a part of a council names none
    assert out.select("lad_cd", "county").rows() == [
        ("E06000004", "County Durham"),
        ("E07000040", "Devon"),
    ]


def test_the_layers_names_become_the_ones_people_say():
    shares = _shares([
        ("E09000001", "City and County of the City of London", 1.0),
        ("E08000021", "Tyne & Wear", 1.0),
        ("S12000005", "Clackmannan", 1.0),
    ])  # fmt: skip
    names = _names(
        {
            "E09000001": "City of London",
            "E08000021": "Newcastle upon Tyne",
            "S12000005": "Clackmannanshire",
        }
    )
    assert council_counties(shares, names)["county"].to_list() == [
        "Tyne and Wear",
        "Greater London",
        "Clackmannanshire",
    ]


def test_outside_england_a_county_named_like_a_council_is_that_council():
    shares = _shares([
        # Somerset is still Bath to the people who live there
        ("E06000022", "Somerset", 1.0), ("E06000066", "Somerset", 1.0),
        # Nobody on Anglesey lives in Gwynedd; Gwent has no council of its name
        ("W06000001", "Gwynedd", 1.0), ("W06000002", "Gwynedd", 1.0),
        ("W06000019", "Gwent", 1.0), ("W06000022", "Gwent", 1.0),
        ("S12000038", "Renfrewshire", 1.0), ("S12000018", "Renfrewshire", 1.0),
    ])  # fmt: skip
    names = _names(
        {
            "E06000022": "Bath and North East Somerset",
            "E06000066": "Somerset",
            "W06000001": "Isle of Anglesey",
            "W06000002": "Gwynedd",
            "W06000019": "Blaenau Gwent",
            "W06000022": "Newport",
            "S12000038": "Renfrewshire",
            "S12000018": "Inverclyde",
        }
    )
    out = dict(council_counties(shares, names).select("lad_cd", "county").rows())
    assert out == {
        "E06000022": "Somerset",
        "E06000066": "Somerset",
        "W06000002": "Gwynedd",
        "W06000019": "Gwent",
        "W06000022": "Gwent",
        "S12000038": "Renfrewshire",
    }
