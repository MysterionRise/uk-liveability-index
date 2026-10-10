"""Night-time light around homes, from the VIIRS annual composite (EOG VNL v2).

The global GeoTIFF (15 arc-second cells, about 460m north–south and 280m east–west over
Britain) is read only over the homes' extent. Each residential postcode takes the mean
radiance of the cells within about 1km, a box the size of the kernel; the LSOA takes the
mean over its postcodes. The source sits behind a free login, so it is a manual download
and the stager raises a clear error when the file is absent.
"""

import math
from pathlib import Path

import numpy as np
import polars as pl
import rasterio
from rasterio.windows import Window, from_bounds
from scipy.ndimage import uniform_filter

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes

logger = setup_logging("stage.night_lights")

M_PER_DEGREE_LAT = 111_320.0
RADIUS_M = 1000.0


def night_lights_file() -> Path | None:
    files = sorted((data_dir("manual") / "viirs_vnl").glob("VNL_*average_masked*.tif"))
    return files[-1] if files else None


def radiance_at_homes(path: Path, homes: pl.DataFrame, radius_m: float = RADIUS_M) -> pl.DataFrame:
    """Per LSOA: mean radiance (nW/cm²/sr) within ``radius_m`` of its postcodes."""
    lon = homes["long"].to_numpy().astype(float)
    lat = homes["lat"].to_numpy().astype(float)
    with rasterio.open(path) as src:
        margin = 2 * radius_m / M_PER_DEGREE_LAT
        wanted = from_bounds(
            lon.min() - margin,
            lat.min() - margin,
            lon.max() + margin,
            lat.max() + margin,
            src.transform,
        )
        # Clip to the raster, or rows and columns computed below would not line up
        window = (
            wanted.round_offsets().round_lengths().intersection(Window(0, 0, src.width, src.height))
        )
        arr = src.read(1, window=window).astype(np.float32)
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
        t = src.window_transform(window)
    lat_mid = (lat.min() + lat.max()) / 2
    size = (
        2 * round(radius_m / (abs(t.e) * M_PER_DEGREE_LAT)) + 1,
        2 * round(radius_m / (t.a * M_PER_DEGREE_LAT * math.cos(math.radians(lat_mid)))) + 1,
    )
    arr = np.nan_to_num(arr, nan=0.0)
    smooth = uniform_filter(arr, size=size, mode="nearest")
    rows = np.floor((lat - t.f) / t.e).astype(np.int64)
    cols = np.floor((lon - t.c) / t.a).astype(np.int64)
    inside = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
    values = np.full(len(lon), np.nan)
    values[inside] = smooth[rows[inside], cols[inside]]
    return (
        homes.select("lsoa21cd")
        .with_columns(pl.Series("radiance", values, nan_to_null=True))
        .group_by("lsoa21cd")
        .agg(pl.col("radiance").mean(), pl.col("radiance").is_not_null().sum().alias("n_postcodes"))
        .sort("lsoa21cd")
    )


def stage_night_lights() -> pl.LazyFrame:
    path = night_lights_file()
    if path is None:
        raise FileNotFoundError(
            "No VIIRS night-lights file in data/manual/viirs_vnl/ (see the registry's instructions)"
        )
    homes = residential_postcodes("lat", "long").filter(
        pl.col("lat").is_not_null() & pl.col("long").is_not_null()
    )
    df = radiance_at_homes(path, homes)
    logger.info(
        f"{df.height:,} LSOAs from {path.name}; median radiance {df['radiance'].median():.2f}"
    )
    return df.lazy()
