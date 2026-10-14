"""Northern Ireland's backbone pieces: the NISRA lookups, Irish Grid centroids, the
mid-year estimates, OpenStreetMap places and the search table for a shared tier."""

import polars as pl
import pytest

from lix_pipeline.serve.lookups import build_areas
from lix_pipeline.stage.geo_ni import tidy_centroids, tidy_lookup
from lix_pipeline.stage.population import tidy_mye


def test_tidy_lookup_joins_the_zone_sheet_and_the_status_sheet():
    sdz = pl.DataFrame(
        {
            "SDZ2021_code": ["N21000001", "N21000002"],
            "SDZ2021_name": ["Dunsilly_A", "Dunsilly_B"],
            "DEA2014_code": ["N10000104", "N10000104"],
            "DEA2014_name": ["Dunsilly", "Dunsilly"],
            "LGD2014_code": ["N09000001", "N09000001"],
            "LGD2014_name": ["Antrim and Newtownabbey"] * 2,
        }
    )
    status = pl.DataFrame(
        {
            "sdz2021_code": ["N21000001", "N21000002"],
            "SDZ2021_name": ["Dunsilly_A", "Dunsilly_B"],
            "Urban_status": ["Urban", "Rural"],
            "Urban_mixed_rural_status": ["Urban", "Mixed"],
        }
    )
    out = tidy_lookup(sdz, status).sort("lsoa21cd")
    assert out.columns == [
        "lsoa21cd", "lsoa21nm", "dea_cd", "dea_nm", "lad_cd", "ruc21cd", "ruc21nm", "urban"
    ]  # fmt: skip
    assert out["ruc21cd"].to_list() == ["Urban", "Mixed"]
    assert out["urban"].to_list() == [True, False]
    assert out["lad_cd"].to_list() == ["N09000001"] * 2


def test_tidy_centroids_reprojects_irish_grid_to_the_working_crs():
    df = pl.DataFrame(
        {
            "SDZ2021_code": ["N21000258"],
            "SDZ2021_name": ["Malone_A"],
            "X": ["333230"],
            "Y": ["372800"],
        }
    )
    out = tidy_centroids(df, 29902)
    row = out.row(0, named=True)
    assert row["lsoa21cd"] == "N21000258"
    assert 100_000 < row["pwc_x"] < 200_000 and 500_000 < row["pwc_y"] < 560_000
    assert -6.1 < row["pwc_lon"] < -5.8 and 54.5 < row["pwc_lat"] < 54.7  # Belfast


def test_tidy_mye_keeps_the_latest_year_and_the_broad_bands():
    rows = []
    for year in (2023, 2024):
        for sex in ("All Persons", "Females"):
            for age, n in (
                ("All ages", 1000 + year - 2023),
                ("00-15", 200),
                ("16-39", 300),
                ("65+", 150),
            ):
                rows.append(("Super Data Zones", "N21000001", "Dunsilly_A", year, sex, age, n))
    rows.append(
        ("Local Government Districts", "N09000001", "Antrim", 2024, "All Persons", "All ages", 1)
    )
    flat = pl.DataFrame(
        rows, schema=["Area", "Area_code", "Area_name", "Year", "Sex", "Age", "MYE"], orient="row"
    )
    out = tidy_mye(flat)
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["population"] == 1001 and row["pop_year"] == 2024
    assert row["pop_children_0_15"] == 200 and row["pop_65_plus"] == 150
    assert row["median_age"] is None


def test_build_areas_skips_the_duplicate_mid_row_of_a_shared_tier():
    features = pl.DataFrame(
        {
            "lsoa21cd": ["E01000001", "N21000001"],
            "msoa21cd": ["E02000001", "N21000001"],
            "msoa_name": ["City of London 001", "Dunsilly_A"],
            "lad_cd": ["E09000001", "N09000001"],
            "lad_nm": ["City of London", "Antrim and Newtownabbey"],
            "rgn_cd": ["E12000007", "N92000002"],
            "rgn_nm": ["London", "Northern Ireland"],
            "ctry_cd": ["E92000001", "N92000002"],
            "nation": ["E", "N"],
            "population": [1000, 2000],
            "bbox_w": [-0.1, -6.3],
            "bbox_s": [51.5, 54.6],
            "bbox_e": [-0.09, -6.2],
            "bbox_n": [51.51, 54.7],
        }
    )
    areas = build_areas(features)
    msoa_rows = areas.filter(pl.col("level") == "msoa")
    assert msoa_rows["code"].to_list() == ["E02000001"]  # no msoa row for the Super Data Zone
    assert "N09000001" in areas.filter(pl.col("level") == "lad")["code"].to_list()
    assert "N92000002" in areas.filter(pl.col("level") == "nation")["code"].to_list()


@pytest.mark.parametrize(
    "value, kind", [("town", "Town"), ("suburb", "Suburban Area"), ("isolated_dwelling", None)]
)
def test_osm_place_types_follow_os_open_names(value, kind):
    from lix_pipeline.stage.places_osm import PLACE_TYPES

    assert PLACE_TYPES.get(value) == kind


def test_places_from_pbf_reads_named_place_nodes(tmp_path):
    import osmium

    from lix_pipeline.stage.places_osm import places_from_pbf

    path = tmp_path / "ni.osm.pbf"
    writer = osmium.SimpleWriter(str(path))
    writer.add_node(
        osmium.osm.mutable.Node(
            id=1,
            location=(-5.93, 54.597),
            tags={"place": "city", "name": "Belfast", "name:ga": "Béal Feirste"},
        )
    )
    writer.add_node(
        osmium.osm.mutable.Node(id=2, location=(-6.0, 54.6), tags={"place": "farm", "name": "x"})
    )
    writer.add_node(osmium.osm.mutable.Node(id=3, location=(-6.1, 54.7), tags={"place": "village"}))
    writer.close()
    df = places_from_pbf(path)
    assert df.height == 1
    row = df.row(0, named=True)
    assert (
        row["name"] == "Belfast"
        and row["name_ga"] == "Béal Feirste"
        and row["place_type"] == "City"
    )
