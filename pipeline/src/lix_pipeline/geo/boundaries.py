"""Boundary polygons of the small areas: ONS GeoPackages for England and Wales, Scottish
Government shapefiles for Scotland. Each nation names its files in config/nations.yaml
(``geo:``), so the joins and tiles read whichever files the active nations need."""

import geopandas as gpd
import pandas as pd
import polars as pl

from lix_core.codes import active_nations, area_code_regex
from lix_core.config import load_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("geo")


def _code_column(columns: list[str], code_column: str | None) -> str:
    if code_column:
        match = next((c for c in columns if c.lower() == code_column.lower()), None)
        if match is None:
            raise ValueError(f"No column {code_column!r} among {columns}")
        return match
    for candidate in ("LSOA21CD", "lsoa21cd"):
        if candidate in columns:
            return candidate
    # Any code column: ONS's *CD, the Scottish Government's DZCode / IZCode
    coded = [c for c in columns if c.lower().endswith(("cd", "code"))]
    if not coded:
        raise ValueError(f"Cannot find a code column in {columns}")
    return coded[0]


def load_boundaries(
    slug: str, code_column: str | None = None, simplify_tolerance: float = 0.0
) -> gpd.GeoDataFrame:
    """The polygons downloaded under ``data/raw/{slug}`` (a GeoPackage or a shapefile),
    indexed by their code as ``lsoa21cd`` whatever the publisher calls it.

    ``code_column`` names the code attribute when the file has no ``*CD``-style column;
    ``simplify_tolerance`` (metres) simplifies full-resolution polygons.
    """
    folder = data_dir("raw") / slug
    files = sorted(folder.glob("*.gpkg")) or sorted(folder.glob("**/*.shp"))
    if not files:
        raise FileNotFoundError(f"No GeoPackage or shapefile under {folder}")
    path = files[0]
    logger.info(f"Loading boundaries from {path}")
    gdf = gpd.read_file(path)
    code = _code_column(list(gdf.columns), code_column)
    if code != "lsoa21cd":
        gdf = gdf.rename(columns={code: "lsoa21cd"})
    if simplify_tolerance > 0:
        gdf["geometry"] = gdf["geometry"].simplify(tolerance=simplify_tolerance)
    gdf = gdf.set_index("lsoa21cd")
    logger.info(f"Loaded {len(gdf):,} boundaries from {slug}")
    return gdf


def load_lsoa_boundaries(
    slug: str = "lsoa_boundaries", simplify_tolerance: float = 0.0
) -> gpd.GeoDataFrame:
    """The England and Wales LSOA 2021 polygons (``lsoa_boundaries`` is the 20m
    generalised set, ``lsoa_boundaries_bsc`` the 200m one)."""
    return load_boundaries(slug, simplify_tolerance=simplify_tolerance)


def tier_polygons(
    level: str = "low", nations: tuple[str, ...] | None = None, simplify: bool = False
) -> gpd.GeoDataFrame:
    """One tier's polygons across ``nations`` (default: the active ones), each nation's
    file read once and filtered to its own codes; indexed by ``lsoa21cd`` (the mid tier's
    codes too, for uniformity). With ``simplify``, a nation's ``geo.simplify_m`` applies."""
    cfg = load_nations().nations
    nations = nations or active_nations()
    attr = {"low": "low_boundaries", "mid": "mid_boundaries"}[level]
    frames = []
    for slug in dict.fromkeys(getattr(cfg[n].geo, attr) for n in nations):
        users = tuple(n for n in nations if getattr(cfg[n].geo, attr) == slug)
        spec = cfg[users[0]].geo
        gdf = load_boundaries(
            slug,
            code_column=spec.code_column if level == "low" else None,
            simplify_tolerance=spec.simplify_m if simplify else 0.0,
        )
        frames.append(gdf[gdf.index.str.match(area_code_regex(level, nations=users))])
    out = pd.concat(frames) if len(frames) > 1 else frames[0]
    return gpd.GeoDataFrame(out, crs=frames[0].crs)


def bounds_table(gdf: gpd.GeoDataFrame) -> pl.DataFrame:
    """Land area (km²) and WGS84 bounding box per polygon, keyed by ``lsoa21cd``."""
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
    )
