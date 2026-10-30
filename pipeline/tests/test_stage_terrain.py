"""Hilliness and height around homes from tiles on the 50 m grid and from points (#128)."""

import zipfile

import numpy as np
import polars as pl
import pytest
import rasterio
from rasterio.transform import from_origin

from lix_pipeline.geo.focal import BngGrid
from lix_pipeline.stage.terrain import (
    paste_aligned,
    points_to_raster,
    terrain50_index,
    terrain_at_homes,
)


def _asc(path, left, bottom, z):
    """A Terrain 50-style ASCII grid tile of 200 × 200 cells, zipped as OS ships it."""
    tif = path.with_suffix(".asc")
    with rasterio.open(
        tif, "w", driver="AAIGrid", height=200, width=200, count=1, dtype="float32",
        crs="EPSG:27700", transform=from_origin(left, bottom + 10_000, 50.0, 50.0),
    ) as dst:  # fmt: skip
        dst.write(z.astype("float32"), 1)
    with zipfile.ZipFile(path, "w") as zf:
        zf.write(tif, tif.name)
    return path


def test_tiles_paste_cell_for_cell_and_slope_reads_percent(tmp_path):
    folder = tmp_path / "os_terrain_50" / "data" / "su"
    folder.mkdir(parents=True)
    ramp = np.tile(np.arange(200, dtype=float) * 50 * 0.08, (200, 1))  # 8 % up to the east
    _asc(folder / "su10.zip", 410_000, 100_000, ramp)
    _asc(folder / "su11.zip", 410_000, 110_000, np.full((200, 200), 30.0))
    index = terrain50_index(tmp_path / "os_terrain_50")
    assert set(index) == {(41, 10), (41, 11)}
    grid = BngGrid(409_000.0, 112_000.0, 50.0, 80, 80)
    dem = paste_aligned(index, grid)
    r, c = grid.rowcol(
        np.array([409_500.0, 410_025.0, 410_025.0]), np.array([111_000.0, 109_975.0, 110_025.0])
    )
    assert np.isnan(dem[r[0], c[0]])  # west of the tiles
    assert dem[r[1], c[1]] == pytest.approx(0.0) and dem[r[2], c[2]] == pytest.approx(30.0)

    homes = pl.DataFrame({
        "lsoa21cd": ["ramp", "flat"],
        "east1m": [415_000.0, 415_000.0], "north1m": [105_000.0, 115_000.0],
    })  # fmt: skip
    out = dict(
        (r["lsoa21cd"], r) for r in terrain_at_homes(index, [], homes, block_m=20_000).to_dicts()
    )
    assert out["ramp"]["hilliness_pct"] == pytest.approx(8.0, abs=0.1)
    assert out["flat"]["hilliness_pct"] == pytest.approx(0.0, abs=0.1)
    assert out["flat"]["elevation_m"] == pytest.approx(30.0)


def test_points_become_a_raster_on_their_lattice(tmp_path):
    xs, ys = np.meshgrid(np.arange(5) * 50 + 25.0, np.arange(4) * 50 + 25.0)
    pts = pl.DataFrame({"X": xs.ravel(), "Y": ys.ravel(), "Z": (xs + ys).ravel()})
    path = points_to_raster(pts.filter(pl.col("Z") != 100.0), tmp_path / "d.tif", "EPSG:29902")
    with rasterio.open(path) as src:
        assert src.shape == (4, 5) and src.transform.c == 0.0 and src.transform.f == 200.0
        a = src.read(1)
    assert a[3, 0] == pytest.approx(50.0) and np.isnan(a[2, 0])  # the dropped point is a hole
