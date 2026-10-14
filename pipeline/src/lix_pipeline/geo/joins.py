"""Bring data published on other geographies onto LSOA 2021.

Every helper returns a frame keyed by ``lsoa21cd``. Values copied down from a larger
area (MSOA, local authority) get a ``quality`` flag so scores can show they don't vary
within that area.
"""

from functools import lru_cache
from typing import Literal

import geopandas as gpd
import polars as pl

from lix_core.codes import active_nations, area_code_regex
from lix_core.geo import working_crs
from lix_core.paths import data_dir
from lix_pipeline.geo.boundaries import tier_polygons


@lru_cache(maxsize=2)
def _lsoa_polygons(code_regex: str) -> gpd.GeoDataFrame:
    """The active nations' small-area polygons (each nation's own file), with codes
    matching ``code_regex``."""
    gdf = tier_polygons("low", active_nations())
    gdf = gdf[gdf.index.str.match(code_regex)]
    return gdf[["geometry"]].reset_index()


def points_to_lsoa(
    df: pl.DataFrame,
    x: str = "x",
    y: str = "y",
    crs: int | None = None,
    polygons: gpd.GeoDataFrame | None = None,
) -> pl.DataFrame:
    """Add ``lsoa21cd`` to point records (null outside the active nations).

    Coordinates are in the working CRS by default (eastings/northings); pass
    ``crs=4326`` with lon/lat columns. ``polygons`` (columns lsoa21cd, geometry) is for
    tests.
    """
    polys = polygons if polygons is not None else _lsoa_polygons(area_code_regex())
    valid = df[x].is_not_null() & df[y].is_not_null()
    pts = gpd.GeoDataFrame(
        {"_row": range(df.height)},
        geometry=gpd.points_from_xy(df[x].fill_null(0).to_numpy(), df[y].fill_null(0).to_numpy()),
        crs=crs if crs is not None else working_crs(),
    )
    pts = pts[valid.to_numpy()]
    if pts.crs.to_epsg() != polys.crs.to_epsg():
        pts = pts.to_crs(polys.crs)
    # A point on a shared border intersects both LSOAs; keep one so rows aren't duplicated
    # ("within" would instead drop border points altogether)
    hit = gpd.sjoin(pts, polys, how="inner", predicate="intersects")
    hit = hit.sort_values("lsoa21cd").drop_duplicates("_row")
    lookup = pl.DataFrame(
        {"_row": hit["_row"].to_numpy(), "lsoa21cd": hit["lsoa21cd"].to_numpy()},
        schema={"_row": pl.Int64, "lsoa21cd": pl.Utf8},
    )
    return (
        df.with_row_index("_row")
        .with_columns(pl.col("_row").cast(pl.Int64))
        .join(lookup, on="_row", how="left")
        .drop("_row")
    )


def oa_to_lsoa(
    df: pl.DataFrame,
    values: list[str],
    oa_col: str = "oa21cd",
    how: Literal["sum", "weighted_mean"] = "sum",
    weight: str | None = None,
    lookup: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Aggregate output-area values to LSOAs (OAs nest exactly in LSOAs).

    ``sum`` for counts; ``weighted_mean`` for rates, weighted by ``weight`` (e.g. premises).
    """
    if lookup is None:
        lookup = pl.read_parquet(data_dir("staged") / "oa_lookup.parquet")
    joined = df.join(lookup.select("oa21cd", "lsoa21cd"), left_on=oa_col, right_on="oa21cd")
    if how == "sum":
        aggs = [pl.col(v).sum() for v in values]
    else:
        if weight is None:
            raise ValueError("weighted_mean needs a weight column")
        # An LSOA whose OAs all lack the value stays null rather than 0/0 = NaN
        aggs = [
            (
                (pl.col(v) * pl.col(weight)).sum()
                / pl.col(weight).filter(pl.col(v).is_not_null()).sum()
            )
            .fill_nan(None)
            .alias(v)
            for v in values
        ]
    return joined.group_by("lsoa21cd").agg(*aggs).sort("lsoa21cd")


def broadcast(
    df: pl.DataFrame,
    values: list[str],
    level: Literal["msoa", "lad"],
    code_col: str,
    geo: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Copy MSOA- or local-authority-level values down to each LSOA in that area.

    For ``lad``, the codes may come from any boundary vintage the backbone knows
    (current, 2024 or 2022); the one with the most matches is used. Adds
    ``quality = "broadcast_msoa" | "broadcast_lad"``.
    """
    if geo is None:
        geo = pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet")
    if level == "msoa":
        key = "msoa21cd"
    else:
        candidates = [c for c in ("lad_cd", "lad24cd", "lad22cd") if c in geo.columns]
        codes = set(df[code_col].drop_nulls().to_list())
        key = max(candidates, key=lambda c: len(codes & set(geo[c].to_list())))
    out = geo.select("lsoa21cd", key).join(
        df.select(pl.col(code_col).alias(key), *values), on=key, how="left"
    )
    return out.drop(key).with_columns(pl.lit(f"broadcast_{level}").alias("quality"))
