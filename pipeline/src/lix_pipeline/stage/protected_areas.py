"""Protected nature around homes (#84): hectares of statutorily protected land within
about 2 km of each home, averaged over an area's homes.

The designations of each territory (``pa_*`` in the registry: SSSI/ASSI, national and
local nature reserves, SAC and SPA in the UK; SAC, SPA and NHA in Ireland) are burned
onto one 50 m grid as a single footprint, so a site that is both an SSSI and an SAC
counts once, and clipped to land (the small-area boundaries), so the marine parts of
coastal SACs and SPAs do not count. A moving window with the area of a 2 km disc then
gives the protected share, in hectares, around every cell, sampled at the homes
(``geo/focal.py``).
"""

from __future__ import annotations

import zipfile

import geopandas as gpd
import numpy as np
import polars as pl
import pyogrio
import shapely
from shapely.geometry import box

from lix_core.codes import active_nations
from lix_core.config import load_registry
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes
from lix_pipeline.geo.focal import BngGrid, box_mean, focal_at_homes, rasterize_polygons

logger = setup_logging("stage.protected_areas")

CELL_M = 50.0
RADIUS_M = 2_000.0
SOURCE_PREFIX = "pa_"


def _vector_paths(slug: str) -> list[str]:
    """Every vector file a source's download holds: GeoPackages and shapefiles on disk,
    and those inside zips the fetcher keeps whole (NatureScot's, one per designation)."""
    folder = data_dir("raw") / slug
    paths = [str(p) for p in sorted(folder.glob("**/*.gpkg")) if ".part" not in p.name]
    paths += [str(p) for p in sorted(folder.glob("**/*.shp"))]
    paths += [str(p) for p in sorted(folder.glob("**/*.geojson"))]
    for z in sorted(folder.glob("*.zip")):
        inner = [n for n in zipfile.ZipFile(z).namelist() if n.endswith((".gpkg", ".shp"))]
        paths += [f"/vsizip/{z}/{n}" for n in inner]
    return paths


def site_slugs() -> list[str]:
    active = set(active_nations())
    return [
        s
        for s, spec in load_registry().items()
        if s.startswith(SOURCE_PREFIX) and active & set(spec.coverage)
    ]


def load_sites(slugs: list[str] | None = None) -> gpd.GeoSeries:
    """Every designation's polygons in British National Grid."""
    parts = []
    for slug in slugs if slugs is not None else site_slugs():
        for path in _vector_paths(slug):
            gdf = pyogrio.read_dataframe(path, columns=[])
            if gdf.crs is None:
                raise ValueError(f"{path} has no CRS")
            geoms = gdf.to_crs(27700).geometry
            geoms = geoms[~geoms.is_empty & geoms.notna()].make_valid()
            logger.info(f"[{slug}] {len(geoms):,} polygons from {path.rsplit('/', 1)[-1]}")
            parts.append(geoms)
    if not parts:
        raise FileNotFoundError("No protected-site boundaries fetched for the active nations")
    return gpd.GeoSeries(np.concatenate([p.to_numpy() for p in parts]), crs=27700)


def protected_hectares(
    sites: gpd.GeoSeries,
    land: gpd.GeoSeries,
    homes: pl.DataFrame,
    cell_m: float = CELL_M,
    radius_m: float = RADIUS_M,
    block_m: float = 100_000.0,
) -> pl.DataFrame:
    """Per area: the mean over its homes of the protected land (hectares) in the window
    with the area of a ``radius_m`` disc around each home, and ``n_postcodes``."""
    site_geoms = sites.to_numpy()
    land_geoms = land.to_numpy()
    site_tree = shapely.STRtree(site_geoms)
    land_tree = shapely.STRtree(land_geoms)
    window_ha = np.pi * radius_m**2 / 10_000

    def block(g: BngGrid) -> np.ndarray | None:
        frame = box(*g.bounds)
        on_land = land_geoms[land_tree.query(frame)]
        if not len(on_land):
            return None
        protected = rasterize_polygons(site_geoms[site_tree.query(frame)], g)
        protected &= rasterize_polygons(on_land, g)
        return box_mean(protected.astype(np.float32), cell_m, radius_m) * window_ha

    return focal_at_homes(
        block, homes, cell_m=cell_m, halo_m=radius_m * 1.2, block_m=block_m, log=logger.info
    ).rename({"value": "protected_ha"})


def stage_protected_areas() -> pl.LazyFrame:
    from lix_pipeline.geo.boundaries import tier_polygons

    sites = load_sites()
    land = tier_polygons("low").geometry
    homes = residential_postcodes()
    df = protected_hectares(sites, land, homes)
    logger.info(
        f"{df.height:,} areas from {homes.height:,} homes and {len(sites):,} site polygons; "
        f"median {df['protected_ha'].median():.0f} ha within about {RADIUS_M / 1000:g} km"
    )
    return df.lazy()
