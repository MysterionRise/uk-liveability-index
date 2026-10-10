"""Rasters → values at points, without loading a national 10m grid into memory.

Tiles are read in ``block_px`` windows, and only the windows that contain points: the
Round 4 noise maps are 57,000 × 65,000 cells, but 1.4 million postcodes touch a few
thousand blocks of them.
"""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window


def sample_tiles_at_points(
    paths: list[Path], x: np.ndarray, y: np.ndarray, block_px: int = 1024
) -> np.ndarray:
    """The raster value under each point, NaN where no tile covers it or the cell is nodata.

    ``x``/``y`` are in the rasters' CRS. A point on the edge of two tiles takes the first
    tile's value. All tiles are expected to share the same cell size and alignment.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    out = np.full(len(x), np.nan, dtype=np.float64)
    for path in paths:
        with rasterio.open(path) as src:
            b = src.bounds
            inside = (x >= b.left) & (x < b.right) & (y > b.bottom) & (y <= b.top)
            idx = np.flatnonzero(inside & np.isnan(out))
            if idx.size == 0:
                continue
            t = src.transform
            cols = np.clip(((x[idx] - t.c) / t.a).astype(np.int64), 0, src.width - 1)
            rows = np.clip(((y[idx] - t.f) / t.e).astype(np.int64), 0, src.height - 1)
            blocks = (rows // block_px) * (src.width // block_px + 2) + cols // block_px
            for key in np.unique(blocks):
                sel = blocks == key
                r0 = int(rows[sel].min() // block_px * block_px)
                c0 = int(cols[sel].min() // block_px * block_px)
                h = min(block_px, src.height - r0)
                w = min(block_px, src.width - c0)
                arr = src.read(1, window=Window(c0, r0, w, h)).astype(np.float64)
                vals = arr[rows[sel] - r0, cols[sel] - c0]
                if src.nodata is not None:
                    vals[vals == src.nodata] = np.nan
                out[idx[sel]] = vals
    return out
