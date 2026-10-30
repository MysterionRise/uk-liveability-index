"""Slope and height around homes (#128): ``hilliness`` (the mean slope, in percent,
within about 500 m of each home) and ``elevation`` (the height of the home's own 50 m
cell), averaged over an area's homes.

Three terrain models meet on one 50 m grid in British National Grid (``geo/focal.py``):

- **Great Britain:** OS Terrain 50, 10 km tiles of 200 × 200 cells already on that grid,
  so they are pasted in, not resampled (each tile is a zip the stager reads in place).
- **Northern Ireland:** OSNI's 50 m DTM, published as X, Y, Z points in the Irish Grid;
  they are burned into a raster once and resampled (bilinear).
- **Ireland:** Copernicus GLO-30, a 30 m surface model in lon/lat, resampled (bilinear).
  A surface model includes buildings and trees, so slope reads higher in towns and
  woods than a terrain model would; the 500 m window damps it, and Irish areas are
  compared among themselves by default.

Slope is Horn's method on the 50 m grid; a single 50 m step of 1 m is a 2 % slope.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import numpy as np
import polars as pl
import rasterio
from rasterio.transform import from_origin

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes
from lix_pipeline.geo.focal import (
    BngGrid,
    box_mean,
    focal_at_homes,
    horn_slope_pct,
    reproject_to_grid,
)

logger = setup_logging("stage.terrain")

CELL_M = 50.0
RADIUS_M = 500.0
TILE_M = 10_000  # OS Terrain 50's tiles
IRISH_GRID = "EPSG:29902"


def terrain50_index(folder: Path) -> dict[tuple[int, int], tuple[str, rasterio.Affine]]:
    """OS Terrain 50's tiles by their 10 km square (x // 10 km, y // 10 km): the path to
    each ASCII grid inside its zip, and its transform."""
    out = {}
    for z in sorted(folder.glob("**/*.zip")):
        for name in zipfile.ZipFile(z).namelist():
            if not name.lower().endswith(".asc"):
                continue
            path = f"/vsizip/{z}/{name}"
            with rasterio.open(path) as src:
                b = src.bounds
                out[(int(b.left // TILE_M), int(b.bottom // TILE_M))] = (path, src.transform)
    return out


def paste_aligned(index: dict, grid: BngGrid) -> np.ndarray:
    """The tiles that meet ``grid``, pasted cell for cell (they share its 50 m lattice);
    NaN where none does."""
    out = np.full(grid.shape, np.nan, dtype=np.float32)
    x0, y0, x1, y1 = grid.bounds
    for tx in range(int(x0 // TILE_M), int((x1 - 1) // TILE_M) + 1):
        for ty in range(int(y0 // TILE_M), int((y1 - 1) // TILE_M) + 1):
            hit = index.get((tx, ty))
            if hit is None:
                continue
            path, t = hit
            with rasterio.open(path) as src:
                a = src.read(1, masked=True).astype(np.float32).filled(np.nan)
            col = round((t.c - grid.x0) / grid.cell_m)
            row = round((grid.y1 - t.f) / grid.cell_m)
            r0, c0 = max(row, 0), max(col, 0)
            r1 = min(row + a.shape[0], grid.nrows)
            c1 = min(col + a.shape[1], grid.ncols)
            if r1 > r0 and c1 > c0:
                out[r0:r1, c0:c1] = a[r0 - row : r1 - row, c0 - col : c1 - col]
    return out


def points_to_raster(points: pl.DataFrame, path: Path, crs: str, cell_m: float = CELL_M) -> Path:
    """X, Y, Z points on a regular lattice (cell centres) → a GeoTIFF."""
    x = points["X"].to_numpy().astype(float)
    y = points["Y"].to_numpy().astype(float)
    z = points["Z"].to_numpy().astype(np.float32)
    left, top = x.min() - cell_m / 2, y.max() + cell_m / 2
    cols = np.round((x - x.min()) / cell_m).astype(np.int64)
    rows = np.round((y.max() - y) / cell_m).astype(np.int64)
    arr = np.full((rows.max() + 1, cols.max() + 1), np.nan, dtype=np.float32)
    arr[rows, cols] = z
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
        dtype="float32", crs=crs, transform=from_origin(left, top, cell_m, cell_m),
        nodata=np.nan, compress="deflate",
    ) as dst:  # fmt: skip
        dst.write(arr, 1)
    return path


def terrain_at_homes(
    index: dict,
    other: list[str | Path],
    homes: pl.DataFrame,
    cell_m: float = CELL_M,
    radius_m: float = RADIUS_M,
    block_m: float = 100_000.0,
) -> pl.DataFrame:
    """Per area: mean slope (%) within ``radius_m`` and the height of the homes' cells."""

    def block(g: BngGrid) -> dict[str, np.ndarray] | None:
        dem = paste_aligned(index, g) if index else np.full(g.shape, np.nan, dtype=np.float32)
        if other and np.isnan(dem).any():
            fill = reproject_to_grid(other, g, "bilinear")
            dem = np.where(np.isnan(dem), fill, dem)
        if np.isnan(dem).all():
            return None
        slope = horn_slope_pct(dem, cell_m)
        return {"hilliness_pct": box_mean(slope, cell_m, radius_m), "elevation_m": dem}

    return focal_at_homes(
        block, homes, cell_m=cell_m, halo_m=radius_m * 1.5, block_m=block_m, log=logger.info
    )


def stage_terrain() -> pl.LazyFrame:
    raw = data_dir("raw")
    index = terrain50_index(raw / "os_terrain_50") if (raw / "os_terrain_50").exists() else {}
    logger.info(f"OS Terrain 50: {len(index):,} tiles")
    homes = residential_postcodes()
    with tempfile.TemporaryDirectory() as tmp:
        other: list[str | Path] = []
        csvs = sorted((raw / "osni_dtm_50m").glob("**/*.csv"))
        if csvs:
            points = pl.read_csv(csvs[0], columns=["X", "Y", "Z"])
            other.append(points_to_raster(points, Path(tmp) / "osni_dtm.tif", IRISH_GRID))
            logger.info(f"OSNI DTM: {points.height:,} points")
        other += sorted(str(p) for p in (raw / "copernicus_dem_glo30").glob("*.tif"))
        df = terrain_at_homes(index, other, homes)
    logger.info(
        f"{df.height:,} areas; median slope {df['hilliness_pct'].median():.1f}%, "
        f"median height {df['elevation_m'].median():.0f} m"
    )
    return df.lazy()
