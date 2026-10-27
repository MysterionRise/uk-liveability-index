"""Ireland's backbone pieces: minted codes through the boundary loader, the Small Area
names and hierarchy, the census population, home points and the title-casing of Irish
and English names."""

from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.geo.boundaries import load_boundaries
from lix_pipeline.stage import geo_ie, homes
from lix_pipeline.stage.population import tidy_saps


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("CARLOW RURAL", "Carlow Rural"),
        ("O'BRIEN'S BRIDGE", "O'Brien's Bridge"),
        ("DUN LAOGHAIRE/RATHDOWN", "Dun Laoghaire/Rathdown"),
        ("BALLYMUN-ST. MARGARET'S", "Ballymun-St. Margaret's"),
        ("THE WARD", "The Ward"),
        ("CASTLE OF THE RIVER", "Castle of the River"),
        (None, None),
    ],
)
def test_english_title(raw, expected):
    assert geo_ie.english_title(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("CEATHARLACH (TUATH)", "Ceatharlach (Tuath)"),
        ("DÚN NA NGALL", "Dún na nGall"),
        ("NA HÁIRDE", "Na hÁirde"),
        ("BAILE AN MBÓTHAIR", "Baile an mBóthair"),
        ("CILL CHAINNIGH", "Cill Chainnigh"),
        ("AN TSRÁID", "An tSráid"),
        (None, None),
    ],
)
def test_irish_title(raw, expected):
    assert geo_ie.irish_title(raw) == expected


def test_area_suffix_shortens_split_and_merged_ids():
    assert geo_ie.area_suffix("017010016") == "016"
    assert geo_ie.area_suffix("017010012/01") == "012/01"
    assert geo_ie.area_suffix("017010029/017010046") == "029/046"
    assert geo_ie.area_suffix("017012004/02/017012002") == "004/02/002"


def _councils() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "county_code": ["01", "22", "23"],
            "county_english": ["CARLOW", "NORTH TIPPERARY", "SOUTH TIPPERARY"],
            "lad_cd": ["I06000001", "I06000027", "I06000027"],
            "lad_nm": ["Carlow", "Tipperary", "Tipperary"],
            "lad_nm_ga": ["Ceatharlach", "Tiobraid Árann", "Tiobraid Árann"],
            "te_english": [
                "CARLOW COUNTY COUNCIL", "TIPPERARY COUNTY COUNCIL", "TIPPERARY COUNTY COUNCIL",
            ],
            "province": ["Leinster", "Munster", "Munster"],
        }
    )  # fmt: skip


def test_tidy_small_areas_mints_codes_names_areas_and_folds_counties():
    sa = pl.DataFrame(
        {
            "SA_PUB2022": ["017010016", "017010012/01", "227001001"],
            "SA_GUID_2022": ["g1", "g2", "g3"],
            "ED_ID_STR": ["017010", "017010", "227001"],
            "ED_ENGLISH": ["CARLOW RURAL", "CARLOW RURAL", "NENAGH EAST URBAN"],
            "ED_GAEILGE": ["CEATHARLACH (TUATH)", "CEATHARLACH (TUATH)", None],
            "COUNTY_CODE": ["01", "01", "22"],
            "COUNTY_ENGLISH": ["CARLOW", "CARLOW", "NORTH TIPPERARY"],
            "CSO_LEA": ["TULLOW", "TULLOW", "NENAGH"],
            "SA_URBAN_AREA_NAME": [None, None, "Nenagh"],
        }
    )
    out = geo_ie.tidy_small_areas(sa, _councils()).sort("lsoa21cd")
    # Ranks follow the sorted ids: "017010012/01" sorts before "017010016"
    assert out["lsoa21cd"].to_list() == ["I01000001", "I01000002", "I01000003"]
    assert out["source_code"].to_list() == ["017010012/01", "017010016", "227001001"]
    assert out["lsoa21nm"].to_list() == [
        "Carlow Rural 012/01", "Carlow Rural 016", "Nenagh East Urban 001"
    ]  # fmt: skip
    assert out["lsoa21nm_ga"].to_list() == [
        "Ceatharlach (Tuath) 012/01",
        "Ceatharlach (Tuath) 016",
        None,
    ]
    assert out["msoa21cd"].to_list() == ["I02000001", "I02000001", "I02000002"]
    assert out["msoa_name_ga"][0] == "Ceatharlach (Tuath)"
    assert out["lad_cd"].to_list() == ["I06000001", "I06000001", "I06000027"]
    assert out["lad_nm_ga"][2] == "Tiobraid Árann"


def test_small_areas_of_a_merged_division_get_distinct_names():
    """Cong/Ross (067069) holds Small Areas 067069001 and 067193001, whose tails both read
    "001": the old divisions' digits tell them apart (#194)."""
    sa = pl.DataFrame(
        {
            "SA_PUB2022": ["067069001", "067193001", "067069002"],
            "SA_GUID_2022": ["g1", "g2", "g3"],
            "ED_ID_STR": ["067069", "067069", "067069"],
            "ED_ENGLISH": ["CONG/ROSS"] * 3,
            "ED_GAEILGE": ["CONGA/ROS"] * 3,
            "COUNTY_CODE": ["01"] * 3,
            "COUNTY_ENGLISH": ["CARLOW"] * 3,
            "CSO_LEA": ["TULLOW"] * 3,
            "SA_URBAN_AREA_NAME": [None] * 3,
        }
    )
    out = geo_ie.tidy_small_areas(sa, _councils()).sort("source_code")
    assert out["lsoa21nm"].to_list() == [
        "Cong/Ross 069-001", "Cong/Ross 002", "Cong/Ross 193-001"
    ]  # fmt: skip
    assert out["lsoa21nm_ga"].to_list() == [
        "Conga/Ros 069-001", "Conga/Ros 002", "Conga/Ros 193-001"
    ]  # fmt: skip
    assert out["msoa21cd"].n_unique() == 1


def test_tidy_small_areas_refuses_a_county_the_list_lacks():
    sa = pl.DataFrame(
        {
            "SA_PUB2022": ["999001001"], "SA_GUID_2022": ["g"], "ED_ID_STR": ["999001"],
            "ED_ENGLISH": ["X"], "ED_GAEILGE": [None], "COUNTY_CODE": ["99"],
            "COUNTY_ENGLISH": ["NOWHERE"], "CSO_LEA": ["X"], "SA_URBAN_AREA_NAME": [None],
        }
    )  # fmt: skip
    with pytest.raises(ValueError, match="99"):
        geo_ie.tidy_small_areas(sa, _councils())


def test_tidy_saps_drops_the_state_row_and_sums_the_bands():
    cols = {f"T1_1AGE{i}T": ["10", "5", "100"] for i in range(16)}
    older = ["T1_1AGE65_69T", "T1_1AGE70_74T", "T1_1AGE75_79T", "T1_1AGE80_84T", "T1_1AGEGE_85T"]
    cols.update({c: ["2", "1", "20"] for c in older})
    df = pl.DataFrame(
        {
            "GEOGID": ["017010016", "017010012/01", "State"],
            "UR_Category": ["4", "1", None],
            "T1_1AGETT": ["419", "345", "5149139"],
            **cols,
        }
    )
    out = tidy_saps(df).sort("lsoa21cd")
    assert out["lsoa21cd"].to_list() == ["I01000001", "I01000002"]
    assert out["population"].to_list() == [345, 419]
    assert out["pop_children_0_15"].to_list() == [80, 160]
    assert out["pop_65_plus"].to_list() == [5, 10]
    assert out["pop_year"].to_list() == [2022, 2022]


def test_load_boundaries_mints_codes_and_dissolves_parts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    folder = tmp_path / "raw" / "parts"
    folder.mkdir(parents=True)
    gdf = gpd.GeoDataFrame(
        {"ED_ID_STR": ["027058/027085", "017010", "027058/027085"]},
        geometry=[box(0, 0, 1, 1), box(2, 0, 3, 1), box(1, 0, 2, 1)],
        crs="EPSG:27700",
    )
    gdf.to_file(folder / "parts.gpkg", driver="GPKG")
    out = load_boundaries("parts", code_column="ED_ID_STR", recode=("I", "mid"))
    assert out.index.to_list() == ["I02000001", "I02000002"]
    # The two parts of the amalgamated division became one polygon
    assert out.loc["I02000002"].geometry.area == pytest.approx(2.0)


def test_home_centroids_are_the_mean_of_the_points():
    pts = pl.DataFrame(
        {
            "lsoa21cd": ["I01000001", "I01000001", "I01000002"],
            "east1m": [100.0, 300.0, 50.0],
            "north1m": [200.0, 400.0, 60.0],
        }
    )
    out = geo_ie.home_centroids(pts).sort("lsoa21cd")
    assert out["pwc_x"].to_list() == [200.0, 50.0] and out["pwc_y"].to_list() == [300.0, 60.0]
    assert out.columns == ["lsoa21cd", "pwc_x", "pwc_y", "pwc_lon", "pwc_lat"]


def test_postcode_homes_keep_live_small_users_of_the_nations(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    staged = tmp_path / "staged"
    staged.mkdir(parents=True)
    pl.DataFrame(
        {
            "postcode": ["LS6 3HN", "LS6 3HP", "CF10 1AA", "BT9 6AB"],
            "live": [True, False, True, True],
            "usrtypind": ["0", "0", "1", "0"],
            "ctry_cd": ["E92000001", "E92000001", "W92000004", "N92000002"],
            "lsoa21cd": ["E01000001", "E01000001", "W01001880", "N21000258"],
            "east1m": [1, 2, 3, 4],
            "north1m": [5, 6, 7, 8],
            "lat": [53.8, 53.8, 51.5, 54.6],
            "long": [-1.5, -1.5, -3.2, -5.9],
        }
    ).write_parquet(staged / "nspl.parquet")
    out = homes.postcode_homes(("E", "N")).sort("home_id")
    assert out["home_id"].to_list() == ["BT9 6AB", "LS6 3HN"]
    assert out["nation"].to_list() == ["N", "E"] and set(out["kind"]) == {"postcode"}
    assert out.columns == [
        "home_id",
        "lsoa21cd",
        "east1m",
        "north1m",
        "lat",
        "long",
        "nation",
        "kind",
    ]


def test_tidy_comreg_broadcasts_a_division_to_its_areas_once():
    from lix_pipeline.stage.transport import tidy_comreg

    comreg = pl.DataFrame(
        {
            "ED_ID_STR": ["017010", "027058/027085", "027058/027085"],
            "Gigabit_Passed_PCT_Public_View": [91, 20, 40],
            "Gigabit_Active_PCT_Public_View": [48, 10, 10],
        }
    )
    divisions = pl.DataFrame(
        {
            "lsoa21cd": ["I01000001", "I01000002", "I01000003"],
            "ED_ID_STR": ["017010", "017010", "027058/027085"],
        }
    )
    out = tidy_comreg(comreg, divisions)
    assert out["lsoa21cd"].to_list() == ["I01000001", "I01000002", "I01000003"]
    assert out["gigabit_pct"].to_list() == [91.0, 91.0, 30.0]  # the two parts averaged
    assert out["premises"].null_count() == 3


def test_tidy_pobal_pads_division_ids_and_broadcasts_to_small_areas():
    from lix_pipeline.stage.deprivation import tidy_pobal

    pobal = pl.DataFrame(
        {
            "ED_ID_STR": ["17001", "27058/27085"],
            "Index22_ED_std_rel_wt": ["-12.5", "8.25"],
            "Index22_ED_std_abs_wt": ["-10", "7"],
            "UNEMPM22": ["20", "6"],
            "UNEMPF22": ["10", "4"],
        }
    )
    divisions = pl.DataFrame(
        {
            "lsoa21cd": ["I01000001", "I01000002", "I01000003"],
            "ED_ID_STR": ["017001", "017001", "027058/027085"],
        }
    )
    out = tidy_pobal(pobal, divisions).sort("lsoa21cd")
    assert out["pobal_index"].to_list() == [-12.5, -12.5, 8.25]
    assert out["imd_score"].to_list() == [12.5, 12.5, -8.25]  # higher = more deprived
    assert out["employment_score"].to_list() == [0.15, 0.15, 0.05]
    assert out["imd_rank"].to_list() == [1, 2, 3] and out["income_score"].null_count() == 3


def test_sample_raster_at_homes_reads_the_cell_under_each_home(tmp_path):
    """A 3 × 3 raster on the European grid; homes in the working CRS land in its cells."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from lix_core.geo import from_working, to_working
    from lix_pipeline.stage.pcm import sample_raster_at_homes

    # Three 1 km cells across by three down, placed over Dublin on EPSG:3035
    x0, y0 = from_working(np.array([315_000.0]), np.array([234_000.0]), 3035)
    origin_x, origin_y = float(x0[0]) // 1000 * 1000, float(y0[0]) // 1000 * 1000 + 3000
    band = np.array([[1.0, 2.0, 3.0], [4.0, -9999.0, 6.0], [7.0, 8.0, 9.0]], dtype="float32")
    path = tmp_path / "grid.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=3, width=3, count=1, dtype="float32", crs="EPSG:3035",
        transform=from_origin(origin_x, origin_y, 1000, 1000), nodata=-9999.0,
    ) as dst:  # fmt: skip
        dst.write(band, 1)
    # Cell centres (col, row) → the working CRS
    centres_x = np.array(
        [origin_x + 500, origin_x + 1500, origin_x + 2500, origin_x + 1500, origin_x - 5000]
    )
    centres_y = np.array(
        [origin_y - 500, origin_y - 500, origin_y - 2500, origin_y - 1500, origin_y - 500]
    )
    east, north = to_working(centres_x, centres_y, 3035)
    homes = pl.DataFrame(
        {"lsoa21cd": ["A", "A", "B", "B", "C"], "east1m": east, "north1m": north}
    )  # fmt: skip
    out = sample_raster_at_homes(path, homes)
    assert out["lsoa21cd"].to_list() == ["A", "B"]  # C is outside the raster
    assert out["value"].to_list() == [1.5, 9.0]  # B's second home sits on the no-data cell
    assert out["n_points"].to_list() == [2, 1]
