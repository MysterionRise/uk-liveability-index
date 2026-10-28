"""Neighbourhood statistics on a metre grid in British National Grid (#199).

Protected sites within 2 km (#84) and slope within 500 m (#128) are means over a disc
around each home. Polygon intersection per home is far too slow for five million homes,
so these work on a raster: a 50 m grid in EPSG:27700, processed in 100 km blocks with a
halo wide enough for the window, so memory stays at a few blocks of 2,080² cells.

- ``BngGrid``: a grid (row 0 to the north) with ``blocks()`` that yield padded sub-grids.
- ``rasterize_polygons``: burn polygons (overlaps count once).
- ``reproject_to_grid``: resample rasters in any CRS onto the grid (``WarpedVRT``).
- ``box_mean``: a NaN-aware moving mean over the square with the disc's area.
- ``horn_slope_pct``: slope in percent by Horn's method (the Sobel kernel).
- ``focal_at_homes``: run a block function and sample it at the homes, per area.

The tree-cover and night-lights stagers keep their own lon/lat grids; this one is for
data that is in metres or is better resampled once to metres.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
from affine import Affine
from scipy.ndimage import sobel, uniform_filter

BNG = "EPSG:27700"


@dataclass(frozen=True)
class BngGrid:
    """``nrows`` × ``ncols`` cells of ``cell_m`` metres; (x0, y1) is the north-west corner."""

    x0: float
    y1: float
    cell_m: float
    nrows: int
    ncols: int

    @classmethod
    def covering(
        cls, bbox: tuple[float, float, float, float], cell_m: float, margin_m: float = 0.0
    ) -> BngGrid:
        """The grid, aligned to multiples of ``cell_m``, that covers ``bbox`` (x0, y0,
        x1, y1) plus ``margin_m`` on every side."""
        x0, y0, x1, y1 = bbox
        gx0 = math.floor((x0 - margin_m) / cell_m) * cell_m
        gy1 = math.ceil((y1 + margin_m) / cell_m) * cell_m
        gx1 = math.ceil((x1 + margin_m) / cell_m) * cell_m
        gy0 = math.floor((y0 - margin_m) / cell_m) * cell_m
        # One row and column more than the span: a point on the south or east edge
        # (y0, x1 on a cell boundary) falls in the next cell, which must exist
        return cls(
            gx0,
            gy1,
            cell_m,
            int(round((gy1 - gy0) / cell_m)) + 1,
            int(round((gx1 - gx0) / cell_m)) + 1,
        )

    @property
    def transform(self) -> Affine:
        return Affine(self.cell_m, 0.0, self.x0, 0.0, -self.cell_m, self.y1)

    @property
    def shape(self) -> tuple[int, int]:
        return self.nrows, self.ncols

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            self.x0,
            self.y1 - self.nrows * self.cell_m,
            self.x0 + self.ncols * self.cell_m,
            self.y1,
        )

    def rowcol(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rows = np.floor((self.y1 - np.asarray(y, dtype=float)) / self.cell_m).astype(np.int64)
        cols = np.floor((np.asarray(x, dtype=float) - self.x0) / self.cell_m).astype(np.int64)
        return rows, cols

    def sub(self, row0: int, col0: int, nrows: int, ncols: int) -> BngGrid:
        return BngGrid(
            self.x0 + col0 * self.cell_m, self.y1 - row0 * self.cell_m, self.cell_m, nrows, ncols
        )

    def blocks(self, size_m: float = 100_000, halo_m: float = 0.0) -> Iterator[Block]:
        """The grid in square blocks of ``size_m``, each with a padded grid that reaches
        ``halo_m`` further on every side (beyond the grid's own edge too)."""
        step = max(1, round(size_m / self.cell_m))
        halo = math.ceil(halo_m / self.cell_m)
        for r in range(0, self.nrows, step):
            for c in range(0, self.ncols, step):
                core = self.sub(r, c, min(step, self.nrows - r), min(step, self.ncols - c))
                padded = self.sub(r - halo, c - halo, core.nrows + 2 * halo, core.ncols + 2 * halo)
                yield Block(core, padded, halo)


@dataclass(frozen=True)
class Block:
    core: BngGrid
    padded: BngGrid
    halo: int  # cells of padding on each side

    def core_of(self, arr: np.ndarray) -> np.ndarray:
        h = self.halo
        return arr[h : h + self.core.nrows, h : h + self.core.ncols]


def rasterize_polygons(
    geoms: Iterable, grid: BngGrid, all_touched: bool = False, dtype: str = "uint8"
) -> np.ndarray:
    """1 where any polygon covers a cell's centre (or touches it with ``all_touched``),
    else 0. Overlapping polygons are burned once, so no union is needed."""
    from rasterio.features import rasterize

    shapes = [(g, 1) for g in geoms if g is not None and not g.is_empty]
    if not shapes:
        return np.zeros(grid.shape, dtype=dtype)
    return rasterize(
        shapes,
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        all_touched=all_touched,
        dtype=dtype,
    )


def reproject_to_grid(
    paths: Iterable[str | Path],
    grid: BngGrid,
    resampling: str = "bilinear",
    src_crs: str | None = None,
) -> np.ndarray:
    """The rasters in ``paths`` resampled onto ``grid`` (float32, NaN where none covers
    a cell), later files filling only cells still empty. ``resampling`` is a rasterio
    name: ``average`` turns a 10 m share into a 50 m share, ``bilinear`` suits a DEM.
    A path may be a GDAL virtual path (``/vsizip/…``); ``src_crs`` overrides a file
    without one (an ASCII grid without its .prj)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    out = np.full(grid.shape, np.nan, dtype=np.float32)
    for path in paths:
        with rasterio.open(str(path)) as src:
            crs = src.crs or src_crs
            if crs is None:
                raise ValueError(f"{path} has no CRS; pass src_crs")
            if not _overlaps(src, crs, grid):
                continue
            with WarpedVRT(
                src,
                src_crs=crs,
                crs=BNG,
                transform=grid.transform,
                width=grid.ncols,
                height=grid.nrows,
                resampling=getattr(Resampling, resampling),
                src_nodata=src.nodata,
                nodata=np.nan,
                dtype="float32",
            ) as vrt:
                data = vrt.read(1)
        fill = np.isnan(out) & ~np.isnan(data)
        out[fill] = data[fill]
    return out


def _overlaps(src, crs, grid: BngGrid) -> bool:
    from rasterio.warp import transform_bounds

    try:
        x0, y0, x1, y1 = transform_bounds(crs, BNG, *src.bounds)
    except Exception:  # noqa: BLE001 (a projection that fails on the corners: try it)
        return True
    gx0, gy0, gx1, gy1 = grid.bounds
    return x0 < gx1 and x1 > gx0 and y0 < gy1 and y1 > gy0


def window_cells(cell_m: float, radius_m: float) -> int:
    """The odd side, in cells, of the square with the area of a disc of ``radius_m``."""
    side = max(1, round(radius_m * math.sqrt(math.pi) / cell_m))
    return side if side % 2 else side + 1


def box_mean(arr: np.ndarray, cell_m: float, radius_m: float, valid=None) -> np.ndarray:
    """Mean of ``arr`` over the square with the area of a ``radius_m`` disc around each
    cell, over the cells that are ``valid`` (and not NaN): a seafront home is judged on
    the land around it. NaN where no valid cell is in the window."""
    size = window_cells(cell_m, radius_m)
    a = np.asarray(arr, dtype=np.float64)
    ok = ~np.isnan(a)
    if valid is not None:
        ok &= np.asarray(valid, dtype=bool)
    num = uniform_filter(np.where(ok, a, 0.0), size=size, mode="constant")
    den = uniform_filter(ok.astype(np.float64), size=size, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den < 1e-9] = np.nan
    return out


def horn_slope_pct(dem: np.ndarray, cell_m: float) -> np.ndarray:
    """Slope in percent (rise over run × 100) by Horn's method: the Sobel kernel is
    Horn's weighted difference, so dz/dx = sobel_x / (8 × cell). NaN next to no-data."""
    z = np.asarray(dem, dtype=np.float64)
    dzdx = sobel(z, axis=1, mode="nearest") / (8 * cell_m)
    dzdy = sobel(z, axis=0, mode="nearest") / (8 * cell_m)
    return 100.0 * np.hypot(dzdx, dzdy)


def focal_at_homes(
    fn: Callable[[BngGrid], np.ndarray | dict[str, np.ndarray] | None],
    homes: pl.DataFrame,
    cell_m: float = 50.0,
    halo_m: float = 2_000.0,
    block_m: float = 100_000.0,
    by: str = "lsoa21cd",
    xy: tuple[str, str] = ("east1m", "north1m"),
    log: Callable[[str], None] | None = None,
) -> pl.DataFrame:
    """Run ``fn`` on every padded block that holds homes and sample its result at the
    homes in the block's core; the per-area mean over homes with a value, and
    ``n_postcodes`` (the raster stagers' column name). ``fn`` returns an array of the
    padded grid's shape (the column ``value``), a dict of such arrays (one column each;
    ``n_postcodes`` counts the first), or None for a block with nothing to say."""
    homes = homes.filter(pl.col(xy[0]).is_not_null() & pl.col(xy[1]).is_not_null())
    x = homes[xy[0]].to_numpy().astype(float)
    y = homes[xy[1]].to_numpy().astype(float)
    grid = BngGrid.covering((x.min(), y.min(), x.max(), y.max()), cell_m)
    rows, cols = grid.rowcol(x, y)
    values: dict[str, np.ndarray] = {}
    blocks = list(grid.blocks(block_m, halo_m))
    for i, block in enumerate(blocks):
        r0 = round((grid.y1 - block.core.y1) / cell_m)
        c0 = round((block.core.x0 - grid.x0) / cell_m)
        inside = (
            (rows >= r0)
            & (rows < r0 + block.core.nrows)
            & (cols >= c0)
            & (cols < c0 + block.core.ncols)
        )
        if not inside.any():
            continue
        out = fn(block.padded)
        if out is None:
            continue
        arrays = out if isinstance(out, dict) else {"value": out}
        rr, cc = rows[inside] - r0 + block.halo, cols[inside] - c0 + block.halo
        for name, arr in arrays.items():
            values.setdefault(name, np.full(len(x), np.nan))[inside] = arr[rr, cc]
        if log:
            log(f"block {i + 1}/{len(blocks)}: {int(inside.sum()):,} homes")
    if not values:
        values = {"value": np.full(len(x), np.nan)}
    first = next(iter(values))
    per_home = pl.DataFrame({by: homes[by], **values}, nan_to_null=True)
    return (
        per_home.group_by(by)
        .agg(
            *[pl.col(n).mean() for n in values],
            pl.col(first).is_not_null().sum().alias("n_postcodes"),
        )
        .sort(by)
    )
