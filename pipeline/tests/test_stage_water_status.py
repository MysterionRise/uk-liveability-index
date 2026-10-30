import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from lix_pipeline.stage.water_status import class_score, points_along


@pytest.mark.parametrize(
    ("text", "score"),
    [("High", 5), ("Good ecological potential", 4), ("MODERATE", 3), ("MEP", 3), ("PEP", 2),
     ("GEP", 4), ("Bad", 1), ("NO DATA", None), ("Not assessed", None), (None, None)],
)  # fmt: skip
def test_classes_across_publishers(text, score):
    assert class_score(text) == score


def test_lines_and_lake_outlines_become_scored_points():
    from shapely.geometry import MultiLineString

    geoms = gpd.GeoSeries([
        LineString([(0, 0), (1_000, 0)]), box(0, 0, 200, 200), LineString([(0, 5), (50, 5)]),
        MultiLineString([[(0, 500), (500, 500)], [(600, 500), (1_000, 500)]]),
    ])  # fmt: skip
    pts = points_along(geoms, [4, 2, float("nan"), 1])
    # A multi-line river is densified along its parts, not cut to its endpoints
    assert pts.filter(pts["class_score"] == 1).height == 11
    assert set(pts["class_score"].to_list()) == {4, 2, 1}  # the unscored line is left out
    river = pts.filter(pts["class_score"] == 4)
    assert river.height == 11 and river["x"].max() == 1_000  # every 100 m, ends included
    assert pts.filter(pts["class_score"] == 2).height >= 8  # around the lake's 800 m outline
