"""The coastline as points (#130): densified rings, and a land border that is no coast."""

import geopandas as gpd
import numpy as np
import polars as pl
from shapely.geometry import LineString, Polygon

from lix_pipeline.stage.coastline import coast_points, drop_land_border


def test_coast_points_densify_lines_and_polygon_rings():
    island = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
    shore = LineString([(5000, 0), (5000, 350)])
    gdf = gpd.GeoDataFrame(geometry=[island, shore], crs="EPSG:27700")
    pts = coast_points(gdf, "test")
    assert pts.columns == ["x", "y", "source"] and pts["source"].unique().to_list() == ["test"]
    # Every vertex is at most 100 m from the next: 40 round the square (the closing
    # vertex is the first), 5 along the 350 m line (four 87.5 m steps)
    assert pts.height == 40 + 5
    assert pts.filter(pl.col("x") <= 1000).height == 40
    line = pts.filter(pl.col("x") == 5000).sort("y")["y"].to_list()
    assert line == [0.0, 87.5, 175.0, 262.5, 350.0]


def test_a_vertex_near_the_neighbouring_state_is_dropped():
    pts = pl.DataFrame({"x": [0.0, 100.0, 2000.0], "y": [0.0, 0.0, 0.0], "source": ["n"] * 3})
    land = gpd.GeoDataFrame(
        geometry=[Polygon([(-500, -500), (150, -500), (150, 500), (-500, 500)])], crs="EPSG:27700"
    )
    kept = drop_land_border(pts, land, within_m=250)
    assert kept["x"].to_list() == [2000.0]
    assert drop_land_border(pts, land.iloc[:0], within_m=250).height == 3
    np.testing.assert_allclose(drop_land_border(pts.head(0), land).height, 0)
