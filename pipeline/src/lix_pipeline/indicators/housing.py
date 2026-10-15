"""House prices from Land Registry sales, steadied where an LSOA has few sales."""

import polars as pl

from lix_pipeline.geo.joins import broadcast

# Sales-equivalent weight of the MSOA median in the blend (empirical-Bayes style)
PRIOR_SALES = 5


def median_price(ctx) -> pl.DataFrame:
    """Median 12-month price, shrunk towards the MSOA's median when sales are few.

    value = (n × LSOA median + k × MSOA median) / (n + k), with n the LSOA's sales in
    the last 12 months and k = ``PRIOR_SALES``. LSOAs with no sales in 12 months take
    the MSOA median; if that's missing too, their own 5-year median.
    """
    pp = ctx.staged("price_paid").join(
        ctx.geo.select("lsoa21cd", "msoa21cd"), on="lsoa21cd", how="right"
    )
    msoa = pp.group_by("msoa21cd").agg(pl.col("median_price_12m").median().alias("msoa_median"))
    pp = pp.join(msoa, on="msoa21cd", how="left")
    n = pl.col("transaction_count_12m").fill_null(0)
    own = pl.col("median_price_12m")
    blended = (
        pl.when(own.is_not_null() & pl.col("msoa_median").is_not_null())
        .then((n * own + PRIOR_SALES * pl.col("msoa_median")) / (n + PRIOR_SALES))
        .otherwise(pl.coalesce(own, pl.col("msoa_median"), pl.col("median_price_5y")))
    )
    return pp.select(
        "lsoa21cd",
        blended.alias("value"),
        pl.when(n < PRIOR_SALES).then(pl.lit("low_n")).otherwise(pl.lit("ok")).alias("quality"),
    )


def price_to_income(ctx) -> pl.DataFrame:
    """Typical house price over the MSOA's mean net household income (years of income)."""
    price = median_price(ctx).select("lsoa21cd", pl.col("value").alias("price"))
    income = ctx.staged("msoa_income").select("msoa21cd", "income_net")
    df = (
        ctx.geo.select("lsoa21cd", "msoa21cd")
        .join(price, on="lsoa21cd")
        .join(income, on="msoa21cd", how="left")
    )
    return df.select(
        "lsoa21cd",
        (pl.col("price") / pl.col("income_net")).alias("value"),
        pl.lit("broadcast_msoa").alias("quality"),
    )


def msoa_income(ctx, column: str) -> pl.DataFrame:
    """An MSOA income estimate copied to its LSOAs."""
    df = broadcast(ctx.staged("msoa_income"), [column], "msoa", "msoa21cd", geo=ctx.geo)
    return df.rename({column: "value"})


def domestic_rates(ctx) -> pl.DataFrame:
    """Annual domestic rates bill for a home at the area's median capital value: the
    council's district rate plus the regional rate, in the pound (Northern Ireland)."""
    rates = broadcast(ctx.staged("council_tax"), ["rate_poundage"], "lad", "lad_cd", geo=ctx.geo)
    values = ctx.staged("voa_ctsop").select("lsoa21cd", "capital_value_median")
    return rates.join(values, on="lsoa21cd", how="left").select(
        "lsoa21cd",
        (pl.col("rate_poundage") * pl.col("capital_value_median")).round(0).alias("value"),
        "quality",
    )


def council_tax_typical(ctx) -> pl.DataFrame:
    """What a typical home in the area pays: each band's charge weighted by the area's
    stock in that band (the VOA's counts), so it varies within a council."""
    bands = [f"band_{b}" for b in "abcdefgh"]
    charges = broadcast(ctx.staged("council_tax"), bands, "lad", "lad_cd", geo=ctx.geo)
    stock = ctx.staged("voa_ctsop")
    missing = [b for b in bands if b not in stock.columns]
    if missing:
        stock = stock.with_columns([pl.lit(None, pl.Float64).alias(b) for b in missing])
    stock = stock.select("lsoa21cd", *[pl.col(b).alias(f"n_{b}") for b in bands])
    df = charges.join(stock, on="lsoa21cd", how="left")
    total = pl.sum_horizontal([pl.col(f"n_{b}").fill_null(0) for b in bands])
    weighted = pl.sum_horizontal([pl.col(f"n_{b}").fill_null(0) * pl.col(b) for b in bands])
    return df.select(
        "lsoa21cd",
        pl.when(total > 0).then(weighted / total).otherwise(None).round(0).alias("value"),
    )


def council_tax(ctx, band: str = "d") -> pl.DataFrame:
    """Annual council tax for a band in the billing authority (all precepts)."""
    col = f"band_{band}"
    df = broadcast(ctx.staged("council_tax"), [col], "lad", "lad_cd", geo=ctx.geo)
    return df.rename({col: "value"})
