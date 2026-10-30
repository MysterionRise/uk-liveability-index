"""The 50 m British National Grid helper (#199)."""

import numpy as np
import polars as pl
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from lix_pipeline.geo.focal import (
    BngGrid,
    box_mean,
    focal_at_homes,
    horn_slope_pct,
    rasterize_polygons,
    reproject_to_grid,
    window_cells,
)


def test_grid_covers_aligns_and_blocks_tile_it():
    grid = BngGrid.covering((1_010.0, 2_020.0, 9_990.0, 7_980.0), 50.0, margin_m=100.0)
    assert (grid.x0, grid.y1) == (900.0, 8_100.0)
    assert grid.bounds[2] >= 10_090.0 and grid.bounds[1] <= 1_920.0
    rows, cols = grid.rowcol(np.array([900.0, 949.0]), np.array([8_099.0, 8_051.0]))
    assert rows.tolist() == [0, 0] and cols.tolist() == [0, 0]
    blocks = list(grid.blocks(size_m=2_000, halo_m=150))
    assert sum(b.core.nrows * b.core.ncols for b in blocks) == grid.nrows * grid.ncols
    b = blocks[0]
    assert b.halo == 3 and b.padded.nrows == b.core.nrows + 6
    assert (b.padded.x0, b.padded.y1) == (b.core.x0 - 150, b.core.y1 + 150)


def test_rasterize_burns_overlaps_once():
    grid = BngGrid(0.0, 2_000.0, 50.0, 40, 40)
    burnt = rasterize_polygons([box(0, 1_000, 1_000, 2_000), box(500, 1_500, 1_000, 2_000)], grid)
    assert burnt.sum() == 400 and burnt.max() == 1  # 1 km² of 50 m cells, overlap once


def test_box_mean_is_a_share_over_valid_cells():
    grid = BngGrid(0.0, 4_000.0, 50.0, 80, 80)
    site = rasterize_polygons([box(1_500, 1_500, 2_500, 2_500)], grid).astype(float)
    assert window_cells(50.0, 200.0) == 7
    share = box_mean(site, 50.0, 200.0)
    r, c = grid.rowcol(np.array([2_000.0, 500.0]), np.array([2_000.0, 500.0]))
    assert share[r[0], c[0]] == pytest.approx(1.0) and share[r[1], c[1]] == pytest.approx(0.0)
    # Cells that are not land (the west half here) are left out of the mean
    land = np.ones(grid.shape, dtype=bool)
    land[:, :40] = False
    edge = box_mean(site, 50.0, 400.0, valid=land)
    r, c = grid.rowcol(np.array([2_010.0]), np.array([2_000.0]))
    assert edge[r[0], c[0]] == pytest.approx(1.0)
    assert np.isnan(box_mean(np.full((5, 5), np.nan), 50.0, 50.0)).all()


def test_horn_slope_of_a_tilted_plane():
    cols = np.arange(30, dtype=float)
    dem = np.tile(cols * 50.0 * 0.1, (30, 1))  # rises 10 m per 100 m eastwards
    slope = horn_slope_pct(dem, 50.0)
    assert slope[5:25, 5:25] == pytest.approx(np.full((20, 20), 10.0))
    assert horn_slope_pct(np.full((10, 10), 3.0), 50.0).max() == 0.0


def test_reproject_fills_the_grid_and_leaves_nan_outside(tmp_path):
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=40, width=40, count=1, dtype="float32",
        crs="EPSG:27700", transform=from_origin(0.0, 1_000.0, 25.0, 25.0), nodata=-9999.0,
    ) as dst:  # fmt: skip
        data = np.full((40, 40), 7.0, dtype="float32")
        data[0, 0] = -9999.0
        dst.write(data, 1)
    grid = BngGrid(0.0, 2_000.0, 50.0, 40, 40)  # 2 × 2 km; the raster covers the SW km
    out = reproject_to_grid([path], grid, "average")
    r, c = grid.rowcol(np.array([500.0, 1_500.0]), np.array([500.0, 1_500.0]))
    assert out[r[0], c[0]] == pytest.approx(7.0)
    assert np.isnan(out[r[1], c[1]])


def test_focal_at_homes_samples_each_block_and_averages_per_area():
    homes = pl.DataFrame({
        "lsoa21cd": ["A", "A", "B"],
        "east1m": [1_025.0, 3_025.0, 250_025.0],
        "north1m": [10_000.0, 10_000.0, 10_000.0],
    })  # fmt: skip
    calls = []

    def east_of_cell(g: BngGrid) -> np.ndarray:
        calls.append(g)
        xs = g.x0 + (np.arange(g.ncols) + 0.5) * g.cell_m
        return np.tile(xs, (g.nrows, 1))

    out = focal_at_homes(east_of_cell, homes, cell_m=50.0, halo_m=500.0, block_m=100_000)
    got = dict(zip(out["lsoa21cd"], out["value"]))
    assert got["A"] == pytest.approx(2_025.0) and got["B"] == pytest.approx(250_025.0)
    assert out.filter(pl.col("lsoa21cd") == "A")["n_postcodes"][0] == 2
    assert len(calls) == 2  # the empty blocks between the two areas are never computed
