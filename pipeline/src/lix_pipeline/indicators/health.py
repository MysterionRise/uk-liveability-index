"""GP access: distance to the nearest practice and GP capacity of the practices used."""

import polars as pl

# Practices reporting less than this many qualified GP FTE are left out of the ratio:
# they are usually staffed through other organisations, and dividing by ~0 explodes
MIN_QUALIFIED_GP_FTE = 1.0
MIN_PATIENT_COVERAGE = 0.5


def nearest_gp(ctx) -> pl.DataFrame:
    access = ctx.access(ctx.staged("ods_gp"), radius_m=1)
    return access.select("lsoa21cd", pl.col("nearest_m").alias("value"))


def patients_per_gp(ctx) -> pl.DataFrame:
    """Patients per qualified GP FTE, averaged over the practices an LSOA's residents use.

    Each practice's ratio is its registered list (England residents) over its qualified
    GP FTE; an LSOA's value weights those ratios by how many of its residents are
    registered at each practice. Null if less than half its patients are at practices
    with a usable ratio.
    """
    reg = ctx.staged("gp_registrations")
    lists = reg.group_by("practice_code").agg(pl.col("patients").sum().alias("list_size"))
    ratio = (
        lists.join(ctx.staged("gp_workforce"), on="practice_code", how="inner")
        .filter(pl.col("qualified_gp_fte") >= MIN_QUALIFIED_GP_FTE)
        .select("practice_code", (pl.col("list_size") / pl.col("qualified_gp_fte")).alias("ratio"))
    )
    per_lsoa = (
        reg.join(ratio, on="practice_code", how="left")
        .group_by("lsoa21cd")
        .agg(
            (pl.col("ratio") * pl.col("patients")).sum().alias("num"),
            pl.col("patients").filter(pl.col("ratio").is_not_null()).sum().alias("covered"),
            pl.col("patients").sum().alias("total"),
        )
    )
    return per_lsoa.select(
        "lsoa21cd",
        pl.when(pl.col("covered") / pl.col("total") >= MIN_PATIENT_COVERAGE)
        .then(pl.col("num") / pl.col("covered"))
        .alias("value"),
    )


def gp_quality(ctx) -> pl.DataFrame:
    """CQC rating (0–1) of the practices residents use, weighted by registered patients.

    Null if less than half an LSOA's patients are at a practice with a rating.
    """
    cqc = (
        ctx.staged("cqc_locations")
        .filter((pl.col("category") == "GP Practices") & pl.col("rating_score").is_not_null())
        .select(pl.col("ods_code").alias("practice_code"), "rating_score")
        .unique("practice_code")
    )
    per_lsoa = (
        ctx.staged("gp_registrations")
        .join(cqc, on="practice_code", how="left")
        .group_by("lsoa21cd")
        .agg(
            (pl.col("rating_score") * pl.col("patients")).sum().alias("num"),
            pl.col("patients").filter(pl.col("rating_score").is_not_null()).sum().alias("rated"),
            pl.col("patients").sum().alias("total"),
        )
    )
    return per_lsoa.select(
        "lsoa21cd",
        pl.when(pl.col("rated") / pl.col("total") >= MIN_PATIENT_COVERAGE)
        .then(pl.col("num") / pl.col("rated"))
        .alias("value"),
    )


def care_homes(ctx, radius_m: float, cap: float) -> pl.DataFrame:
    """Beds in care homes rated Good or Outstanding near homes (distance-decayed, 0–1)."""
    homes = (
        ctx.staged("cqc_locations")
        .filter(pl.col("care_home") & (pl.col("rating_score") >= 0.75) & (pl.col("beds") > 0))
        .with_columns((pl.col("beds") / 40).alias("weight"))
    )
    access = ctx.access(homes, radius_m=radius_m, cap=cap, weight="weight")
    return access.select("lsoa21cd", pl.col("score").alias("value"))


def gp_list_size(ctx, radius_m: float = 2000.0) -> pl.DataFrame:
    """Registered patients per practice at the practices near homes (distance-weighted
    mean within ``radius_m``, the nearest beyond that). Scotland's and Northern
    Ireland's lists carry the size; England's comes from the patients registered by
    practice; Wales publishes none in a machine-readable file."""
    from lix_pipeline.geo.access import nearby_mean

    gp = ctx.staged("ods_gp")
    if "list_size" not in gp.columns:
        gp = gp.with_columns(pl.lit(None, pl.Int32).alias("list_size"))
    try:
        patients = (
            ctx.staged("gp_registrations")
            .group_by("practice_code")
            .agg(pl.col("patients").sum().alias("_patients"))
        )
        gp = gp.join(patients, on="practice_code", how="left").with_columns(
            pl.coalesce("list_size", "_patients").alias("list_size")
        )
    except FileNotFoundError:
        pass
    gp = gp.filter(pl.col("list_size").is_not_null() & (pl.col("list_size") > 0))
    return nearby_mean(ctx.origins, gp, "list_size", radius_m=radius_m)
