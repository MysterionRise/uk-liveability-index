"""Shares of residents or households from Census 2021 tables."""

import polars as pl


def census_share(ctx, table: str, numerator: list[str], denominator: str = "total") -> pl.DataFrame:
    """sum(numerator columns) / denominator × 100."""
    df = ctx.staged(table)
    return df.select(
        "lsoa21cd",
        # The parts and the total can come from different tables or roundings: a share is
        # never over 100
        (pl.sum_horizontal(numerator) / pl.col(denominator) * 100)
        .clip(upper_bound=100)
        .alias("value"),
    )


def claimant_rate(ctx) -> pl.DataFrame:
    """Claimants as a % of residents aged 16–64 (Census 2021 ages; 16–19 taken as 4/5 of 15–19)."""
    ages = ctx.staged("census_ts007a")
    working = [c for c in ages.columns if _band_in(c, 20, 64)]
    pop = ages.select(
        "lsoa21cd",
        (pl.sum_horizontal(working) + 0.8 * pl.col("aged_15_to_19_years")).alias("pop_16_64"),
    )
    df = ctx.staged("claimant_count").join(pop, on="lsoa21cd")
    return df.select("lsoa21cd", (pl.col("claimants") / pl.col("pop_16_64") * 100).alias("value"))


def _band_in(column: str, lo: int, hi: int) -> bool:
    """Whether a Census age column like 'aged_20_to_24_years' lies within lo–hi."""
    parts = column.split("_")
    if len(parts) != 5 or parts[0] != "aged" or parts[2] != "to":
        return False
    return int(parts[1]) >= lo and int(parts[3]) <= hi
