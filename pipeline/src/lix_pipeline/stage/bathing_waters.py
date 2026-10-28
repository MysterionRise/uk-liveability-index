"""Designated bathing waters and the class of their latest season (#79), in one table
across the five territories: a point per water (its sampling point, or the centre of
its area), its name, and ``class_score`` 4 Excellent, 3 Good, 2 Sufficient, 1 Poor
(null when not yet classified).

- England: the Environment Agency's monitoring locations, which carry the latest class.
- Wales: Natural Resources Wales's layer with its classification.
- Scotland: SEPA's points with the current class.
- Northern Ireland: DAERA's 33 areas and the classes DAERA announces each November,
  transcribed in ``config/data/bathing_water_classes_ni.csv`` (no machine-readable list).
- Ireland: the EPA's layer ("Excellent Water Quality" and so on).
"""

from __future__ import annotations

import geopandas as gpd
import polars as pl
import pyogrio

from lix_core.log import setup_logging
from lix_core.paths import data_dir, get_project_root

logger = setup_logging("stage.bathing_waters")

CLASS_SCORE = {"excellent": 4, "good": 3, "sufficient": 2, "poor": 1}


def class_score(text: str | None) -> int | None:
    """'Excellent', 'Good Water Quality', 'un-assessed' → 4, 3, None."""
    if text is None:
        return None
    word = str(text).strip().lower().split(" ")[0]
    return CLASS_SCORE.get(word)


def _points(gdf: gpd.GeoDataFrame) -> gpd.GeoSeries:
    geoms = gdf.to_crs(27700).geometry
    return geoms.where(geoms.geom_type == "Point", geoms.representative_point())


def _frame(nation: str, gdf: gpd.GeoDataFrame, name: str, cls: str, season=None) -> pl.DataFrame:
    pts = _points(gdf)
    return pl.DataFrame(
        {
            "nation": nation,
            "name": gdf[name].astype(str).str.strip().to_list(),
            "class": [None if c is None else str(c) for c in gdf[cls].to_list()],
            "season": [
                None if s is None else str(s)
                for s in (gdf[season].to_list() if season else [None] * len(gdf))
            ],
            "x": pts.x.to_numpy(),
            "y": pts.y.to_numpy(),
        },
        schema={
            "nation": pl.Utf8,
            "name": pl.Utf8,
            "class": pl.Utf8,
            "season": pl.Utf8,
            "x": pl.Float64,
            "y": pl.Float64,
        },
    )


def _read(slug: str, pattern: str) -> gpd.GeoDataFrame | None:
    files = sorted((data_dir("raw") / slug).glob(pattern))
    if not files:
        logger.info(f"[{slug}] not fetched")
        return None
    return pyogrio.read_dataframe(str(files[0]))


def stage_bathing_waters() -> pl.LazyFrame:
    frames = []
    if (e := _read("ea_bathing_waters", "**/*.gpkg")) is not None:
        e = e[e["notes"].fillna("") != "De-designated"]
        frames.append(_frame("E", e, "bw_name", "class"))
    if (w := _read("nrw_bathing_waters", "*.gpkg")) is not None:
        frames.append(_frame("W", w, "name", "classification"))
    if (s := _read("sepa_bathing_waters", "*.gdb")) is not None:
        s = s[s["current_yn"].fillna("Y") == "Y"]
        frames.append(_frame("S", s, "description", "class_description", "year"))
    if (n := _read("daera_bathing_waters", "*.gpkg")) is not None:
        classes = pl.read_csv(get_project_root() / "config/data/bathing_water_classes_ni.csv")
        lookup = dict(zip(classes["name"], classes["class"], strict=True))
        season = dict(zip(classes["name"], classes["season"], strict=True))
        n["class"] = n["Name"].str.strip().map(lookup)
        n["season"] = n["Name"].str.strip().map(season)
        missing = n.loc[n["class"].isna(), "Name"].tolist()
        if missing:
            logger.warning(f"Northern Irish bathing waters without a curated class: {missing}")
        frames.append(_frame("N", n, "Name", "class", "season"))
    if (i := _read("epa_bathing_waters", "*.geojson")) is not None:
        if i.crs is None:
            i = i.set_crs(4326)
        frames.append(_frame("I", i, "Name", "CurrentClassification", "LatestYear"))
    if not frames:
        raise FileNotFoundError("No bathing water source fetched for the active nations")
    df = pl.concat(frames).with_columns(
        pl.col("class").map_elements(class_score, return_dtype=pl.Int8).alias("class_score")
    )
    counts = df.group_by("nation").agg(
        pl.len(), pl.col("class_score").is_not_null().sum().alias("classified")
    )
    logger.info(f"{df.height} bathing waters: {counts.sort('nation').to_dicts()}")
    return df.lazy()
