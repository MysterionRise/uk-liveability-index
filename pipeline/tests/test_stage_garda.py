"""Ireland's recorded crime: station names to sub-districts, areas to sub-districts and
the spreading of a station's counts over its areas by population."""

import geopandas as gpd
import polars as pl
from shapely.geometry import box

from lix_pipeline.stage import garda


def _subdistricts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "SUB_CODE": ["4305B", "6210A", "3110B", "2203C"],
            "SUB_DIST": ["Kinsale", "Bridewell", "Bridewell", "Corofin"],
            "SUB_IRISH": [
                "Cionn tSáile",
                "Stáisiún na Smachtlainne",
                "Stáisiún na Smachtlainne",
                "Cora Finne",
            ],
            "COUNTY_1": ["Cork", "Dublin", "Cork", "Clare"],
        }
    )  # fmt: skip


def test_station_counts_keep_the_latest_year_and_map_the_groups():
    cja = pl.DataFrame(
        {
            "Garda Station": ["Kinsale, Cork West Division (31201)"] * 4,
            "C03037V05454": ["31201"] * 4,
            "Year": ["2024", "2025", "2025", "2025"],
            "Type of Offence": [
                "Burglary and related offences (07)",
                "Burglary and related offences (07)",
                "Theft and related offences (08)",
                "Homicide offences (01)",
            ],
            "VALUE": ["9", "12", "30", "1"],
        }
    )
    out, year = garda.station_counts(cja)
    assert year == 2025
    assert out.select("crime_type", "n").rows() == [
        ("Burglary", 12.0),
        ("Theft from the person", 30.0),
    ]


def test_station_flags_find_a_station_that_stopped_reporting_and_tiny_ones():
    """Laytown recorded 906 incidents in 2024 and 0 in every group in 2025: not a
    crime-free coast but a station that stopped reporting (#143)."""
    rows = []
    for station, by_year in {
        "Drogheda, Louth Division (42101)": {"2024": 3800, "2025": 3790},
        "Laytown, Louth Division (42105)": {"2024": 906, "2025": 0},
        "Baltimore, Cork West Division (31207)": {"2024": 6, "2025": 4},
        "Newtown, Galway Division (99998)": {"2025": 0},
    }.items():
        for year, n in by_year.items():
            rows.append((station, year, "Burglary and related offences (07)", str(n)))
            rows.append((station, year, "Homicide offences (01)", "1"))
    cja = pl.DataFrame(
        rows, schema=["Garda Station", "Year", "Type of Offence", "VALUE"], orient="row"
    )
    cja = cja.with_columns(pl.lit("x").alias("C03037V05454"))
    non_reporting, low_n = garda.station_flags(cja, 2025)
    assert non_reporting == {"Laytown, Louth Division (42105)"}
    # A tiny station and a new one with nothing yet are low on counts, not non-reporting
    assert low_n == {
        "Baltimore, Cork West Division (31207)", "Newtown, Galway Division (99998)",
    }  # fmt: skip


def test_match_stations_by_name_irish_name_alias_and_county():
    aliases = pl.DataFrame(
        {
            "station": ["Bridewell Cork", "Schull"],
            "sub_district": ["Bridewell", "Skull"],
            "county": ["Cork", "Cork"],
        }
    )
    labels = [
        "Kinsale, Cork West Division (31201)",
        "Cionn tSáile, Cork West Division (31201)",
        "Bridewell Cork, Cork City Division (31105)",
        "Bridewell, D.M.R. North Central Division (62101)",
        "Corofin Co. Clare, Clare/Tipperary Division (21203)",
        "Schull, Cork West Division (31301)",
        "Nowhere, Galway Division (99999)",
    ]
    out = garda.match_stations(labels, _subdistricts(), aliases)
    assert [out[label] for label in labels] == [
        "4305B",
        "4305B",
        "3110B",
        "6210A",
        None,
        None,
        None,
    ]


def test_spread_to_areas_by_population():
    counts = pl.DataFrame(
        {"sub_code": ["A", "A"], "crime_type": ["Burglary", "Robbery"], "n": [10.0, 4.0]}
    )
    areas = pl.DataFrame(
        {
            "lsoa21cd": ["I01000001", "I01000002", "I01000003"],
            "population": [300, 100, 500],
            "sub_code": ["A", "A", "B"],
        }
    )
    out = garda.spread_to_areas(counts, areas, low={"A"})
    assert out.filter(pl.col("crime_type") == "Burglary")["n"].to_list() == [7.5, 2.5]
    assert set(out["force"]) == {garda.FORCE} and set(out["force_months"]) == {12}
    assert out["low_n"].all()  # every area of a low-count sub-district carries the flag
    assert "I01000003" not in out["lsoa21cd"].to_list()  # its sub-district has no counts


def test_areas_to_subdistricts_uses_the_polygon_then_the_nearest_served_one():
    polygons = gpd.GeoDataFrame(
        {"SUB_CODE": ["A", "B", "C"]},
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(40, 0, 50, 10)],
        crs="EPSG:27700",
    )
    areas = pl.DataFrame(
        {
            "lsoa21cd": ["a1", "b1", "c1", "far"],
            "pwc_x": [5.0, 15.0, 45.0, 30.0],
            "pwc_y": [5.0, 5.0, 5.0, 30.0],
        }
    )
    out = garda.areas_to_subdistricts(areas, polygons, served={"A", "C"})
    got = dict(out.rows())
    assert got["a1"] == "A" and got["c1"] == "C"
    assert got["b1"] == "A"  # B has no reporting station: the nearest served one
    assert got["far"] in {"A", "B", "C"}  # outside every polygon: the nearest, then remapped
