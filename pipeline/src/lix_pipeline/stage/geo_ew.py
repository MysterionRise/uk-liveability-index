"""England and Wales geography backbone, from the ONS LSOA 2021 files both nations share.

``build()`` is called by ``stage_geo_lsoa`` with the active nations among E and W. Apart
from the nation columns themselves every column is nation-agnostic, so Welsh and English
rows look alike to the indicators.
"""

import geopandas as gpd
import polars as pl

from lix_core.codes import area_code_regex, in_scope, nation_of
from lix_core.config import load_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file
from lix_pipeline.geo.boundaries import load_lsoa_boundaries
from lix_pipeline.geo.crs import bng_to_lonlat
from lix_pipeline.geo.nspl import load_nspl, nspl_document

logger = setup_logging("stage.geo_ew")

Nations = tuple[str, ...]


def nation_columns(nations: Nations) -> list[pl.Expr]:
    """``nation``, ``ctry_cd`` and ``area_type`` from the LSOA code."""
    cfg = load_nations().nations
    ctry = {n: cfg[n].ctry_cd for n in nations}
    return [
        nation_of("lsoa21cd").alias("nation"),
        nation_of("lsoa21cd").replace_strict(ctry, default=None).alias("ctry_cd"),
        pl.lit("lsoa21").alias("area_type"),
    ]


def ruc_class_expr(nations: Nations) -> pl.Expr:
    """``ruc21cd`` → the harmonised urban | town | rural class from nations.yaml."""
    cfg = load_nations().nations
    mapping: dict[str, str] = {}
    for n in nations:
        mapping.update(cfg[n].ruc_map)
    return pl.col("ruc21cd").replace_strict(mapping, default=None).alias("ruc_class")


def region_columns(nations: Nations) -> list[pl.Expr]:
    """Region code and name; where a nation has no regions (Wales), the nation stands in."""
    cfg = load_nations().nations
    pseudo = [n for n in nations if cfg[n].pseudo_region]
    if not pseudo:
        return [pl.col("rgn_cd"), pl.col("rgn_nm")]
    is_pseudo = pl.col("nation").is_in(pseudo)
    names = {n: cfg[n].name for n in pseudo}
    return [
        pl.when(is_pseudo).then(pl.col("ctry_cd")).otherwise(pl.col("rgn_cd")).alias("rgn_cd"),
        pl.when(is_pseudo)
        .then(pl.col("nation").replace_strict(names, default=None))
        .otherwise(pl.col("rgn_nm"))
        .alias("rgn_nm"),
    ]


def _lsoa_pfa_from_nspl(nspl: pl.LazyFrame, nations: Nations) -> pl.DataFrame:
    """Police force area per LSOA (majority of its postcodes), with the force's name."""
    pfa = (
        nspl.filter(in_scope("lsoa21cd", nations=nations))
        .group_by("lsoa21cd", "pfa_cd")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
        .collect()
        .unique("lsoa21cd", keep="first")
        .drop("n")
    )
    doc = pl.read_csv(nspl_document("PFA*names and codes*.csv"), encoding="utf8-lossy")
    names = doc.select(
        pl.col(next(c for c in doc.columns if c.upper().endswith("CD"))).alias("pfa_cd"),
        pl.col(next(c for c in doc.columns if c.upper().endswith("NM"))).alias("pfa_nm"),
    )
    return pfa.join(names, on="pfa_cd", how="left")


def _lsoa_lad_from_nspl(nspl: pl.LazyFrame, nations: Nations) -> pl.DataFrame:
    """Current local authority and region per LSOA: the most common among its postcodes.

    The ONS OA→LAD lookup is frozen at 2022 boundaries; NSPL carries the latest ones.
    """
    counts = (
        nspl.filter(in_scope("lsoa21cd", nations=nations))
        .group_by("lsoa21cd", "lad_cd", "rgn_cd")
        .agg(pl.len().alias("n"))
        .collect()
    )
    split = (
        counts.group_by("lsoa21cd")
        .agg(pl.col("lad_cd").n_unique().alias("k"))
        .filter(pl.col("k") > 1)
    )
    if split.height:
        logger.warning(f"{split.height} LSOAs span more than one local authority; using majority")
    return counts.sort("n", descending=True).unique("lsoa21cd", keep="first").drop("n")


def _other_language(other: pl.Expr, english: pl.Expr) -> pl.Expr:
    """A Welsh (or Gaelic) name column: null where blank or the same as the English."""
    cleaned = other.cast(pl.Utf8).str.strip_chars()
    return pl.when((cleaned == "") | (cleaned == english)).then(None).otherwise(cleaned)


def _lsoa_bounds(nations: Nations) -> pl.DataFrame:
    """Land area (km², from the 20m-generalised clipped polygons), WGS84 bounding box and
    the Welsh name where the file has one."""
    gdf = load_lsoa_boundaries("lsoa_boundaries")
    gdf = gdf[gdf.index.str.match(area_code_regex(nations=nations))]
    welsh = (
        pl.DataFrame(
            {
                "lsoa21cd": gdf.index.to_list(),
                "w": gdf["LSOA21NMW"].to_list(),
                "e": gdf["LSOA21NM"].to_list(),
            }
        ).select("lsoa21cd", _other_language(pl.col("w"), pl.col("e")).alias("lsoa21nm_cy"))
        if "LSOA21NMW" in gdf.columns
        else pl.DataFrame({"lsoa21cd": gdf.index.to_list()}).with_columns(
            pl.lit(None, pl.Utf8).alias("lsoa21nm_cy")
        )
    )
    area = gdf.geometry.area / 1e6
    bbox = gdf.to_crs(4326).geometry.bounds
    return pl.DataFrame(
        {
            "lsoa21cd": gdf.index.to_list(),
            "area_km2": area.to_numpy(),
            "bbox_w": bbox["minx"].to_numpy(),
            "bbox_s": bbox["miny"].to_numpy(),
            "bbox_e": bbox["maxx"].to_numpy(),
            "bbox_n": bbox["maxy"].to_numpy(),
        }
    ).join(welsh, on="lsoa21cd", how="left")


def build(nations: Nations) -> pl.DataFrame:
    """One row per LSOA 2021 of ``nations`` (any of E and W)."""
    unsupported = [n for n in nations if n not in ("E", "W")]
    if unsupported:
        raise ValueError(f"geo_ew builds England and Wales, not {unsupported}")

    oa = pl.read_parquet(raw_file("oa_lookup", "parquet"))
    lsoa = (
        oa.filter(in_scope("LSOA21CD", nations=nations))
        .select(
            pl.col("LSOA21CD").alias("lsoa21cd"),
            pl.col("LSOA21NM").alias("lsoa21nm"),
            pl.col("MSOA21CD").alias("msoa21cd"),
            pl.col("MSOA21NM").alias("msoa21nm"),
            pl.col("LAD22CD").alias("lad22cd"),
        )
        .unique("lsoa21cd")
    )

    msoa_names = pl.read_csv(raw_file("msoa_names", "csv"), encoding="utf8-lossy").select(
        pl.col("msoa21cd"),
        pl.col("msoa21hclnm").alias("msoa_name"),
        _other_language(pl.col("msoa21hclnmw"), pl.col("msoa21hclnm")).alias("msoa_name_cy"),
    )

    # Live postcodes only: terminated ones can carry superseded authority codes
    nspl = load_nspl(nations=nations, live_only=True)
    lad = _lsoa_lad_from_nspl(nspl, nations)
    lad_names = gpd.read_file(raw_file("lad_boundaries", "gpkg"), ignore_geometry=True)
    lad_code_col = next(
        c for c in lad_names.columns if c.upper().startswith("LAD") and c.upper().endswith("CD")
    )
    lad_name_col = lad_code_col[:-2] + "NM"
    lad_welsh_col = lad_code_col[:-2] + "NMW"
    lad_names = pl.from_pandas(lad_names[[lad_code_col, lad_name_col, lad_welsh_col]]).select(
        pl.col(lad_code_col).alias("lad_cd"),
        pl.col(lad_name_col).alias("lad_nm"),
        _other_language(pl.col(lad_welsh_col), pl.col(lad_name_col)).alias("lad_nm_cy"),
    )
    rgn_doc = pl.read_csv(nspl_document("RGN*names and codes*.csv"), encoding="utf8-lossy")
    rgn_names = rgn_doc.select(
        pl.col(next(c for c in rgn_doc.columns if c.upper().endswith("CD"))).alias("rgn_cd"),
        pl.col(next(c for c in rgn_doc.columns if c.upper().endswith("NM"))).alias("rgn_nm"),
    )

    postcodes = nspl.group_by("lsoa21cd").agg(pl.len().alias("n_postcodes")).collect()

    ruc = pl.read_parquet(raw_file("ruc_2021", "parquet")).select(
        pl.col("LSOA21CD").alias("lsoa21cd"),
        pl.col("RUC21CD").alias("ruc21cd"),
        pl.col("RUC21NM").alias("ruc21nm"),
        (pl.col("Urban_rural_flag") == "Urban").alias("urban"),
    )

    centroids = pl.read_parquet(raw_file("lsoa_centroids", "parquet")).select(
        pl.col("LSOA21CD").alias("lsoa21cd"), pl.col("x").alias("pwc_x"), pl.col("y").alias("pwc_y")
    )
    lon, lat = bng_to_lonlat(centroids["pwc_x"], centroids["pwc_y"])
    centroids = centroids.with_columns(pwc_lon=lon, pwc_lat=lat)

    # Latest ONS mid-year estimates (staged by stage_population), the same source for E and W
    pop = pl.read_parquet(data_dir("staged") / "population.parquet").select(
        "lsoa21cd", "population", "pop_year"
    )

    df = (
        lsoa.join(msoa_names, on="msoa21cd", how="left")
        .join(lad, on="lsoa21cd", how="left")
        .join(lad_names, on="lad_cd", how="left")
        .join(rgn_names, on="rgn_cd", how="left")
        .join(_lsoa_pfa_from_nspl(nspl, nations), on="lsoa21cd", how="left")
        .join(ruc, on="lsoa21cd", how="left")
        .join(centroids, on="lsoa21cd", how="left")
        .join(pop, on="lsoa21cd", how="left")
        .join(postcodes, on="lsoa21cd", how="left")
        .join(_lsoa_bounds(nations), on="lsoa21cd", how="left")
        .with_columns(nation_columns(nations))
        .with_columns(region_columns(nations))
        # Welsh LSOA names are the council's Welsh name plus the LSOA's number; built from
        # the council name because the ONS file misspells a few (Ynys Mfn for Ynys Môn)
        .with_columns(
            pl.when(
                pl.col("lad_nm_cy").is_not_null()
                & pl.col("lsoa21nm").str.starts_with(pl.col("lad_nm"))
            )
            .then(
                pl.concat_str(
                    pl.col("lad_nm_cy"),
                    pl.col("lsoa21nm").str.slice(pl.col("lad_nm").str.len_chars()),
                )
            )
            .otherwise(pl.col("lsoa21nm_cy"))
            .alias("lsoa21nm_cy")
        )
        .with_columns(
            ruc_class_expr(nations),
            (pl.col("population") / pl.col("area_km2")).alias("density_per_km2"),
            pl.col("n_postcodes").fill_null(0).cast(pl.Int32),
        )
        .sort("lsoa21cd")
    )
    logger.info(f"geo_ew: {df.height:,} LSOAs for {'+'.join(nations)}")
    return df
