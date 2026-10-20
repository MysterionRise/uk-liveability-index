"""Builders that read a staged column directly, and imputation from a proxy."""

import numpy as np
import polars as pl


def column(ctx, slug: str, column: str) -> pl.DataFrame:
    """A column of a staged LSOA-level table, as is."""
    return ctx.staged(slug).select("lsoa21cd", pl.col(column).alias("value"))


def deprivation(ctx, column: str) -> pl.DataFrame:
    """A harmonised deprivation column (IoD in England, WIMD in Wales): see stage/deprivation.py."""
    return ctx.staged("deprivation").select("lsoa21cd", pl.col(column).alias("value"))


def iod(ctx, column: str) -> pl.DataFrame:
    """A score from the English Indices of Deprivation 2025."""
    return ctx.staged("iod_2025").select("lsoa21cd", pl.col(column).alias("value"))


def impute_by_proxy(
    df: pl.DataFrame,
    value: str,
    proxy: str,
    impute: pl.Expr,
    bins: int = 40,
    log: bool = True,
) -> pl.Series:
    """Fill ``value`` where ``impute`` is true from a monotone fit on ``proxy``.

    The fit uses rows that aren't imputed and have both columns: the proxy is cut into
    quantile bins, each bin's median value (log1p scale by default) is taken, the
    medians are made monotone in the direction of the overall correlation, and imputed
    rows are interpolated between bin centres. Returns the completed ``value`` series.
    """
    # A predicate on a column some nations lack (Ireland has no police force area) is
    # null there: those rows are neither training nor imputed
    impute = impute.fill_null(False)
    train = df.filter(~impute & pl.col(value).is_not_null() & pl.col(proxy).is_not_null())
    needs = df.select(impute & pl.col(proxy).is_not_null()).to_series().any()
    if train.height < 2 or not needs:
        # Nothing to impute, or no rows to learn from (a build without the proxy's nation)
        return pl.Series(value, df[value].to_numpy().astype(float))
    y = train[value].to_numpy().astype(float)
    x = train[proxy].to_numpy().astype(float)
    if log:
        y = np.log1p(y)

    bins = max(1, min(bins, len(np.unique(x)) - 1))
    edges = np.unique(np.quantile(x, np.linspace(0, 1, bins + 1)))
    which = np.clip(np.searchsorted(edges, x, side="right") - 1, 0, max(len(edges) - 2, 0))
    filled_bins = [b for b in range(max(len(edges) - 1, 1)) if (which == b).any()]
    centres = np.array([np.median(x[which == b]) for b in filled_bins])
    medians = np.array([np.median(y[which == b]) for b in filled_bins])
    rising = np.corrcoef(x, y)[0, 1] >= 0
    medians = np.maximum.accumulate(medians) if rising else np.minimum.accumulate(medians)

    target = df.filter(impute)[proxy].to_numpy().astype(float)
    fitted = np.interp(target, centres, medians)
    if log:
        fitted = np.expm1(fitted)

    filled = df[value].to_numpy().astype(float).copy()
    mask = df.select(impute).to_series().to_numpy()
    filled[mask] = fitted
    return pl.Series(value, filled, nan_to_null=True)


def _points(ctx, slug: str, column: str | None, values: list | None) -> pl.DataFrame:
    df = ctx.staged(slug)
    if column and values is not None:
        df = df.filter(pl.col(column).is_in(values))
    return df


def nearest(
    ctx,
    slug: str,
    column: str | None = None,
    values: list | None = None,
    beyond_cap: float | None = None,
) -> pl.DataFrame:
    """Mean distance from homes to the nearest point of a staged table (metres).

    Homes with nothing within the search cap (50 km) have no distance; ``beyond_cap``
    gives an area with no measurable distance that value instead (an island with no
    emergency department within reach is 50 km from one, not unknown)."""
    access = ctx.access(_points(ctx, slug, column, values), radius_m=1)
    out = access.select("lsoa21cd", pl.col("nearest_m").alias("value"))
    if beyond_cap is not None:
        out = out.with_columns(pl.col("value").fill_null(beyond_cap))
    return out


def access(
    ctx,
    slug: str,
    radius_m: float,
    cap: float,
    column: str | None = None,
    values: list | None = None,
    weight: str | None = None,
    sigma_m: float | None = None,
) -> pl.DataFrame:
    """Distance-decayed, log-capped count (0–1) of a staged table's points near homes."""
    pts = _points(ctx, slug, column, values)
    df = ctx.access(pts, radius_m=radius_m, cap=cap, weight=weight, sigma_m=sigma_m)
    return df.select("lsoa21cd", pl.col("score").alias("value"))


def count_within(
    ctx,
    slug: str,
    radius_m: float,
    column: str | None = None,
    values: list | None = None,
    per_years: float = 1.0,
) -> pl.DataFrame:
    """Mean number of a staged table's points within ``radius_m`` of homes.

    ``per_years`` turns a multi-year record (e.g. 5 years of collisions) into a yearly figure.
    """
    df = ctx.access(_points(ctx, slug, column, values), radius_m=radius_m)
    return df.select("lsoa21cd", (pl.col("count") / per_years).alias("value"))


def density_within(
    ctx,
    slug: str,
    radius_m: float,
    column: str | None = None,
    values: list | None = None,
) -> pl.DataFrame:
    """Mean number of a staged table's points per km² within ``radius_m`` of homes."""
    import math

    km2 = math.pi * radius_m**2 / 1e6
    df = ctx.access(_points(ctx, slug, column, values), radius_m=radius_m)
    return df.select("lsoa21cd", (pl.col("count") / km2).alias("value"))


def share(ctx, slug: str, numerator: list[str], denominator: str) -> pl.DataFrame:
    """sum(numerator columns) / denominator × 100 from a staged LSOA table."""
    df = ctx.staged(slug)
    return df.select(
        "lsoa21cd",
        # The parts and the total can come from different tables or roundings: a share is
        # never over 100
        (pl.sum_horizontal(numerator) / pl.col(denominator) * 100)
        .clip(upper_bound=100)
        .alias("value"),
    )


def msoa(ctx, slug: str, column: str) -> pl.DataFrame:
    """A column of a staged MSOA-level table, copied to each LSOA in the MSOA."""
    from lix_pipeline.geo.joins import broadcast

    df = broadcast(ctx.staged(slug), [column], "msoa", "msoa21cd", geo=ctx.geo)
    return df.rename({column: "value"})


def nearby_max(ctx, slug: str, column: str, radius_m: float) -> pl.DataFrame:
    """Mean over homes of the largest ``column`` among a staged table's points nearby."""
    from lix_pipeline.geo.access import nearby_max as _nearby_max

    return _nearby_max(ctx.origins, ctx.staged(slug), column, radius_m=radius_m)
