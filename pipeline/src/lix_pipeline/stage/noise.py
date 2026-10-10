"""Road and rail noise at homes: the strategic noise maps sampled at residential postcodes.

England's Round 4 maps (Defra, 2021 traffic) and Wales's 2022 maps were made with the
same modelling system on a 10m grid; each residential postcode takes the Lden value of
the cell it sits in. Cells with no mapped source hold 0 dB (or nodata), so a home on an
unmapped minor road outside an agglomeration reads as quiet.
"""

from pathlib import Path

import numpy as np
import polars as pl

from lix_core.codes import active_nations, nation_of
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes
from lix_pipeline.geo.rasters import sample_tiles_at_points

logger = setup_logging("stage.noise")

# nation → (road Lden slug, rail Lden slug)
NOISE_SOURCES: dict[str, tuple[str, str]] = {
    "E": ("defra_noise_road_lden", "defra_noise_rail_lden"),
    "W": ("wg_noise_road_lden", "wg_noise_rail_lden"),
}
# The Environmental Noise Directive's reporting threshold for Lden
NOISY_DB = 55.0
LOUD_DB = 65.0


def _tiles(slug: str) -> list[Path]:
    return sorted((data_dir("raw") / slug).glob("*.tif"))


def noise_at_homes(homes: pl.DataFrame, road: list[Path], rail: list[Path]) -> pl.DataFrame:
    """Per LSOA: the share of residential postcodes at or above 55 and 65 dB Lden from
    road or rail, each source's share, and the mean of the louder source."""
    x = homes["east1m"].to_numpy().astype(float)
    y = homes["north1m"].to_numpy().astype(float)
    road_db = np.nan_to_num(sample_tiles_at_points(road, x, y), nan=0.0)
    rail_db = np.nan_to_num(sample_tiles_at_points(rail, x, y), nan=0.0)
    louder = pl.max_horizontal("road_db", "rail_db")
    return (
        homes.select("lsoa21cd")
        .with_columns(pl.Series("road_db", road_db), pl.Series("rail_db", rail_db))
        .group_by("lsoa21cd")
        .agg(
            pl.len().alias("n_postcodes"),
            (pl.col("road_db") >= NOISY_DB).mean().alias("share_road_55"),
            (pl.col("rail_db") >= NOISY_DB).mean().alias("share_rail_55"),
            (louder >= NOISY_DB).mean().alias("share_any_55"),
            (louder >= LOUD_DB).mean().alias("share_any_65"),
            louder.mean().alias("mean_db"),
        )
        .sort("lsoa21cd")
    )


def stage_noise() -> pl.LazyFrame:
    """Noise exposure per LSOA for every active nation with a noise map."""
    homes = residential_postcodes()
    frames = []
    for nation, (road, rail) in NOISE_SOURCES.items():
        if nation not in active_nations():
            continue
        pts = homes.filter(nation_of("lsoa21cd") == nation)
        df = noise_at_homes(pts, _tiles(road), _tiles(rail))
        noisy = df.select((pl.col("share_any_55") * pl.col("n_postcodes")).sum()).item()
        logger.info(
            f"{nation}: {df.height:,} LSOAs from {pts.height:,} postcodes; "
            f"{noisy / max(pts.height, 1):.1%} of postcodes at 55 dB Lden or more"
        )
        frames.append(df)
    return pl.concat(frames).sort("lsoa21cd").lazy()
