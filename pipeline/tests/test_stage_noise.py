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
        out = noise_at_homes(homes, {"road": [road], "rail": [rail]}).sort("lsoa21cd")
        e01 = out.row(0, named=True)
        assert e01["n_postcodes"] == 4
        assert e01["share_road_60"] == 0.5 and e01["share_rail_60"] == 0.0
        assert e01["share_any_60"] == 0.5
        assert e01["share_any_55"] == 0.75 and e01["share_any_65"] == 0.5
        assert e01["mean_db"] == (70 + 70 + 0 + 58) / 4
        e02 = out.row(1, named=True)
        assert e02["share_any_60"] == 0.0 and e02["mean_db"] == 0.0
        # Shares at 55 per kind, and the road-or-rail share (no aircraft layer here)
        assert e01["share_road_55"] == 0.5 and e01["share_rail_55"] == 0.25
        assert e01["share_road_rail_60"] == 0.5

    def test_aircraft_counts_in_any_but_not_in_road_rail(self, tmp_path):
        road = _tile(tmp_path / "road.tif", 0, 40, [[70, 0, 0, 0]] * 4)  # x 0–10 loud
        air = _tile(tmp_path / "air.tif", 0, 40, [[0, 0, 57, 63]] * 4)  # x 20–40 aircraft
        homes = pl.DataFrame(
            {
                "lsoa21cd": ["E01"] * 4,
                "east1m": [5.0, 15.0, 25.0, 35.0],
                "north1m": [20.0] * 4,
            }
        )
        out = noise_at_homes(homes, {"road": [road], "air": [air]}).row(0, named=True)
        assert out["share_air_55"] == 0.5 and out["share_air_60"] == 0.25
        assert out["share_any_60"] == 0.5  # the loud road home and the 63 dB aircraft home
        assert out["share_road_rail_60"] == 0.25  # aircraft left out

    def test_night_shares_follow_the_night_maps(self, tmp_path):
        """Night-time exposure counts from 50 dB Lnight (the Directive's reporting
        threshold at night) over the loudest kind; without night maps the columns are
        null, not zero (#86)."""
        road = _tile(tmp_path / "road.tif", 0, 40, [[70, 70, 0, 0]] * 4)
        road_night = _tile(tmp_path / "road_n.tif", 0, 40, [[52, 48, 0, 0]] * 4)
        rail_night = _tile(tmp_path / "rail_n.tif", 0, 40, [[0, 0, 56, 0]] * 4)
        homes = pl.DataFrame(
            {
                "lsoa21cd": ["E01"] * 4,
                "east1m": [5.0, 15.0, 25.0, 35.0],
                "north1m": [20.0] * 4,
            }
        )
        out = noise_at_homes(
            homes, {"road": [road]}, night_layers={"road": [road_night], "rail": [rail_night]}
        ).row(0, named=True)
        assert out["share_any_night_50"] == 0.5 and out["share_any_night_55"] == 0.25
        assert out["mean_night_db"] == (52 + 48 + 56 + 0) / 4
        assert out["share_any_60"] == 0.5  # the day-time shares are untouched
        quiet = noise_at_homes(homes, {"road": [road]}).row(0, named=True)
        assert quiet["share_any_night_50"] is None and quiet["mean_night_db"] is None

    def test_without_surface_layers_the_road_rail_share_is_null(self, tmp_path):
        every = _tile(tmp_path / "all.tif", 0, 40, [[70, 0, 0, 0]] * 4)
        homes = pl.DataFrame({"lsoa21cd": ["S01"], "east1m": [5.0], "north1m": [20.0]})
        out = noise_at_homes(homes, {"all": [every]}).row(0, named=True)
        assert out["share_all_60"] == 1.0 and out["share_road_rail_60"] is None


class TestContourBands:
    def test_daera_gridcodes_give_the_band_floor_down_to_the_night_maps_lowest(self, tmp_path):
        """DAERA's gridcode is the top of a 5 dB band; the night maps add 49 (45–49.9 dB),
        which stopped the November 2026 refresh when it was unknown."""
        import geopandas as gpd
        from shapely.geometry import box

        from lix_pipeline.stage.noise import contour_db_at_points

        polys = gpd.GeoDataFrame(
            {"gridcode": [49, 54, 1000]},
            geometry=[box(0, 0, 10, 10), box(20, 0, 30, 10), box(40, 0, 50, 10)],
            crs="EPSG:27700",
        )
        path = tmp_path / "R3_con_lngt.gpkg"
        polys.to_file(path, driver="GPKG")
        got = contour_db_at_points([path], np.array([5.0, 25.0, 45.0, 65.0]), np.array([5.0] * 4))
        assert got[:3].tolist() == [45.0, 50.0, 75.0] and np.isnan(got[3])
