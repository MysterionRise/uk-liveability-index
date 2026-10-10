"""OS Open UPRN: every property point in the active nations, with its LSOA.

The Environment Agency and NRW count properties at risk by the property points inside
their risk areas; where only the areas are published, these points let the pipeline
count the same way. Great Britain has 40 million; the staged table keeps those that fall
in an active nation's LSOA.
"""

import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.joins import points_to_lsoa

logger = setup_logging("stage.uprn")

CHUNK = 4_000_000


def stage_os_open_uprn() -> pl.LazyFrame:
    path = next((data_dir("raw") / "os_open_uprn").glob("**/*.csv"))
    raw = pl.read_csv(
        path,
        columns=["UPRN", "X_COORDINATE", "Y_COORDINATE"],
        schema_overrides={"UPRN": pl.Int64, "X_COORDINATE": pl.Float64, "Y_COORDINATE": pl.Float64},
    ).rename({"UPRN": "uprn", "X_COORDINATE": "x", "Y_COORDINATE": "y"})
    frames = []
    for start in range(0, raw.height, CHUNK):
        chunk = points_to_lsoa(raw.slice(start, CHUNK)).filter(pl.col("lsoa21cd").is_not_null())
        frames.append(chunk)
        logger.info(f"{min(start + CHUNK, raw.height):,} of {raw.height:,} points placed")
    out = pl.concat(frames).select(
        "uprn", pl.col("x").cast(pl.Int32), pl.col("y").cast(pl.Int32), "lsoa21cd"
    )
    logger.info(f"{out.height:,} property points in the active nations")
    return out.lazy()
