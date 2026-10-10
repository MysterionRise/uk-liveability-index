"""Tree cover around homes, from ESA WorldCover 10m land cover (2021).

Each 3° tile (36,000 × 36,000 cells) is reduced to blocks of 10 × 10 cells (1/1200°,
about 93m north–south and 57m east–west over Britain) holding the number of tree-cover
cells and of land cells. A box sum over the blocks within about 500m of a point then
gives the share of nearby land under tree canopy; each residential postcode is sampled
and the LSOA takes the mean over its postcodes. Water and unmapped cells are not land,
so a seafront home is judged on the land around it.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import rasterio
from rasterio.windows import Window
from scipy.ndimage import uniform_filter

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes

logger = setup_logging("stage.landcover")

TREE_COVER = 10
WATER = 80
NODATA = 0
BLOCK = 10  # cells per block side
M_PER_DEGREE_LAT = 111_320.0
CANOPY_RADIUS_M = 500.0


@dataclass(frozen=True)
class BlockGrid:
    """Blocks of ``BLOCK`` × ``BLOCK`` cells over a lon/lat box, row 0 at the north edge."""

    lon0: float
    lat1: float
    blocks_per_degree: int
    nrows: int
    ncols: int

    @classmethod
    def covering(cls, bbox: tuple[float, float, float, float], blocks_per_degree: int):
        lon0, lat0, lon1, lat1 = bbox
        return cls(
            lon0=math.floor(lon0 * blocks_per_degree) / blocks_per_degree,
            lat1=math.ceil(lat1 * blocks_per_degree) / blocks_per_degree,
            blocks_per_degree=blocks_per_degree,
            nrows=math.ceil((lat1 - lat0) * blocks_per_degree) + 1,
            ncols=math.ceil((lon1 - lon0) * blocks_per_degree) + 1,
        )

    def rowcol(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rows = np.floor((self.lat1 - lat) * self.blocks_per_degree).astype(np.int64)
        cols = np.floor((lon - self.lon0) * self.blocks_per_degree).astype(np.int64)
        return rows, cols


def blocks_per_degree(path: Path) -> int:
    with rasterio.open(path) as src:
        return round(1 / (abs(src.transform.a) * BLOCK))


def reduce_tile(
    path: Path, grid: BlockGrid, tree: np.ndarray, land: np.ndarray, strip_rows: int = 3600
) -> None:
    """Add a tile's tree-cover and land cell counts per block into ``tree`` and ``land``."""
    with rasterio.open(path) as src:
        t = src.transform
        if src.width % BLOCK or src.height % BLOCK:
            raise ValueError(f"{path.name}: {src.width}x{src.height} is not a multiple of {BLOCK}")
        col0 = round((t.c - grid.lon0) * grid.blocks_per_degree)
        for r0 in range(0, src.height, strip_rows):
            h = min(strip_rows, src.height - r0)
            a = src.read(1, window=Window(0, r0, src.width, h))
            shape = (h // BLOCK, BLOCK, src.width // BLOCK, BLOCK)
            tree_blk = (a == TREE_COVER).reshape(shape).sum(axis=(1, 3), dtype=np.uint8)
            land_blk = (
                ((a != NODATA) & (a != WATER)).reshape(shape).sum(axis=(1, 3), dtype=np.uint8)
            )
            row0 = round((grid.lat1 - (t.f + t.e * r0)) * grid.blocks_per_degree)
            _paste(tree, tree_blk, row0, col0)
            _paste(land, land_blk, row0, col0)


def _paste(target: np.ndarray, block: np.ndarray, row0: int, col0: int) -> None:
    """Add ``block`` into ``target`` at (row0, col0), clipped to the target's bounds."""
    r0, c0 = max(row0, 0), max(col0, 0)
    r1 = min(row0 + block.shape[0], target.shape[0])
    c1 = min(col0 + block.shape[1], target.shape[1])
    if r1 <= r0 or c1 <= c0:
        return
    target[r0:r1, c0:c1] += block[r0 - row0 : r1 - row0, c0 - col0 : c1 - col0]


def canopy_share(
    tree: np.ndarray, land: np.ndarray, grid: BlockGrid, radius_m: float, lat_mid: float
) -> np.ndarray:
    """Tree-cover cells as a share (%) of land cells within about ``radius_m`` of each block;
    NaN where there is no land nearby."""
    m_per_block_lat = M_PER_DEGREE_LAT / grid.blocks_per_degree
    m_per_block_lon = m_per_block_lat * math.cos(math.radians(lat_mid))
    size = (
        2 * round(radius_m / m_per_block_lat) + 1,
        2 * round(radius_m / m_per_block_lon) + 1,
    )
    tree_sum = uniform_filter(tree.astype(np.float32), size=size, mode="constant")
    land_sum = uniform_filter(land.astype(np.float32), size=size, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(land_sum > 0, tree_sum / land_sum * 100, np.nan).astype(np.float32)


def tree_cover_at_homes(
    tiles: list[Path], homes: pl.DataFrame, radius_m: float = CANOPY_RADIUS_M
) -> pl.DataFrame:
    """Per LSOA: mean share of land under tree canopy within ``radius_m`` of its postcodes."""
    lon = homes["long"].to_numpy().astype(float)
    lat = homes["lat"].to_numpy().astype(float)
    margin = 2 * radius_m / M_PER_DEGREE_LAT
    bbox = (lon.min() - margin, lat.min() - margin, lon.max() + margin, lat.max() + margin)
    grid = BlockGrid.covering(bbox, blocks_per_degree(tiles[0]))
    tree = np.zeros((grid.nrows, grid.ncols), dtype=np.uint8)
    land = np.zeros((grid.nrows, grid.ncols), dtype=np.uint8)
    for path in tiles:
        with rasterio.open(path) as src:
            b = src.bounds
        if b.right < bbox[0] or b.left > bbox[2] or b.top < bbox[1] or b.bottom > bbox[3]:
            continue
        logger.info(f"Reducing {path.name}")
        reduce_tile(path, grid, tree, land)
    canopy = canopy_share(tree, land, grid, radius_m, lat_mid=(bbox[1] + bbox[3]) / 2)
    rows, cols = grid.rowcol(lon, lat)
    inside = (rows >= 0) & (rows < grid.nrows) & (cols >= 0) & (cols < grid.ncols)
    values = np.full(len(lon), np.nan, dtype=np.float64)
    values[inside] = canopy[rows[inside], cols[inside]]
    return (
        homes.select("lsoa21cd")
        .with_columns(pl.Series("canopy_pct", values, nan_to_null=True))
        .group_by("lsoa21cd")
        .agg(
            pl.col("canopy_pct").mean(),
            pl.col("canopy_pct").is_not_null().sum().alias("n_postcodes"),
        )
        .sort("lsoa21cd")
    )


def stage_tree_cover() -> pl.LazyFrame:
    tiles = sorted((data_dir("raw") / "esa_worldcover").glob("*.tif"))
    homes = residential_postcodes("lat", "long").filter(
        pl.col("lat").is_not_null() & pl.col("long").is_not_null()
    )
    df = tree_cover_at_homes(tiles, homes)
    logger.info(
        f"{df.height:,} LSOAs from {homes.height:,} postcodes and {len(tiles)} tiles; "
        f"median canopy {df['canopy_pct'].median():.1f}%"
    )
    return df.lazy()
