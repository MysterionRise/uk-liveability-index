"""Road collisions (STATS19): every reported injury collision with its severity and location."""

import polars as pl

from lix_core.codes import in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.safety")

# collision_severity codes
SEVERITY = {"1": "fatal", "2": "serious", "3": "slight"}


# PSNI's district codes → local government districts
PSNI_DISTRICTS = {
    "ANTN": "N09000001",
    "ARBC": "N09000002",
    "BELC": "N09000003",
    "CCGL": "N09000004",
    "DCST": "N09000005",
    "FERO": "N09000006",
    "LISC": "N09000007",
    "MEAN": "N09000008",
    "MIDU": "N09000009",
    "NEMD": "N09000010",
    "ARND": "N09000011",
}


def _psni_collisions() -> pl.DataFrame:
    """Northern Ireland's injury collisions from the PSNI's yearly files (STATS19 stops at
    Great Britain): the same severities, Irish Grid references reprojected."""
    from lix_core.config import load_nations
    from lix_core.geo import to_working

    frames = []
    for folder in sorted(data_dir("raw").glob("psni_collisions_*")):
        for path in sorted(folder.glob("*.csv")):
            raw = pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy")
            raw = raw.rename({c: c.strip().lstrip("\ufeff") for c in raw.columns})
            frames.append(
                raw.select(
                    pl.concat_str(
                        [pl.lit("N"), pl.col("a_year"), pl.lit("-"), pl.col("a_ref")]
                    ).alias("collision_index"),
                    pl.col("a_year").cast(pl.Int16, strict=False).alias("year"),
                    pl.col("a_type").replace_strict(SEVERITY, default=None).alias("severity"),
                    pl.col("a_gd1").cast(pl.Float64, strict=False).alias("x"),
                    pl.col("a_gd2").cast(pl.Float64, strict=False).alias("y"),
                    pl.col("a_District")
                    .replace_strict(PSNI_DISTRICTS, default=None)
                    .alias("lad_cd"),
                )
            )
    if not frames:
        raise FileNotFoundError("No PSNI collision files under data/raw/psni_collisions_*")
    df = pl.concat(frames).filter(pl.col("x").is_not_null() & pl.col("y").is_not_null())
    crs = load_nations().nations["N"].input_crs
    x, y = to_working(df["x"].to_numpy(), df["y"].to_numpy(), crs)
    df = df.with_columns(pl.Series("x", x), pl.Series("y", y))
    logger.info(f"{df.height:,} PSNI collisions, {df['year'].min()}–{df['year'].max()}")
    return df


def stage_stats19() -> pl.LazyFrame:
    """Collisions in the active nations with BNG coordinates (a few without a location dropped)."""
    from lix_core.codes import active_nations

    df = (
        pl.scan_csv(data_dir("raw") / "stats19" / "stats19.csv", infer_schema=False)
        .select(
            "collision_index",
            pl.col("collision_year").cast(pl.Int16).alias("year"),
            pl.col("collision_severity").replace_strict(SEVERITY, default=None).alias("severity"),
            pl.col("location_easting_osgr").cast(pl.Float64, strict=False).alias("x"),
            pl.col("location_northing_osgr").cast(pl.Float64, strict=False).alias("y"),
            pl.col("local_authority_ons_district").alias("lad_cd"),
        )
        .filter(in_scope("lad_cd", "upper") & pl.col("x").is_not_null())
        .collect()
    )
    if "N" in active_nations():
        df = pl.concat([df, _psni_collisions()], how="vertical_relaxed")
    years = df["year"].unique().sort().to_list()
    logger.info(f"{df.height:,} collisions, {years[0]}–{years[-1]}")
    return df.lazy()
