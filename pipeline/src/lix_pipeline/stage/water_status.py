"""Water Framework Directive status of rivers and lakes (#82), as points every 100 m
along every river water body and around every lake, each with its class: 5 High,
4 Good, 3 Moderate, 2 Poor, 1 Bad ("good ecological potential" and the like, for
heavily modified waters, rank with good).

The measure and the year differ by territory, which the caveat says: ecological status
in England (2025), Wales (2024, interim), Northern Ireland (2018) and Ireland (2019–2024);
overall status in Scotland (2024), the only class SEPA's open layer carries. Northern
Ireland's statuses are a table by water-body code, joined to the river network (lines
that carry the code) and to the lake outlines.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import polars as pl
import pyogrio
import shapely

from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.water_status")

SPACING_M = 100.0
WORDS = {"high": 5, "good": 4, "moderate": 3, "poor": 2, "bad": 1}
# Northern Ireland writes heavily modified waters' potential as initials
INITIALS = {"GEP": 4, "MEP": 3, "PEP": 2, "BEP": 1}


def class_score(text) -> int | None:
    if text is None:
        return None
    t = str(text).strip()
    if t.upper() in INITIALS:
        return INITIALS[t.upper()]
    return WORDS.get(t.lower().split(" ")[0])


def _read(slug: str, pattern: str = "*") -> gpd.GeoDataFrame | None:
    folder = data_dir("raw") / slug
    files = [
        p
        for p in sorted(folder.glob(pattern))
        if p.suffix in (".gpkg", ".geojson") and ".part" not in p.name
    ]
    if not files:
        logger.info(f"[{slug}] not fetched")
        return None
    gdf = pyogrio.read_dataframe(str(files[0]))
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    return gdf.to_crs(27700)


def points_along(geoms: gpd.GeoSeries, scores, spacing_m: float = SPACING_M) -> pl.DataFrame:
    """Every ``spacing_m`` along lines, and along the outlines of polygons, each point
    carrying its feature's score; features without a score are left out."""
    scores = np.asarray(scores, dtype=float)
    keep = ~np.isnan(scores) & geoms.notna().to_numpy() & ~geoms.is_empty.to_numpy()
    g = geoms.to_numpy()[keep]
    s = scores[keep]
    # Polygons (type 3) and multipolygons (6) become their outlines; lines stay lines
    polygonal = np.isin(shapely.get_type_id(g), [3, 6])
    g = np.where(polygonal, shapely.boundary(g), g)
    # Vertex density varies by publisher (NRW's lines have a vertex every metre or so):
    # simplify to 10 m, then add a point at least every ``spacing_m``
    simple = shapely.simplify(g, 10.0)
    # A part simplified to a single point (the EPA has a few degenerate lines) cannot be
    # densified: keep those features as they were
    simple = np.where(shapely.is_valid(simple) & ~shapely.is_empty(simple), simple, g)
    try:
        dense = shapely.segmentize(simple, spacing_m)
    except shapely.errors.GEOSException:
        dense = np.array([_segmentize_or_keep(x, spacing_m) for x in simple], dtype=object)
    xy, idx = shapely.get_coordinates(dense, return_index=True)
    return pl.DataFrame({"x": xy[:, 0], "y": xy[:, 1], "class_score": s[idx].astype(np.int8)})


def _segmentize_or_keep(geom, spacing_m: float):
    try:
        return shapely.segmentize(geom, spacing_m)
    except shapely.errors.GEOSException:
        return geom


def _scored(gdf: gpd.GeoDataFrame, column: str) -> list:
    return [class_score(v) for v in gdf[column].to_list()]


def stage_water_status() -> pl.LazyFrame:
    frames: list[pl.DataFrame] = []

    def add(nation: str, kind: str, gdf: gpd.GeoDataFrame | None, column: str):
        if gdf is None:
            return
        scores = [np.nan if v is None else v for v in _scored(gdf, column)]
        pts = points_along(gdf.geometry, scores)
        frames.append(pts.with_columns(nation=pl.lit(nation), kind=pl.lit(kind)))
        logger.info(f"{nation} {kind}: {len(gdf):,} water bodies, {pts.height:,} points")

    add("E", "river", _read("ea_wfd_rivers", "**/*"), "ecological_class")
    add("E", "lake", _read("ea_wfd_lakes", "**/*"), "ecological_class")
    add("W", "river", _read("nrw_wfd_rivers"), "eco")
    add("W", "lake", _read("nrw_wfd_lakes"), "eco")
    add("S", "river", _read("sepa_wfd_rivers"), "overall_classification")
    add("S", "lake", _read("sepa_wfd_lochs"), "overall_classification")
    status_path = data_dir("raw") / "daera_wfd_status_2018" / "daera_wfd_status_2018.parquet"
    if status_path.exists():
        status = pl.read_parquet(status_path)
        lookup = dict(
            zip(status["localId"].to_list(), status["F2018_statu"].to_list(), strict=False)
        )
        rivers = _read("daera_river_segments")
        if rivers is not None:
            rivers["status"] = rivers["RWB_CD"].map(lookup)
            add("N", "river", rivers, "status")
        lakes = _read("daera_lake_water_bodies")
        if lakes is not None:
            lakes["status"] = lakes["localId"].map(lookup)
            add("N", "lake", lakes, "status")
    add("I", "river", _read("epa_wfd_rivers"), "Status")
    add("I", "lake", _read("epa_wfd_lakes"), "Status")
    if not frames:
        raise FileNotFoundError("No water status source fetched for the active nations")
    df = pl.concat(frames)
    logger.info(
        f"{df.height:,} points; classes "
        f"{dict(df.group_by('class_score').len().sort('class_score').iter_rows())}"
    )
    return df.lazy()
