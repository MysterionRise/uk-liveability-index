"""Noise contours as polygons (Northern Ireland's and Ireland's maps): the band at each
home, for the noise engine's polygon sources."""

import numpy as np
import shapely

# Above this many polygons, with fewer vertices each than this, a tree query wins
TREE_ABOVE = 1000
SMALL_POLYGON = 500


def band_at_points(polygons, band_column: str, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The highest ``band_column`` value of the polygons each point falls in (NaN where
    none does). ``polygons`` is a GeoDataFrame in the points' CRS.

    Each polygon is prepared once and tested against the points inside its bounding box
    with ``contains_xy`` (no point geometries, an edge index per polygon), which stays
    fast for a noise band drawn as one polygon with millions of vertices, where a tree
    query with a predicate crawls.
    """
    out = np.full(len(x), np.nan)
    if len(polygons) == 0 or len(x) == 0:
        return out
    bands = polygons[band_column].to_numpy().astype(float)
    geoms = polygons.geometry.values
    if len(geoms) > TREE_ABOVE and np.mean(shapely.get_num_coordinates(geoms)) < SMALL_POLYGON:
        # Many small polygons (a radon atlas of grid squares): index them and query the
        # points, which is where a tree with a predicate is quick
        tree = shapely.STRtree(geoms)
        point_idx, poly_idx = tree.query(shapely.points(x, y), predicate="within")
        if point_idx.size:
            np.fmax.at(out, point_idx, bands[poly_idx])
        return out
    bounds = shapely.bounds(geoms)
    for geom, band, (x0, y0, x1, y1) in zip(geoms, bands, bounds, strict=True):
        if np.isnan(band) or geom is None or geom.is_empty:
            continue
        inside_box = np.flatnonzero((x >= x0) & (x <= x1) & (y >= y0) & (y <= y1))
        if inside_box.size == 0:
            continue
        shapely.prepare(geom)
        hit = inside_box[shapely.contains_xy(geom, x[inside_box], y[inside_box])]
        if hit.size:
            out[hit] = np.fmax(out[hit], band)
    return out
