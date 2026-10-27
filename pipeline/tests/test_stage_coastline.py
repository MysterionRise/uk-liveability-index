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


def test_a_polygon_contributes_its_outer_ring_only():
    """Lough Neagh is a hole in the OSNI outline: a lake's shore is no coast."""
    from shapely.geometry import MultiPolygon

    shell = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    hole = [(400, 400), (600, 400), (600, 600), (400, 600)]
    gdf = gpd.GeoDataFrame(
        geometry=[Polygon(shell, [hole]), MultiPolygon([Polygon(shell, [hole])])],
        crs="EPSG:27700",
    )
    pts = coast_points(gdf, "test")
    assert pts.height == 40  # the two outer rings coincide, their 40 vertices deduplicated
    assert (
        pts.filter((pl.col("x") > 300) & (pl.col("x") < 700) & (pl.col("y") > 300))
        .filter(pl.col("y") < 700)
        .is_empty()
    )


def test_vertices_in_the_lake_basin_box_are_dropped():
    from pyproj import Transformer

    from lix_pipeline.stage.coastline import INLAND_WATER_LONLAT, drop_inland_water

    to_bng = Transformer.from_crs(4326, 27700, always_xy=True)
    # Enniskillen's lough shore (in the box) and Warrenpoint on Carlingford Lough (out)
    xs, ys = to_bng.transform([-7.63, -6.25], [54.35, 54.10])
    pts = pl.DataFrame({"x": list(xs), "y": list(ys), "source": ["osni_ni_outline"] * 2})
    kept = drop_inland_water(pts, INLAND_WATER_LONLAT["osni_ni_outline"])
    assert kept.height == 1 and kept["x"][0] == xs[1]
    assert drop_inland_water(pts.head(0), INLAND_WATER_LONLAT["osni_ni_outline"]).height == 0
