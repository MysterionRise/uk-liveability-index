"""Noise contours as polygons (Northern Ireland's and Ireland's maps): the band at each
home, for the noise engine's polygon sources."""

import numpy as np
import shapely


def band_at_points(polygons, band_column: str, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The highest ``band_column`` value of the polygons each point falls in (NaN where
    none does). ``polygons`` is a GeoDataFrame in the points' CRS."""
    out = np.full(len(x), np.nan)
    if len(polygons) == 0 or len(x) == 0:
        return out
    tree = shapely.STRtree(polygons.geometry.values)
    points = shapely.points(x, y)
    point_idx, poly_idx = tree.query(points, predicate="within")
    bands = polygons[band_column].to_numpy().astype(float)[poly_idx]
    np.maximum.at(out, point_idx, np.where(np.isnan(out[point_idx]), bands, bands))
    # maximum.at leaves NaN where it started NaN: seed those first
    first = np.full(len(x), np.nan)
    first[point_idx] = bands
    out = np.where(np.isnan(out), first, out)
    return out
