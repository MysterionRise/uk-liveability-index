"""Boundary polygons of the small areas: ONS GeoPackages for England and Wales, Scottish
Government shapefiles for Scotland. Each nation names its files in config/nations.yaml
(``geo:``), so the joins and tiles read whichever files the active nations need."""

import geopandas as gpd
import pandas as pd
import polars as pl

from lix_core.codes import active_nations, area_code_regex, minted_codes
from lix_core.config import LevelName, load_nations
from lix_core.geo import working_crs
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
    slug: str,
    code_column: str | None = None,
    simplify_tolerance: float = 0.0,
    recode: tuple[str, LevelName] | None = None,
) -> gpd.GeoDataFrame:
    """The polygons downloaded under ``data/raw/{slug}`` (a GeoPackage or a shapefile),
    indexed by their code as ``lsoa21cd`` whatever the publisher calls it, in the
    working CRS whatever grid the publisher drew them on (Irish Grid files are
    reprojected; a file without a CRS is taken to be in the working one).

    ``code_column`` names the code attribute when the file has no ``*CD``-style column;
    ``simplify_tolerance`` (metres) simplifies full-resolution polygons. ``recode``
    (nation, level) mints the codes from the publisher's ids (``lix_core.codes.
    minted_codes``); polygons sharing a code (a division in two parts) are dissolved.
    """
    folder = data_dir("raw") / slug
    files = sorted(folder.glob("*.gpkg")) or sorted(folder.glob("**/*.shp"))
    if not files:
        raise FileNotFoundError(f"No GeoPackage or shapefile under {folder}")
    path = files[0]
    logger.info(f"Loading boundaries from {path}")
    gdf = gpd.read_file(path)
    working = working_crs()
    if gdf.crs is None:
        gdf = gdf.set_crs(working)
    elif gdf.crs.to_epsg() != working:
        logger.info(f"{slug}: reprojecting from {gdf.crs.to_string()} to EPSG:{working}")
        gdf = gdf.to_crs(working)
    code = _code_column(list(gdf.columns), code_column)
    if code != "lsoa21cd":
        gdf = gdf.rename(columns={code: "lsoa21cd"})
    gdf["lsoa21cd"] = gdf["lsoa21cd"].astype(str).str.strip()
    if recode is not None:
        nation, level = recode
        gdf["lsoa21cd"] = gdf["lsoa21cd"].map(minted_codes(nation, level, gdf["lsoa21cd"]))
    if gdf["lsoa21cd"].duplicated().any():
        parts = len(gdf)
        gdf = gdf.dissolve(by="lsoa21cd", as_index=False, aggfunc="first")
        logger.info(f"{slug}: {parts - len(gdf)} polygons dissolved into their areas")
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
        # ``code_column`` names the low tier's code; it applies to the mid pass too when
        # a nation's low areas are their own mid tier (Northern Ireland's Super Data Zones)
        same_file = spec.mid_boundaries == spec.low_boundaries
        column = spec.code_column if level == "low" or same_file else spec.mid_code_column
        gdf = load_boundaries(
            slug,
            code_column=column,
            simplify_tolerance=spec.simplify_m if simplify else 0.0,
            recode=(users[0], level) if spec.minted_codes else None,
        )
        frames.append(gdf[gdf.index.str.match(area_code_regex(level, nations=users))])
    out = pd.concat(frames) if len(frames) > 1 else frames[0]
    return gpd.GeoDataFrame(out, crs=working_crs())


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


def upper_polygons(nation: str) -> gpd.GeoDataFrame:
    """A nation's upper-tier polygons (``lad_cd``, ``lad_nm``, geometry) in the working
    CRS: the UK local-authority file filtered to the nation's codes, or the nation's own
    file through its builder (Ireland's councils, named through the curated list)."""
    spec = load_nations().nations[nation]
    if spec.geo_builder == "geo_ie":
        from lix_pipeline.stage.geo_ie import upper_polygons as ireland

        return ireland()
    gdf = gpd.read_file(
        data_dir("raw") / spec.geo.upper_boundaries / f"{spec.geo.upper_boundaries}.gpkg"
    )
    code = next(c for c in gdf.columns if c.upper().startswith("LAD") and c.upper().endswith("CD"))
    gdf = gdf.rename(columns={code: "lad_cd", code[:-2] + "NM": "lad_nm"})
    gdf = gdf[gdf["lad_cd"].str.match(area_code_regex("upper", nations=(nation,)))]
    if gdf.crs is None or gdf.crs.to_epsg() != working_crs():
        gdf = gdf.set_crs(working_crs()) if gdf.crs is None else gdf.to_crs(working_crs())
    return gpd.GeoDataFrame(gdf[["lad_cd", "lad_nm", "geometry"]], crs=working_crs())
