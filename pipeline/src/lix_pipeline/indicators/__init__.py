"""Build every indicator in config/indicators.yaml into one long table.

    data/indicators/long.parquet: indicator_id, lsoa21cd, value, quality

Each builder (``module:function`` in the catalogue) takes a ``Context`` plus the
indicator's ``params`` and returns ``lsoa21cd, value`` and optionally ``quality``.
The runner gives every indicator a row for each area of the active nations, with
``quality = "missing"`` where there is no value and ``"not_available"`` in nations the
indicator is not built for (see ``IndicatorSpec.coverage``).
"""

import importlib
from functools import cached_property
from pathlib import Path

import polars as pl

from lix_core.codes import active_nations
from lix_core.config import IndicatorSpec, indicator_coverage, load_indicators, load_registry
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_core.quality import QUALITY_LEVELS
from lix_pipeline.geo.access import poi_access, residential_postcodes

logger = setup_logging("indicators")

QUALITY = list(QUALITY_LEVELS)


class Context:
    """Staged inputs shared by builders, loaded once per run."""

    def __init__(self) -> None:
        self._staged: dict[str, pl.DataFrame] = {}

    @cached_property
    def geo(self) -> pl.DataFrame:
        return self.staged("geo_lsoa")

    def staged(self, slug: str) -> pl.DataFrame:
        if slug not in self._staged:
            self._staged[slug] = pl.read_parquet(data_dir("staged") / f"{slug}.parquet")
        return self._staged[slug]

    @cached_property
    def origins(self) -> pl.DataFrame:
        """Residential postcodes: where access is measured from."""
        return residential_postcodes()

    def access(self, pois: pl.DataFrame, radius_m: float, **kwargs) -> pl.DataFrame:
        """Per-LSOA access to ``pois`` (x/y in BNG) from residential postcodes."""
        return poi_access(self.origins, pois, radius_m=radius_m, **kwargs)


def _builder(spec: IndicatorSpec):
    module, func = spec.builder.split(":")
    return getattr(importlib.import_module(f"lix_pipeline.indicators.{module}"), func)


def build_indicator(
    spec: IndicatorSpec, ctx: Context, coverage: list[str] | None = None
) -> pl.DataFrame:
    """One row per area: the builder's value and quality, ``missing`` where it has none,
    and ``not_available`` (value dropped) in nations outside ``coverage``.

    A builder is not run when no active nation is in its coverage (a Northern
    Ireland-only build has no GP registrations table to read): every area is
    ``not_available``.
    """
    if coverage is not None and not set(coverage) & set(active_nations()):
        logger.info(f"{spec.id}: no active nation in its coverage {coverage}; not built")
        return ctx.geo.select(
            pl.lit(spec.id).alias("indicator_id"),
            "lsoa21cd",
            pl.lit(None, pl.Float64).alias("value"),
            pl.lit("not_available").alias("quality"),
        )
    df = _builder(spec)(ctx, **spec.params)
    if df["lsoa21cd"].n_unique() != df.height:
        raise ValueError(f"{spec.id}: builder returned duplicate LSOAs")
    if "quality" not in df.columns:
        df = df.with_columns(pl.lit("ok").alias("quality"))
    geo = ctx.geo.select("lsoa21cd", "nation") if "nation" in ctx.geo.columns else None
    out = (
        (geo if geo is not None else ctx.geo.select("lsoa21cd"))
        .join(df.select("lsoa21cd", "value", "quality"), on="lsoa21cd", how="left")
        # NaN → null first, so the quality flag below sees it as missing
        .with_columns(pl.col("value").cast(pl.Float64).fill_nan(None))
    )
    if geo is not None and coverage is not None:
        outside = ~pl.col("nation").is_in(coverage)
        dropped = out.filter(outside & pl.col("value").is_not_null()).height
        if dropped:
            logger.info(f"{spec.id}: {dropped:,} values outside its coverage {coverage} dropped")
        out = out.with_columns(
            pl.when(outside).then(None).otherwise(pl.col("value")).alias("value"),
            pl.when(outside)
            .then(pl.lit("not_available"))
            .otherwise(pl.col("quality"))
            .alias("quality"),
        )
    out = out.with_columns(
        pl.when(pl.col("value").is_null() & pl.col("quality").ne_missing("not_available"))
        .then(pl.lit("missing"))
        .otherwise(pl.col("quality"))
        .alias("quality"),
        pl.lit(spec.id).alias("indicator_id"),
    )
    return out.select("indicator_id", "lsoa21cd", "value", "quality")


def build_all(only: list[str] | None = None) -> Path:
    """Build indicators (all, or the ``only`` ids) and write data/indicators/long.parquet.

    When rebuilding a subset, the other indicators are kept from the existing file.
    """
    catalogue, registry = load_indicators(), load_registry()
    ctx = Context()
    out_path = data_dir("indicators") / "long.parquet"
    frames = []
    for spec in catalogue.indicators:
        if only and spec.id not in only:
            continue
        df = build_indicator(spec, ctx, indicator_coverage(spec, registry))
        coverage = df["value"].is_not_null().mean()
        flagged = df.filter(pl.col("quality") != "ok").group_by("quality").len().rows()
        logger.info(f"{spec.id:28} coverage {coverage:6.1%}  {dict(flagged) if flagged else ''}")
        frames.append(df)

    long = pl.concat(frames).with_columns(pl.col("quality").cast(pl.Enum(QUALITY)))
    if only and out_path.exists():
        # Keep the other indicators, but only those still in the catalogue
        current = [i.id for i in catalogue.indicators if i.id not in only]
        kept = pl.read_parquet(out_path).filter(pl.col("indicator_id").is_in(current))
        long = pl.concat([kept, long])
    long = long.sort("indicator_id", "lsoa21cd")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    long.write_parquet(out_path)
    logger.info(f"Saved {long['indicator_id'].n_unique()} indicators to {out_path}")
    return out_path
