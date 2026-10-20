"""Crime rates per 1,000 residents a year from police.uk, with Greater Manchester imputed."""

import polars as pl

from lix_pipeline.indicators.common import impute_by_proxy

# Police force area code for Greater Manchester (no police.uk data since 2019)
GREATER_MANCHESTER = "E23000005"


def _iod_crime_scores(ctx) -> pl.DataFrame:
    """IoD 2025's crime domain for the Greater Manchester imputation; empty in a build
    without England (the table is not staged there)."""
    try:
        return ctx.staged("iod_2025").select("lsoa21cd", "crime_score")
    except FileNotFoundError:
        return pl.DataFrame(schema={"lsoa21cd": pl.Utf8, "crime_score": pl.Float64})


def crime_rate(ctx, types: list[str]) -> pl.DataFrame:
    """Annualised crimes of ``types`` per 1,000 residents.

    Each force's count is divided by the months it actually reported, so a force with
    gaps isn't made to look safe. LSOAs with no recorded crime of these types get 0.
    Greater Manchester LSOAs are estimated from IoD 2025's crime domain score (the
    handful of police.uk records there come from neighbouring forces' border points).
    """
    crimes = ctx.staged("police_crime").filter(pl.col("crime_type").is_in(types))
    # Ireland's rows say when a station's counts are too few to trust (#143)
    low = pl.col("low_n").fill_null(False).any() if "low_n" in crimes.columns else pl.lit(False)
    annual = crimes.group_by("lsoa21cd").agg(
        (pl.col("n") / pl.col("force_months") * 12).sum().alias("annual"), low.alias("low_n")
    )
    # The IoD crime domain (England only) is just the proxy for Greater Manchester, so it
    # must not decide which areas get a rate: Welsh areas have no proxy and need none
    df = (
        ctx.geo.select("lsoa21cd", "population", "pfa_cd")
        .join(annual, on="lsoa21cd", how="left")
        .join(_iod_crime_scores(ctx), on="lsoa21cd", how="left")
    )
    gmp = pl.col("pfa_cd") == GREATER_MANCHESTER
    df = df.with_columns(
        pl.when(gmp)
        .then(None)
        .otherwise(pl.col("annual").fill_null(0) / pl.col("population") * 1000)
        .alias("value")
    )
    df = df.with_columns(impute_by_proxy(df, "value", "crime_score", gmp))
    return df.select(
        "lsoa21cd",
        "value",
        pl.when(gmp)
        .then(pl.lit("imputed"))
        .when(pl.col("low_n").fill_null(False))
        .then(pl.lit("low_n"))
        .otherwise(pl.lit("ok"))
        .alias("quality"),
    )
