"""Tests for the tree-cover reduction and canopy share, on a tiny WorldCover-like tile."""

import numpy as np
import polars as pl
import rasterio
from rasterio.transform import from_origin

from lix_pipeline.stage.landcover import (
    TREE_COVER,
    WATER,
    BlockGrid,
    blocks_per_degree,
    canopy_share,
    reduce_tile,
    tree_cover_at_homes,
)

RES = 1 / 12000  # WorldCover cell size in degrees


def _tile(path, lon0, lat1, classes):
    arr = np.asarray(classes, dtype=np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(lon0, lat1, RES, RES),
        nodata=0,
    ) as dst:
        dst.write(arr, 1)
    return path


def test_blocks_count_tree_and_land_cells(tmp_path):
    # 40 × 40 cells = 4 × 4 blocks. Column blocks: all trees | half trees | grass | water
    row = [TREE_COVER] * 10 + [TREE_COVER] * 5 + [30] * 5 + [30] * 10 + [WATER] * 10
    tile = _tile(tmp_path / "t.tif", 0.0, 51.0, [row] * 40)
    assert blocks_per_degree(tile) == 1200
    grid = BlockGrid.covering((0.0, 51.0 - 40 * RES, 40 * RES, 51.0), 1200)
    tree = np.zeros((grid.nrows, grid.ncols), dtype=np.uint8)
    land = np.zeros((grid.nrows, grid.ncols), dtype=np.uint8)
    reduce_tile(tile, grid, tree, land, strip_rows=20)
    assert tree[0, :4].tolist() == [100, 50, 0, 0]
    assert land[0, :4].tolist() == [100, 100, 100, 0]
    # A 1-block "radius" gives each block its own share; water blocks have no land
    share = canopy_share(tree, land, grid, radius_m=1.0, lat_mid=51.0)
    assert share[0, :3].tolist() == [100.0, 50.0, 0.0] and np.isnan(share[0, 3])


def test_lsoa_mean_over_homes(tmp_path):
    row = [TREE_COVER] * 20 + [30] * 20
    tile = _tile(tmp_path / "t.tif", 0.0, 51.0, [row] * 40)
    homes = pl.DataFrame(
        {
            "lsoa21cd": ["E01", "E01", "E02", "E03"],
            "long": [5 * RES, 15 * RES, 35 * RES, 5.0],  # the last is off the tile
            "lat": [51.0 - 5 * RES, 51.0 - 15 * RES, 51.0 - 25 * RES, 51.0 - 5 * RES],
        }
    )
    out = tree_cover_at_homes([tile], homes, radius_m=1.0).sort("lsoa21cd")
    assert out["canopy_pct"].to_list()[:2] == [100.0, 0.0]
    assert out["n_postcodes"].to_list() == [2, 1, 0]
    assert out["canopy_pct"][2] is None
