"""Air quality (Defra grids, already population-weighted per LSOA), flood risk, noise,
tree cover and night-time light."""

import polars as pl

from lix_core.paths import data_dir


def pcm(ctx, slug: str) -> pl.DataFrame:
    return ctx.staged(slug).select("lsoa21cd", "value")


def flood_risk(ctx, bands: list[str], prefix: str = "res") -> pl.DataFrame:
    """Share of homes (%) in the given flood likelihood bands.

    ``prefix`` picks the sources: ``res`` rivers and the sea, ``any`` rivers, the sea or
    surface water. Numerator: homes at risk from the staged ``flood`` table (EA counts in
    England, NRW-based estimates in Wales, flagged ``imputed``); denominator: the LSOA's
    dwellings (VOA). LSOAs the table doesn't list have no homes at risk.
    """
    flood = ctx.staged("flood")
    at_risk = flood.select(
        "lsoa21cd",
        pl.sum_horizontal(f"{prefix}_{b}" for b in bands).alias("at_risk"),
        pl.col("estimated") if "estimated" in flood.columns else pl.lit(False).alias("estimated"),
    )
    df = (
        ctx.staged("voa_ctsop")
        .select("lsoa21cd", "dwellings")
        .join(at_risk, on="lsoa21cd", how="left")
    )
    return df.select(
        "lsoa21cd",
        (pl.col("at_risk").fill_null(0) / pl.col("dwellings") * 100).clip(0, 100).alias("value"),
        pl.when(pl.col("estimated").fill_null(False))
        .then(pl.lit("imputed"))
        .otherwise(pl.lit("ok"))
        .alias("quality"),
    )


def percent(ctx, slug: str, column: str) -> pl.DataFrame:
    """A 0–1 share column of a staged LSOA table, as a percentage."""
    return ctx.staged(slug).select("lsoa21cd", (pl.col(column) * 100).alias("value"))


def optional_column(ctx, slug: str, column: str) -> pl.DataFrame:
    """A staged column that may not exist in this build (a manual source not downloaded):
    every area is then ``missing`` rather than the build failing."""
    if not (data_dir("staged") / f"{slug}.parquet").exists():
        return pl.DataFrame(schema={"lsoa21cd": pl.Utf8, "value": pl.Float64})
    return ctx.staged(slug).select("lsoa21cd", pl.col(column).alias("value"))
