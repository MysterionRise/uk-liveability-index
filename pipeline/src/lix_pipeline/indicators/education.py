"""Access to good state schools (quality-weighted) and to nurseries."""

import polars as pl

from lix_pipeline.stage.schools import NEUTRAL


def ofsted_quality(ctx) -> pl.DataFrame:
    """Ofsted's inspection quality per school reference; empty when England is not in
    the build (no table to read), so every school counts as ungraded."""
    try:
        return ctx.staged("ofsted_schools").select("urn", "quality")
    except FileNotFoundError:
        urn = ctx.staged("schools")["urn"].dtype
        return pl.DataFrame(schema={"urn": urn, "quality": pl.Float64})


def school_access(ctx, phases: list[str], radius_m: float, cap: float) -> pl.DataFrame:
    """State schools of ``phases`` within ``radius_m``, each weighted by inspection quality.

    Schools not yet inspected count as average (``NEUTRAL``). Independent schools
    are left out: they charge fees and aren't in Ofsted's state-funded data.
    """
    schools = (
        ctx.staged("schools")
        .filter(pl.col("phase").is_in(phases) & (pl.col("type_group") != "Independent schools"))
        .join(ofsted_quality(ctx), on="urn", how="left")
        .with_columns(pl.col("quality").fill_null(NEUTRAL))
    )
    access = ctx.access(schools, radius_m=radius_m, cap=cap, weight="quality")
    return access.select("lsoa21cd", pl.col("score").alias("value"))


def nursery_access(ctx, radius_m: float, cap: float) -> pl.DataFrame:
    """Nurseries and pre-schools (Ofsted in England, OpenStreetMap elsewhere) plus school
    nursery classes, by quality.

    Ofsted settings carry their inspection quality; nursery schools, classes and
    uninspected settings count as average.
    """
    ofsted = ctx.staged("childcare").select("x", "y", "quality")
    gias = (
        ctx.staged("schools")
        .filter(
            (pl.col("phase") == "Nursery") | (pl.col("nursery_provision") == "Has Nursery Classes")
        )
        .select("x", "y", pl.lit(NEUTRAL).alias("quality"))
    )
    pois = pl.concat([ofsted, gias], how="vertical_relaxed")
    access = ctx.access(pois, radius_m=radius_m, cap=cap, weight="quality")
    return access.select("lsoa21cd", pl.col("score").alias("value"))


def school_results(ctx, slug: str, column: str, phases: list[str], radius_m: float) -> pl.DataFrame:
    """Distance-weighted average result of the state mainstream schools near homes."""
    from lix_pipeline.geo.access import nearby_mean

    schools = (
        ctx.staged("schools")
        .filter(
            pl.col("phase").is_in(phases)
            & (pl.col("type_group") != "Independent schools")
            & (pl.col("type_group") != "Special schools")
        )
        .join(ctx.staged(slug).select("urn", column), on="urn", how="inner")
    )
    return nearby_mean(ctx.origins, schools, column, radius_m=radius_m)


# Share of a school's quality that comes from its results (the rest is Ofsted)
RESULTS_WEIGHT = 0.4


def state_schools(ctx, phases: list[str]) -> pl.DataFrame:
    """State mainstream schools of these phases with their Ofsted quality (0–1).

    Schools in nations without inspection grades keep a null quality, so callers can
    treat them as present (choice, access) but ungraded.
    """
    from lix_core.codes import nation_of

    return (
        ctx.staged("schools")
        .filter(
            pl.col("phase").is_in(phases)
            & (pl.col("type_group") != "Independent schools")
            & (pl.col("type_group") != "Special schools")
        )
        .join(ofsted_quality(ctx), on="urn", how="left")
        .with_columns(
            pl.when(nation_of("lsoa21cd") == "E")
            .then(pl.col("quality").fill_null(NEUTRAL))
            .otherwise(pl.col("quality"))
            .alias("quality")
        )
    )


def school_quality_nearby(
    ctx,
    phases: list[str],
    results_slug: str,
    results_column: str,
    k: int,
    sigma_m: float,
    max_m: float,
    la_results_slug: str | None = None,
    la_results_column: str | None = None,
    extra_results: dict[str, list[str]] | None = None,
) -> pl.DataFrame:
    """Quality of the ``k`` nearest state schools, nearer ones counting more.

    Each school's quality blends its Ofsted judgement (0–1) with its results, as a
    percentile among schools of the same phase in the same nation:
    (1 − RESULTS_WEIGHT) × Ofsted + RESULTS_WEIGHT × results. Schools without published
    results use Ofsted alone; schools with results but no inspection grade (Scotland)
    use the results alone. ``extra_results`` names another results table per nation
    (``{"S": ["sg_attainment", "tariff_avg"]}``).

    Nations with no per-school grades or results (Wales) take the local authority's
    attainment instead, as a percentile among that nation's authorities, copied down
    to its areas (quality ``broadcast_lad``) when ``la_results_slug`` names a staged
    table with ``lad_cd`` and ``la_results_column``.
    """
    from lix_core.codes import nation_of
    from lix_pipeline.geo.access import nearest_k_mean

    results = pl.concat(
        [
            ctx.staged(slug).select("urn", pl.col(column).cast(pl.Float64).alias("_result"))
            for slug, column in [(results_slug, results_column), *(extra_results or {}).values()]
        ]
    ).unique("urn", keep="first")
    schools = state_schools(ctx, phases).join(results, on="urn", how="left")
    nation = nation_of("lsoa21cd")
    pct = ((pl.col("_result").rank("average") - 1) / (pl.col("_result").count() - 1).clip(1)).over(
        nation
    )
    schools = schools.with_columns(
        pl.when(pl.col("_result").is_not_null() & pl.col("quality").is_not_null())
        .then((1 - RESULTS_WEIGHT) * pl.col("quality") + RESULTS_WEIGHT * pct)
        .when(pl.col("_result").is_not_null())
        .then(pct)
        .otherwise(pl.col("quality"))
        .alias("combined")
    )
    graded = schools.filter(pl.col("combined").is_not_null())
    out = nearest_k_mean(ctx.origins, graded, "combined", k=k, sigma_m=sigma_m, max_m=max_m)
    if la_results_slug is None:
        return out
    la = ctx.staged(la_results_slug).select("lad_cd", pl.col(la_results_column).alias("_la"))
    la = la.with_columns(
        ((pl.col("_la").rank("average") - 1) / (pl.col("_la").count() - 1)).alias("_la_pct")
    )
    nations = la["lad_cd"].str.slice(0, 1).unique().to_list()
    by_la = (
        ctx.geo.select("lsoa21cd", "lad_cd")
        .filter(nation_of("lsoa21cd").is_in(nations))
        .join(la.select("lad_cd", "_la_pct"), on="lad_cd", how="inner")
        .select(
            "lsoa21cd",
            pl.col("_la_pct").alias("value"),
            pl.lit("broadcast_lad").alias("quality"),
        )
    )
    if "quality" not in out.columns:
        out = out.with_columns(pl.lit("ok").alias("quality"))
    out = out.filter(~nation_of("lsoa21cd").is_in(nations)).select("lsoa21cd", "value", "quality")
    return pl.concat([out, by_la], how="vertical_relaxed")


def school_choice(ctx, cap: float) -> pl.DataFrame:
    """Choice of good state schools: the mean of primary (2km) and secondary (5km) access."""
    primary = school_access(ctx, ["Primary", "All-through", "Middle deemed primary"], 2000, cap)
    secondary = school_access(
        ctx, ["Secondary", "All-through", "Middle deemed secondary"], 5000, cap
    )
    both = primary.join(secondary, on="lsoa21cd", how="full", coalesce=True, suffix="_s")
    return both.select("lsoa21cd", ((pl.col("value") + pl.col("value_s")) / 2).alias("value"))
