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
    max_nearest_m: float | None = None,
    scale: float = 1.0,
) -> pl.DataFrame:
    """Mean distance from homes to the nearest point of a staged table (metres, or
    ``scale`` times that: 0.001 for kilometres).

    The search stops at ``max_nearest_m``, else ``beyond_cap``, else 50 km; a home with
    nothing that close counts as ``beyond_cap`` away (the cap itself without one), and an
    area most of whose homes do is marked ``capped`` (#213)."""
    return capped_distance(
        ctx.access, _points(ctx, slug, column, values), beyond_cap, max_nearest_m, scale
    )


# A home with nothing within this distance of it is beyond the search (50 km)
DEFAULT_SEARCH_M = 50_000.0


def capped_distance(
    access_fn,
    points: pl.DataFrame,
    beyond_cap: float | None = None,
    max_nearest_m: float | None = None,
    scale: float = 1.0,
) -> pl.DataFrame:
    """The nearest-distance rule every ``*_distance`` indicator shares (#213): one search
    cap, the value a home beyond it takes, and the ``capped`` flag on an area whose homes
    mostly are. ``beyond_cap`` below the search cap would rank a home with nothing in
    reach nearer than one with a service just inside it."""
    cap = max_nearest_m if max_nearest_m is not None else (beyond_cap or DEFAULT_SEARCH_M)
    if beyond_cap is not None and beyond_cap < cap:
        raise ValueError(f"beyond_cap ({beyond_cap:g} m) is below the search cap ({cap:g} m)")
    access = access_fn(points, radius_m=1, max_nearest_m=cap, beyond_m=beyond_cap)
    return access.select(
        "lsoa21cd",
        (pl.col("nearest_m") * scale).alias("value"),
        pl.when(pl.col("share_capped") > 0.5)
        .then(pl.lit("capped"))
        .otherwise(pl.lit("ok"))
        .alias("quality"),
    )


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
    return sum_within(ctx, slug, radius_m, column=column, values=values, per_km2=True)


def sum_within(
    ctx,
    slug: str,
    radius_m: float,
    weight: str | None = None,
    per_km2: bool = False,
    column: str | None = None,
    values: list | None = None,
    scale: float = 1.0,
) -> pl.DataFrame:
    """Mean over homes of a staged table's points within ``radius_m`` (their ``weight``
    summed when given, per km² with ``per_km2``), times ``scale``; undecayed."""
    from lix_pipeline.geo.access import sum_within as _sum_within

    df = _sum_within(
        ctx.origins, _points(ctx, slug, column, values), radius_m, weight=weight, per_km2=per_km2
    )
    return df.select("lsoa21cd", (pl.col("value") * scale).alias("value"))


def nearest_attr(
    ctx,
    slug: str,
    value: str,
    max_m: float,
    column: str | None = None,
    values: list | None = None,
    min_share: float = 0.5,
) -> pl.DataFrame:
    """Mean over homes of the nearest point's ``value`` within ``max_m``; an area where
    fewer than ``min_share`` of its homes have one has no value (``missing``)."""
    from lix_pipeline.geo.access import nearest_attr as _nearest_attr

    df = _nearest_attr(ctx.origins, _points(ctx, slug, column, values), value, max_m)
    return df.select(
        "lsoa21cd",
        pl.when(pl.col("share_with_any") >= min_share).then(pl.col("value")).alias("value"),
    )


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
