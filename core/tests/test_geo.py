"""The working CRS and the transforms into it."""

import numpy as np
import pytest

from lix_core.geo import (
    WGS84,
    bbox_to_working,
    from_working,
    lonlat_to_working,
    to_working,
    transform,
    working_crs,
    working_to_lonlat,
)

BELFAST = (-5.9301, 54.5973)  # City Hall, lon/lat
IRISH_GRID = 29903


def test_working_crs_is_british_national_grid():
    assert working_crs() == 27700


def test_same_crs_is_a_no_op():
    x, y = transform([1.0, 2.0], [3.0, 4.0], 27700, 27700)
    assert x.tolist() == [1.0, 2.0] and y.tolist() == [3.0, 4.0]


def test_round_trip_through_wgs84():
    x, y = lonlat_to_working([-0.1276], [51.5074])  # Trafalgar Square
    assert 529_000 < x[0] < 531_000 and 180_000 < y[0] < 181_500
    lon, lat = working_to_lonlat(x, y)
    assert lon[0] == pytest.approx(-0.1276, abs=1e-6)
    assert lat[0] == pytest.approx(51.5074, abs=1e-6)


def test_irish_grid_lands_where_the_lonlat_does():
    """A Northern Irish point given on Irish Grid must coincide with the same point given
    as lon/lat once both are in the working CRS: the check the NSPL stage relies on."""
    ig_x, ig_y = transform([BELFAST[0]], [BELFAST[1]], WGS84, IRISH_GRID)
    assert 320_000 < ig_x[0] < 345_000 and 365_000 < ig_y[0] < 385_000  # Irish Grid, as NSPL
    x_from_ig, y_from_ig = to_working(ig_x, ig_y, IRISH_GRID)
    x_from_ll, y_from_ll = lonlat_to_working([BELFAST[0]], [BELFAST[1]])
    assert abs(x_from_ig[0] - x_from_ll[0]) < 2 and abs(y_from_ig[0] - y_from_ll[0]) < 2
    assert x_from_ll[0] < 200_000  # well west of Great Britain's eastings
    back_x, back_y = from_working(x_from_ig, y_from_ig, IRISH_GRID)
    assert back_x[0] == pytest.approx(ig_x[0], abs=0.01)
    assert back_y[0] == pytest.approx(ig_y[0], abs=0.01)


def test_bbox_to_working_encloses_the_corners():
    w, s, e, n = bbox_to_working((-8.2, 54.0, -5.4, 55.4))
    xs, ys = lonlat_to_working([-8.2, -5.4, -8.2, -5.4], [54.0, 54.0, 55.4, 55.4])
    assert w <= xs.min() and e >= xs.max() and s <= ys.min() and n >= ys.max()
    assert isinstance(w, float)


def test_arrays_of_any_kind_are_accepted():
    x, y = lonlat_to_working(np.array([-3.0]), (55.0,))
    assert x.shape == (1,) and y.shape == (1,)
