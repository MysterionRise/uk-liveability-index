"""Tests for raster sampling and the noise stager, on tiny GeoTIFFs written with rasterio."""

import numpy as np
import polars as pl
import rasterio
from rasterio.transform import from_origin

from lix_pipeline.geo.rasters import sample_tiles_at_points
from lix_pipeline.stage.noise import noise_at_homes


def _tile(path, origin_x, origin_y, values, nodata=-96.0, res=10.0):
    """A GeoTIFF whose top-left corner is (origin_x, origin_y) in BNG."""
    arr = np.asarray(values, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:27700",
        transform=from_origin(origin_x, origin_y, res, res),
        nodata=nodata,
    ) as dst:
        dst.write(arr, 1)
    return path


class TestSampleTilesAtPoints:
    def test_reads_cells_across_tiles_and_marks_gaps(self, tmp_path):
        # Two 4x4 tiles side by side covering x 0–80, y 0–40; row 0 is the top (y 30–40)
        a = _tile(
            tmp_path / "a.tif",
            0,
            40,
            [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, -96]],
        )
        b = _tile(tmp_path / "b.tif", 40, 40, np.full((4, 4), 60.0))
        x = np.array([5.0, 35.0, 45.0, 35.0, 500.0, 15.0])
        y = np.array([35.0, 5.0, 20.0, 35.0, 35.0, 25.0])
        out = sample_tiles_at_points([a, b], x, y, block_px=2)
        # (5,35) → row 0 col 0 = 1; (35,5) → row 3 col 3 = nodata; (45,20) → tile b = 60;
        # (35,35) → row 0 col 3 = 4; (500,35) → no tile; (15,25) → row 1 col 1 = 6
        assert out[0] == 1 and np.isnan(out[1]) and out[2] == 60 and out[3] == 4
        assert np.isnan(out[4]) and out[5] == 6

    def test_block_windows_do_not_change_values(self, tmp_path):
        values = np.arange(100, dtype=float).reshape(10, 10)
        t = _tile(tmp_path / "t.tif", 0, 100, values)
        x = np.array([5.0, 95.0, 55.0, 25.0])
        y = np.array([95.0, 5.0, 45.0, 75.0])
        whole = sample_tiles_at_points([t], x, y, block_px=1024)
        blocks = sample_tiles_at_points([t], x, y, block_px=3)
        assert whole.tolist() == blocks.tolist() == [0.0, 99.0, 55.0, 22.0]


class TestNoiseAtHomes:
    def test_shares_and_unmapped_homes_are_quiet(self, tmp_path):
        road = _tile(tmp_path / "road.tif", 0, 40, [[70, 70, 0, 0]] * 4)  # x 0–20 loud
        rail = _tile(tmp_path / "rail.tif", 0, 40, [[0, 0, 0, 58]] * 4)  # x 30–40: 55 but not 60
        homes = pl.DataFrame(
            {
                "lsoa21cd": ["E01", "E01", "E01", "E01", "E02"],
                "east1m": [5.0, 15.0, 25.0, 35.0, 900.0],  # the last is off every map
                "north1m": [20.0, 20.0, 20.0, 20.0, 20.0],
            }
        )
        out = noise_at_homes(homes, [road], [rail]).sort("lsoa21cd")
        e01 = out.row(0, named=True)
        assert e01["n_postcodes"] == 4
        assert e01["share_road_60"] == 0.5 and e01["share_rail_60"] == 0.0
        assert e01["share_any_60"] == 0.5
        assert e01["share_any_55"] == 0.75 and e01["share_any_65"] == 0.5
        assert e01["mean_db"] == (70 + 70 + 0 + 58) / 4
        e02 = out.row(1, named=True)
        assert e02["share_any_60"] == 0.0 and e02["mean_db"] == 0.0
