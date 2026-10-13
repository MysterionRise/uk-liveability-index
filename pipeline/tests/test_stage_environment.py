"""Flood receptors against SEPA extents (the pure part of stage.environment)."""

import numpy as np
import shapely

from lix_pipeline.stage.environment import mark_touched


def test_mark_touched_flags_the_receptors_inside_not_the_polygon_indices():
    # Receptor 0 sits where the bug would flag it (polygon index 0); only receptor 2 is
    # inside a polygon, and receptor 3 inside the hole of the second one
    receptors = np.array(
        [
            shapely.Point(0, 0),
            shapely.Point(10, 10),
            shapely.Point(105, 105),
            shapely.Point(205, 205),
        ]
    )
    tree = shapely.STRtree(receptors)
    polygons = np.array(
        [
            shapely.box(100, 100, 110, 110),
            shapely.Polygon(
                [(200, 200), (210, 200), (210, 210), (200, 210)],
                holes=[[(204, 204), (206, 204), (206, 206), (204, 206)]],
            ),
        ]
    )
    out = np.zeros(len(receptors))
    mark_touched(out, tree, polygons)
    assert out.tolist() == [0, 0, 1, 0]
    # A multipolygon is split into parts; the same receptors are found
    out2 = np.zeros(len(receptors))
    mark_touched(out2, tree, np.array([shapely.MultiPolygon(list(polygons))]))
    assert out2.tolist() == [0, 0, 1, 0]
