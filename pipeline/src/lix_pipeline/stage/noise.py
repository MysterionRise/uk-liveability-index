"""Noise at homes: the strategic noise maps sampled at residential postcodes.

Each nation publishes its Round 4 maps differently: England (Defra, 2021 traffic) a 10m
grid per source, road, rail and aircraft; Wales (2022) road and rail; Scotland one
consolidated grid of every mapped source (road, rail, airports, industry) plus an
industry grid for its five cities.
``NOISE_SOURCES`` lists the grids per nation by kind, and every kind is sampled where the
nation has it; ``share_any_*`` takes the loudest kind at each home, so the scored
indicator means the same everywhere: the share of homes in mapped noise. Cells with no
mapped source hold 0 dB (or nodata), so a home on an unmapped minor road reads as quiet.
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

# nation → kind (road, rail, air, industry, all) → metric → registry slug
NOISE_SOURCES: dict[str, dict[str, dict[str, str]]] = {
    "E": {
        "road": {"lden": "defra_noise_road_lden"},
        "rail": {"lden": "defra_noise_rail_lden"},
        "air": {"lden": "defra_noise_air_lden"},
    },
    "W": {
        "road": {"lden": "wg_noise_road_lden"},
        "rail": {"lden": "wg_noise_rail_lden"},
    },
    "S": {
        "all": {"lden": "sepa_noise_all_lden"},
        "industry": {"lden": "sepa_noise_industry_lden"},
    },
}
KINDS = ("road", "rail", "air", "industry", "all")
# 55 dB Lden is the level at which the Environmental Noise Directive counts people as
# exposed, but at that level the answer depends on whether minor roads were mapped, which
# they are everywhere in Wales and only inside the larger urban areas in England (39% of
# Welsh postcodes pass it against 30% of English ones, most on ordinary streets). At
# 60 dB a home is beside a busy road wherever it is (12% and 14%), so that is the
# cut-off the indicator uses; 55 and 65 are kept.
NOISY_DB = 60.0
REPORTED_DB = 55.0
LOUD_DB = 65.0


def _tiles(slug: str) -> list[Path]:
    return sorted((data_dir("raw") / slug).glob("**/*.tif"))


def noise_at_homes(homes: pl.DataFrame, layers: dict[str, list[Path]]) -> pl.DataFrame:
    """Per area: the share of residential postcodes at or above 60 dB Lden from any mapped
    kind (and at 55 and 65), each kind's own share at 60 and at 55, the share from road
    or rail alone at 60, and the mean of the loudest kind.

    ``layers`` maps a kind (road, rail, air, industry, all) to its grid tiles; a kind with
    no grid counts as 0 dB, like a cell no source reaches. Aircraft and industry are
    reported at 55 dB too: those maps cover whole contours, so the minor-road caveat
    behind the 60 dB cut-off does not apply to them.
    """
    x = homes["east1m"].to_numpy().astype(float)
    y = homes["north1m"].to_numpy().astype(float)
    sampled = {
        kind: np.nan_to_num(sample_tiles_at_points(tiles, x, y), nan=0.0)
        for kind, tiles in layers.items()
        if tiles
    }
    if not sampled:
        raise ValueError("noise_at_homes needs at least one grid")
    db_columns = [pl.Series(f"{kind}_db", values) for kind, values in sampled.items()]
    loudest = pl.max_horizontal([f"{kind}_db" for kind in sampled])
    surface = [k for k in ("road", "rail") if k in sampled]
    road_rail = (
        (pl.max_horizontal([f"{k}_db" for k in surface]) >= NOISY_DB).mean()
        if surface
        else pl.lit(None, pl.Float64)
    )
    return (
        homes.select("lsoa21cd")
        .with_columns(*db_columns)
        .group_by("lsoa21cd")
        .agg(
            pl.len().alias("n_postcodes"),
            *[
                (pl.col(f"{kind}_db") >= NOISY_DB).mean().alias(f"share_{kind}_60")
                for kind in sampled
            ],
            *[
                (pl.col(f"{kind}_db") >= REPORTED_DB).mean().alias(f"share_{kind}_55")
                for kind in sampled
            ],
            road_rail.alias("share_road_rail_60"),
            (loudest >= NOISY_DB).mean().alias("share_any_60"),
            (loudest >= REPORTED_DB).mean().alias("share_any_55"),
            (loudest >= LOUD_DB).mean().alias("share_any_65"),
            loudest.mean().alias("mean_db"),
        )
        .sort("lsoa21cd")
    )


def stage_noise() -> pl.LazyFrame:
    """Noise exposure per area for every active nation with a noise map; each nation's
    kinds appear as ``share_<kind>_60`` (null where a nation has no such grid)."""
    homes = residential_postcodes()
    frames = []
    for nation, kinds in NOISE_SOURCES.items():
        if nation not in active_nations():
            continue
        layers = {kind: _tiles(slugs["lden"]) for kind, slugs in kinds.items()}
        pts = homes.filter(nation_of("lsoa21cd") == nation)
        df = noise_at_homes(pts, layers)
        noisy = df.select((pl.col("share_any_60") * pl.col("n_postcodes")).sum()).item()
        logger.info(
            f"{nation}: {df.height:,} areas from {pts.height:,} postcodes over {sorted(layers)}; "
            f"{noisy / max(pts.height, 1):.1%} of postcodes at 60 dB Lden or more"
        )
        frames.append(df)
    columns = ["lsoa21cd", "n_postcodes", *[f"share_{k}_60" for k in KINDS]]
    columns += [f"share_{k}_55" for k in KINDS]
    columns += ["share_road_rail_60", "share_any_60", "share_any_55", "share_any_65", "mean_db"]
    out = pl.concat(frames, how="diagonal_relaxed").sort("lsoa21cd")
    for column in columns:
        if column not in out.columns:
            out = out.with_columns(pl.lit(None, pl.Float64).alias(column))
    return out.select(columns).lazy()
