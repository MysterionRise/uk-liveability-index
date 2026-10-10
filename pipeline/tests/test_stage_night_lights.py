"""Tests for the night-lights stager on a tiny VIIRS-like raster."""

import numpy as np
import polars as pl
import rasterio
from rasterio.transform import from_origin

from lix_pipeline.stage.night_lights import radiance_at_homes

RES = 15 / 3600  # 15 arc-seconds


def test_mean_radiance_near_homes(tmp_path):
    arr = np.zeros((20, 20), dtype=np.float32)
    arr[5:15, 5:15] = 40.0  # a lit town in the middle, dark countryside around it
    path = tmp_path / "VNL_npp_2024_global.average_masked.dat.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=20, width=20, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(-1.0, 52.0, RES, RES), nodata=-999,
    ) as dst:  # fmt: skip
        dst.write(arr, 1)
    homes = pl.DataFrame(
        {
            "lsoa21cd": ["town", "country", "country"],
            "long": [-1.0 + 10 * RES, -1.0 + 1 * RES, -1.0 + 18 * RES],
            "lat": [52.0 - 10 * RES, 52.0 - 1 * RES, 52.0 - 18 * RES],
        }
    )
    out = radiance_at_homes(path, homes, radius_m=100.0).sort("lsoa21cd")  # kernel of one cell
    assert out["lsoa21cd"].to_list() == ["country", "town"]
    assert out["radiance"].to_list() == [0.0, 40.0]
    # A wide kernel blurs the town's edge into the countryside and the town below 40
    wide = radiance_at_homes(path, homes, radius_m=2500.0).sort("lsoa21cd")
    assert 0.0 <= wide["radiance"][0] < 40.0
    assert 0.0 < wide["radiance"][1] < 40.0
