"""Scotland's geography backbone: Data Zones 2022 → Intermediate Zones → council areas.

``build()`` is called by ``stage_geo_lsoa`` with ``("S",)`` and returns the same columns
as the England and Wales builder (``geo_ew``), so Scottish rows look like every other
row to the indicators: ``lsoa21cd`` holds the Data Zone code, ``msoa21cd`` the
Intermediate Zone, ``lad_cd`` the council area.

Sources (config/nations.yaml ``geo:``): the Data Zone lookup (zone → Intermediate Zone →
council, urban rural classification), the zone and Intermediate Zone names from the NSPL
documents, NRS's population estimates (staged by ``stage_population``), the Scottish
Government's population-weighted centroids and boundaries, the NSPL for postcodes and the
police force, and the UK local-authority file for council names.
"""

import geopandas as gpd
import polars as pl

from lix_core.config import load_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file
from lix_pipeline.geo.boundaries import bounds_table, load_boundaries
from lix_pipeline.geo.crs import bng_to_lonlat
from lix_pipeline.geo.nspl import load_nspl, nspl_document
from lix_pipeline.stage.geo_ew import (
    _lsoa_pfa_from_nspl,
    nation_columns,
    region_columns,
    ruc_class_expr,
)

logger = setup_logging("stage.geo_scotland")

Nations = tuple[str, ...]


def _column(df: pl.DataFrame, prefix: str, suffix: str) -> str:
    """The lookup's column for a geography, e.g. ``LA_Code`` from ("LA", "Code")."""
    name = next((c for c in df.columns if c.upper() == f"{prefix}_{suffix}".upper()), None)
    if name is None:
        raise ValueError(f"Data Zone lookup lacks {prefix}_{suffix}; has {df.columns}")
    return name


def read_lookup() -> pl.DataFrame:
    """The Data Zone 2022 lookup: zone, Intermediate Zone, council and the urban rural
    classification (2-fold for urban/rural, 6-fold for the classes)."""
    raw = pl.read_csv(
        raw_file("sg_dz22_lookup", "csv"), infer_schema_length=0, encoding="utf8-lossy"
    )
    return raw.select(
        pl.col(_column(raw, "DZ22", "Code")).alias("lsoa21cd"),
        pl.col(_column(raw, "DZ22", "Name")).alias("lsoa21nm"),
        pl.col(_column(raw, "IZ22", "Code")).alias("msoa21cd"),
        pl.col(_column(raw, "IZ22", "Name")).alias("msoa21nm"),
        pl.col(_column(raw, "LA", "Code")).alias("lad_cd"),
        pl.col(_column(raw, "UR6", "Code")).alias("ruc21cd"),
        pl.col(_column(raw, "UR6", "Name")).alias("ruc21nm"),
        (pl.col(_column(raw, "UR2", "Code")) == "1").alias("urban"),
    ).unique("lsoa21cd")


def read_centroids() -> pl.DataFrame:
    """Population-weighted centroids (British National Grid and WGS84)."""
    folder = data_dir("raw") / "sg_dz22_centroids"
    shp = sorted(folder.glob("*.shp"))
    if not shp:
        raise FileNotFoundError(f"No centroid shapefile under {folder}")
    gdf = gpd.read_file(shp[0])
    code = next(c for c in gdf.columns if c.lower() in ("dzcode", "dz22_code", "datazone"))
    df = pl.DataFrame(
        {
            "lsoa21cd": gdf[code].astype(str).to_list(),
            "pwc_x": gdf.geometry.x.to_numpy(),
            "pwc_y": gdf.geometry.y.to_numpy(),
        }
    )
    lon, lat = bng_to_lonlat(df["pwc_x"], df["pwc_y"])
    return df.with_columns(pwc_lon=lon, pwc_lat=lat)


def build(nations: Nations) -> pl.DataFrame:
    """One row per Data Zone 2022 (``nations`` must be ``("S",)``)."""
    if tuple(nations) != ("S",):
        raise ValueError(f"geo_scotland builds Scotland, not {list(nations)}")
    geo = load_nations().nations["S"].geo

    zones = read_lookup()
    # The NSPL's own name documents agree with the lookup; the lookup wins where both exist
    dz_names = pl.read_csv(nspl_document("LSOA*DZ names and codes SC*.csv"), encoding="utf8-lossy")
    dz_names = dz_names.select(
        pl.col(dz_names.columns[0]).alias("lsoa21cd"), pl.col(dz_names.columns[1]).alias("nm")
    )
    zones = (
        zones.join(dz_names, on="lsoa21cd", how="left")
        .with_columns(pl.coalesce("lsoa21nm", "nm").alias("lsoa21nm"))
        .drop("nm")
    )

    nspl = load_nspl(nations=nations, live_only=True)
    postcodes = nspl.group_by("lsoa21cd").agg(pl.len().alias("n_postcodes")).collect()

    lad = gpd.read_file(raw_file("lad_boundaries", "gpkg"), ignore_geometry=True)
    lad_code = next(
        c for c in lad.columns if c.upper().startswith("LAD") and c.upper().endswith("CD")
    )
    lad_names = pl.from_pandas(lad[[lad_code, lad_code[:-2] + "NM"]]).rename(
        {lad_code: "lad_cd", lad_code[:-2] + "NM": "lad_nm"}
    )

    gaelic = pl.read_csv(raw_file("council_names_gd", "csv"), infer_schema_length=0).select(
        pl.col("lad_cd"), pl.col("name_gd").alias("lad_nm_gd")
    )
    pop = pl.read_parquet(data_dir("staged") / "population.parquet").select(
        "lsoa21cd", "population", "pop_year"
    )
    polygons = load_boundaries(geo.low_boundaries, code_column=geo.code_column)

    df = (
        zones.join(lad_names, on="lad_cd", how="left")
        .join(gaelic, on="lad_cd", how="left")
        .with_columns(
            pl.col("lad_cd").alias("lad22cd"),
            pl.col("msoa21nm").alias("msoa_name"),
            pl.lit(None, pl.Utf8).alias("rgn_cd"),
            pl.lit(None, pl.Utf8).alias("rgn_nm"),
        )
        .join(_lsoa_pfa_from_nspl(nspl, nations), on="lsoa21cd", how="left")
        .join(read_centroids(), on="lsoa21cd", how="left")
        .join(pop, on="lsoa21cd", how="left")
        .join(postcodes, on="lsoa21cd", how="left")
        .join(bounds_table(polygons), on="lsoa21cd", how="left")
        .with_columns(nation_columns(nations))
        .with_columns(region_columns(nations))
        .with_columns(
            ruc_class_expr(nations),
            (pl.col("population") / pl.col("area_km2")).alias("density_per_km2"),
            pl.col("n_postcodes").fill_null(0).cast(pl.Int32),
            pl.lit("dz22").alias("area_type"),
        )
        .sort("lsoa21cd")
    )
    missing_names = df.filter(pl.col("lad_nm").is_null()).height
    if missing_names:
        logger.warning(f"{missing_names} Data Zones whose council is not in lad_boundaries")
    logger.info(f"geo_scotland: {df.height:,} Data Zones")
    return df
