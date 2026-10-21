"""Radon potential at homes: the share of an area's homes in grid squares where more than
a tenth of homes are expected above the action level (classes 5 and 6 of the BGS and
UKHSA indicative atlas, 1 km squares; the EPA map's High Radon Areas, 10 km squares)."""

import numpy as np
import polars as pl

from lix_core.codes import active_nations, nation_of
from lix_core.geo import working_crs
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.radon")

# Atlas classes: 1 under 1% of homes above the action level, 2 1–3%, 3 3–5%, 4 5–10%,
# 5 10–30%, 6 over 30%
BGS_HIGH_CLASS = 5
EPA_HIGH_PERCENT = 10.0

# Each source, the nations it maps, and the threshold on its value column
SOURCES = {
    "bgs_radon_gb": (("E", "W", "S"), BGS_HIGH_CLASS),
    "bgs_radon_ni": (("N",), BGS_HIGH_CLASS),
    "epa_radon": (("I",), EPA_HIGH_PERCENT),
}


def _bgs_polygons(slug: str):
    """The atlas squares with their highest class as ``value``."""
    import geopandas as gpd
    import pyogrio

    paths = sorted((data_dir("raw") / slug).glob("*.gpkg"))
    if not paths:
        raise FileNotFoundError(f"No GeoPackage under {data_dir('raw') / slug}")
    layer = next(
        name for name, _ in pyogrio.list_layers(paths[0]).tolist() if name != "layer_styles"
    )
    # "CLASS_MAX" in the Great Britain file, "Class_max" in Northern Ireland's
    column = next(
        f for f in pyogrio.read_info(paths[0], layer=layer)["fields"] if f.lower() == "class_max"
    )
    gdf = gpd.read_file(paths[0], layer=layer, columns=[column]).to_crs(working_crs())
    return gdf.rename(columns={column: "value"})[["value", "geometry"]]


def _epa_polygons():
    """The EPA's squares with their share of homes above the reference level as ``value``."""
    import geopandas as gpd
    import pandas as pd

    paths = sorted((data_dir("raw") / "epa_radon").glob("*.geojson"))
    if not paths:
        raise FileNotFoundError(f"No GeoJSON under {data_dir('raw') / 'epa_radon'}")
    frames = [gpd.read_file(p, columns=["PERCENT_"]).to_crs(working_crs()) for p in paths]
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=working_crs())
    gdf = gdf.rename(columns={"PERCENT_": "value"})[["value", "geometry"]]
    gdf.loc[gdf["value"] < 0, "value"] = float("nan")  # -999 marks a square without data
    return gdf


def high_share(homes: pl.DataFrame, polygons, threshold: float) -> pl.DataFrame:
    """Per area, the share (%) of ``homes`` (``lsoa21cd``, ``east1m``, ``north1m``) in a
    polygon whose ``value`` reaches ``threshold``."""
    from lix_pipeline.geo.contours import band_at_points

    value = band_at_points(
        polygons, "value", homes["east1m"].to_numpy(), homes["north1m"].to_numpy()
    )
    # A home in a square without data (the map's -999, or off the map) is unknown, not
    # "not high": the share is over the homes the map covers, and null where it covers none
    high = np.where(np.isnan(value), np.nan, (value >= threshold).astype(float))
    return (
        homes.select("lsoa21cd")
        .with_columns(pl.Series("high", high, nan_to_null=True))
        .group_by("lsoa21cd")
        .agg((pl.col("high").mean() * 100).alias("high_share"))
    )


def stage_radon() -> pl.LazyFrame:
    """Share of homes in a high radon area for every area of the active nations."""
    from lix_pipeline.geo.access import residential_postcodes

    homes = residential_postcodes()
    frames = []
    for slug, (nations, threshold) in SOURCES.items():
        covered = [n for n in nations if n in active_nations()]
        if not covered:
            continue
        polygons = _epa_polygons() if slug == "epa_radon" else _bgs_polygons(slug)
        subset = homes.filter(nation_of("lsoa21cd").is_in(covered))
        shares = high_share(subset, polygons, threshold)
        logger.info(
            f"{slug}: {len(polygons):,} squares; {shares['high_share'].mean():.1f}% of homes in "
            f"{covered} in a high radon area on average"
        )
        frames.append(shares)
    if not frames:
        return pl.DataFrame(schema={"lsoa21cd": pl.Utf8, "high_share": pl.Float64}).lazy()
    return pl.concat(frames).sort("lsoa21cd").lazy()
