"""Home points: where access, air, noise, tree cover, light and climate are measured from.

One table for every nation, ``data/staged/homes.parquet``: in the UK the live small-user
postcodes of the NSPL (mostly homes, with a count of addresses behind each), in Ireland,
which has no open postcode file, the centroids of Tailte Éireann's dwelling-sized building
footprints (``geo_ie.home_points``). Columns: ``home_id`` (the postcode or the building's
GUID), ``lsoa21cd``, ``east1m``/``north1m`` in the working CRS, ``lat``/``long``,
``nation`` and ``kind`` (postcode | building). ``lix_pipeline.geo.access.
residential_postcodes`` reads it; every stager that samples something at homes goes
through that function, so the two kinds of origin look alike downstream. The method
difference (addresses in the UK, buildings in Ireland) is written up in the methodology.
"""

import polars as pl

from lix_core.codes import active_nations, nation_of
from lix_core.config import load_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.homes")


def postcode_homes(nations: tuple[str, ...]) -> pl.DataFrame:
    """Live small-user postcodes (usrtypind 0: under 25 items of mail a day, i.e. mostly
    residential) of ``nations`` with their coordinates."""
    return (
        pl.scan_parquet(data_dir("staged") / "nspl.parquet")
        .filter(
            pl.col("live")
            & (pl.col("usrtypind") == "0")
            & nation_of("ctry_cd").is_in(list(nations))
            & pl.col("lsoa21cd").is_not_null()
            & pl.col("east1m").is_not_null()
        )
        .select(
            pl.col("postcode").alias("home_id"),
            "lsoa21cd",
            "east1m",
            "north1m",
            "lat",
            "long",
            nation_of("ctry_cd").alias("nation"),
            pl.lit("postcode").alias("kind"),
        )
        .collect()
    )


def stage_homes() -> pl.LazyFrame:
    cfg = load_nations()
    frames = []
    from_postcodes = tuple(
        n for n in active_nations() if cfg.countries[cfg.nations[n].country].postcode_regex
    )
    if from_postcodes:
        frames.append(postcode_homes(from_postcodes))
    if "I" in active_nations():
        from lix_pipeline.stage.geo_ie import home_points

        frames.append(home_points())
    out = pl.concat(frames, how="diagonal_relaxed")
    counts = dict(out.group_by("nation").len().sort("nation").iter_rows())
    logger.info(f"Homes: {out.height:,} points {counts}")
    return out.lazy()
