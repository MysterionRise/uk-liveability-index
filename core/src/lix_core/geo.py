"""Coordinate reference systems.

Every stager computes distances in one *working* CRS, named in config/nations.yaml
(British National Grid, EPSG:27700, for the UK). A nation whose published coordinates
use another grid (Northern Ireland's NSPL rows are Irish Grid, EPSG:29903) declares it
as ``input_crs`` and is reprojected on the way in, so nothing downstream needs to know.
"""

from functools import lru_cache

import numpy as np
from pyproj import Transformer

WGS84 = 4326


def working_crs() -> int:
    """The EPSG code every staged coordinate is in (``country.working_crs``)."""
    from lix_core.config import load_nations

    return load_nations().country.working_crs


@lru_cache(maxsize=16)
def transformer(src: int, dst: int) -> Transformer:
    """A cached pyproj transformer taking (x, y) in ``src`` to ``dst``."""
    return Transformer.from_crs(src, dst, always_xy=True)


def transform(x, y, src: int, dst: int) -> tuple[np.ndarray, np.ndarray]:
    """``x``/``y`` (any array-likes) from ``src`` to ``dst``; a no-op when they match."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if src == dst:
        return x, y
    out_x, out_y = transformer(src, dst).transform(x, y)
    return np.asarray(out_x), np.asarray(out_y)


def to_working(x, y, crs: int) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates in ``crs`` → the working CRS."""
    return transform(x, y, crs, working_crs())


def from_working(x, y, crs: int) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates in the working CRS → ``crs``."""
    return transform(x, y, working_crs(), crs)


def lonlat_to_working(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    return transform(lon, lat, WGS84, working_crs())


def working_to_lonlat(x, y) -> tuple[np.ndarray, np.ndarray]:
    return transform(x, y, working_crs(), WGS84)


def bbox_to_working(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """A lon/lat bounding box (w, s, e, n) → the box enclosing it in the working CRS,
    from its four corners and edge midpoints (a projected box is not a rectangle)."""
    w, s, e, n = bbox
    lons = [w, e, w, e, (w + e) / 2, (w + e) / 2, w, e]
    lats = [s, s, n, n, s, n, (s + n) / 2, (s + n) / 2]
    x, y = lonlat_to_working(lons, lats)
    return float(x.min()), float(y.min()), float(x.max()), float(y.max())
