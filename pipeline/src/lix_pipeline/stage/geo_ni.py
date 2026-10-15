"""Northern Ireland's geography backbone: Super Data Zones 2021 → (themselves) → councils.

``build()`` is called by ``stage_geo_lsoa`` with ``("N",)`` and returns the same columns
as the other builders: ``lsoa21cd`` holds the Super Data Zone code and, because there
is no statistical tier between the zone and the council, ``msoa21cd`` holds it too (the
NSPL's pseudo code N99999999 is never used); ``lad_cd`` is the local government district.

Sources (config/nations.yaml ``geo:``): NISRA's Census 2021 lookups (zone → district
electoral area → council, and the urban, mixed or rural status of every zone), the
population-weighted centroids (Irish Grid, reprojected), the mid-year estimates (staged
by ``stage_population``), the NSPL for postcodes and the police force, the UK
local-authority file for council names, and Wikidata's Irish names of the councils.
"""

import fastexcel
import geopandas as gpd
import polars as pl

from lix_core.config import load_nations
from lix_core.geo import to_working, working_to_lonlat
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file
from lix_pipeline.geo.boundaries import bounds_table, load_boundaries
from lix_pipeline.geo.nspl import load_nspl
from lix_pipeline.stage.geo_ew import (
    _lsoa_pfa_from_nspl,
    nation_columns,
    region_columns,
    ruc_class_expr,
)

logger = setup_logging("stage.geo_ni")

Nations = tuple[str, ...]
LOOKUP_SHEET = "SDZ2021_lookup"
STATUS_SHEET = "SDZ21_Urban_mixed_rural_lookup"
DZ_SHEET = "DZ2021_lookup"


def _col(df: pl.DataFrame, name: str) -> str:
    """A column by case-insensitive name (NISRA's casing varies between sheets)."""
    match = next((c for c in df.columns if c.strip().lower() == name.lower()), None)
    if match is None:
        raise ValueError(f"No column {name!r} among {df.columns}")
    return match


def load_sheet(path, sheet: str, first_column: str) -> pl.DataFrame:
    """A NISRA workbook sheet with its header row found by ``first_column``."""
    reader = fastexcel.read_excel(path)
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=30).to_polars()
    header = next(
        (
            i
            for i, row in enumerate(probe.iter_rows())
            if any(str(v).strip().lower() == first_column.lower() for v in row if v is not None)
        ),
        0,
    )
    return reader.load_sheet_by_name(sheet, header_row=header).to_polars()


def tidy_lookup(sdz: pl.DataFrame, status: pl.DataFrame) -> pl.DataFrame:
    """The zone sheet and the urban/mixed/rural sheet → one row per Super Data Zone."""
    zones = sdz.select(
        pl.col(_col(sdz, "SDZ2021_code")).str.strip_chars().alias("lsoa21cd"),
        # NISRA writes "Botanic_H" (the district electoral area and a letter)
        pl.col(_col(sdz, "SDZ2021_name"))
        .str.strip_chars()
        .str.replace_all("_", " ")
        .alias("lsoa21nm"),
        pl.col(_col(sdz, "DEA2014_code")).str.strip_chars().alias("dea_cd"),
        pl.col(_col(sdz, "DEA2014_name")).str.strip_chars().alias("dea_nm"),
        pl.col(_col(sdz, "LGD2014_code")).str.strip_chars().alias("lad_cd"),
    ).unique("lsoa21cd")
    ruc = status.select(
        pl.col(_col(status, "SDZ2021_code")).str.strip_chars().alias("lsoa21cd"),
        pl.col(_col(status, "Urban_mixed_rural_status")).str.strip_chars().alias("ruc21cd"),
    ).unique("lsoa21cd")
    return zones.join(ruc, on="lsoa21cd", how="left").with_columns(
        pl.col("ruc21cd").alias("ruc21nm"),
        (pl.col("ruc21cd") == "Urban").alias("urban"),
    )


def read_lookup() -> pl.DataFrame:
    path = raw_file("nisra_dz21_lookup", "xlsx")
    return tidy_lookup(
        load_sheet(path, LOOKUP_SHEET, "SDZ2021_code"),
        load_sheet(path, STATUS_SHEET, "SDZ2021_code"),
    )


def read_dz_lookup() -> pl.DataFrame:
    """Data Zone → Super Data Zone, for the output-area lookup and the crosswalk."""
    df = load_sheet(raw_file("nisra_dz21_lookup", "xlsx"), DZ_SHEET, "DZ2021_code")
    return df.select(
        pl.col(_col(df, "DZ2021_code")).str.strip_chars().alias("oa21cd"),
        pl.col(_col(df, "SDZ2021_code")).str.strip_chars().alias("lsoa21cd"),
    ).unique("oa21cd")


def tidy_centroids(df: pl.DataFrame, input_crs: int) -> pl.DataFrame:
    """NISRA's centroid table (Irish Grid X/Y) → ``pwc_*`` in the working CRS and WGS84."""
    code = next(c for c in df.columns if c.strip().lower().endswith("_code"))
    x, y = to_working(
        df[_col(df, "X")].cast(pl.Float64).to_numpy(),
        df[_col(df, "Y")].cast(pl.Float64).to_numpy(),
        input_crs,
    )
    lon, lat = working_to_lonlat(x, y)
    return pl.DataFrame(
        {
            "lsoa21cd": df[code].cast(pl.Utf8).str.strip_chars().to_list(),
            "pwc_x": x,
            "pwc_y": y,
            "pwc_lon": lon,
            "pwc_lat": lat,
        }
    )


def read_centroids(input_crs: int, level: str = "super-data-zone") -> pl.DataFrame:
    folder = data_dir("raw") / "nisra_sdz21_centroids"
    files = sorted(folder.glob(f"*{level}.csv"))
    if not files:
        raise FileNotFoundError(f"No *{level}.csv under {folder}")
    return tidy_centroids(pl.read_csv(files[0], infer_schema_length=0), input_crs)


def build(nations: Nations) -> pl.DataFrame:
    """One row per Super Data Zone 2021 (``nations`` must be ``("N",)``)."""
    if tuple(nations) != ("N",):
        raise ValueError(f"geo_ni builds Northern Ireland, not {list(nations)}")
    spec = load_nations().nations["N"]
    geo = spec.geo

    zones = read_lookup()
    nspl = load_nspl(nations=nations, live_only=True)
    postcodes = nspl.group_by("lsoa21cd").agg(pl.len().alias("n_postcodes")).collect()

    lad = gpd.read_file(raw_file("lad_boundaries", "gpkg"), ignore_geometry=True)
    lad_code = next(
        c for c in lad.columns if c.upper().startswith("LAD") and c.upper().endswith("CD")
    )
    lad_names = pl.from_pandas(lad[[lad_code, lad_code[:-2] + "NM"]]).rename(
        {lad_code: "lad_cd", lad_code[:-2] + "NM": "lad_nm"}
    )
    irish = pl.read_csv(raw_file("council_names_ga", "csv"), infer_schema_length=0).select(
        pl.col("lad_cd"), pl.col("name_ga").alias("lad_nm_ga")
    )
    pop = pl.read_parquet(data_dir("staged") / "population.parquet").select(
        "lsoa21cd", "population", "pop_year"
    )
    polygons = load_boundaries(geo.low_boundaries, code_column=geo.code_column)

    df = (
        zones.join(lad_names, on="lad_cd", how="left")
        .join(irish, on="lad_cd", how="left")
        .with_columns(
            # The zone is its own mid tier
            pl.col("lsoa21cd").alias("msoa21cd"),
            pl.col("lsoa21nm").alias("msoa21nm"),
            pl.col("lsoa21nm").alias("msoa_name"),
            pl.col("lad_cd").alias("lad22cd"),
            pl.lit(None, pl.Utf8).alias("rgn_cd"),
            pl.lit(None, pl.Utf8).alias("rgn_nm"),
        )
        .join(_lsoa_pfa_from_nspl(nspl, nations), on="lsoa21cd", how="left")
        .join(read_centroids(spec.input_crs), on="lsoa21cd", how="left")
        .join(pop, on="lsoa21cd", how="left")
        .join(postcodes, on="lsoa21cd", how="left")
        .join(bounds_table(polygons), on="lsoa21cd", how="left")
        .with_columns(nation_columns(nations))
        .with_columns(region_columns(nations))
        .with_columns(
            ruc_class_expr(nations),
            (pl.col("population") / pl.col("area_km2")).alias("density_per_km2"),
            pl.col("n_postcodes").fill_null(0).cast(pl.Int32),
            pl.lit("sdz21").alias("area_type"),
        )
        .sort("lsoa21cd")
    )
    missing_names = df.filter(pl.col("lad_nm").is_null()).height
    if missing_names:
        logger.warning(f"{missing_names} Super Data Zones whose council is not in lad_boundaries")
    logger.info(f"geo_ni: {df.height:,} Super Data Zones")
    return df
