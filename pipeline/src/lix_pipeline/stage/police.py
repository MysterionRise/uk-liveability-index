"""police.uk street-level crime → counts per LSOA, crime type and force.

The archive holds the latest 36 months, but forces don't all report every month
(Gloucestershire stopped in January 2026; Greater Manchester Police hasn't reported
since 2019). Each row therefore carries ``force_months``, the number of months its
force reported, so rates can be annualised fairly. British Transport Police records
crime on the railway rather than where people live, so it is excluded.
"""

import re
from pathlib import Path

import duckdb
import polars as pl

from lix_core.codes import area_code_regex
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.police")

EXCLUDED_FORCES = ("British Transport Police",)
WINDOW_MONTHS = 36


def police_months(root: Path, window: int = WINDOW_MONTHS) -> list[Path]:
    """The latest ``window`` month folders (YYYY-MM) of the archive, so a folder left
    behind by an earlier download does not stretch the window."""
    months = sorted(
        p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}", p.name)
    )
    return months[-window:]


def stage_police() -> pl.LazyFrame:
    from lix_core.codes import active_nations

    frames = []
    if any(n in active_nations() for n in ("E", "W", "S", "N")):
        frames.append(_police_uk())
    if "I" in active_nations():
        from lix_pipeline.stage.garda import garda_crime

        frames.append(garda_crime())
    return pl.concat(frames, how="diagonal_relaxed").lazy()


def _police_uk() -> pl.DataFrame:
    months = police_months(data_dir("raw") / "police_crime")
    if not months:
        raise FileNotFoundError("No police.uk month folders in data/raw/police_crime")
    files = [str(f) for m in months for f in sorted(m.glob("*-street.csv"))]
    window_start, window_end = months[0].name, months[-1].name
    logger.info(f"{len(months)} months, {window_start} to {window_end}, {len(files)} files")
    con = duckdb.connect()
    con.execute("SET VARIABLE files = ?", [files])
    crimes = """
        SELECT "LSOA code" AS lsoa21cd, "Crime type" AS crime_type,
               "Reported by" AS force, "Month" AS month,
               try_cast("Longitude" AS DOUBLE) AS lon, try_cast("Latitude" AS DOUBLE) AS lat
        FROM read_csv(getvariable('files'), union_by_name = true, all_varchar = true)
        WHERE "Reported by" NOT IN (SELECT unnest(?::VARCHAR[]))
    """
    excluded = list(EXCLUDED_FORCES)
    # Months each force reported, over every record whether or not it names an LSOA
    forces = con.execute(
        f"""
        SELECT force, count(DISTINCT month) AS force_months,
               min(month) AS first_month, max(month) AS last_month
        FROM ({crimes}) GROUP BY force
        """,
        [excluded],
    ).pl()
    located = con.execute(
        f"""
        SELECT lsoa21cd, crime_type, force, count(*) AS n
        FROM ({crimes}) WHERE regexp_matches(lsoa21cd, ?)
        GROUP BY ALL
        """,
        [excluded, area_code_regex()],
    ).pl()
    # The Police Service of Northern Ireland's records carry no LSOA code: each is placed
    # by its coordinates (every point is snapped to a street, so this is as exact as the
    # British rows)
    points = con.execute(
        f"""
        SELECT lon, lat, crime_type, force, count(*) AS n
        FROM ({crimes})
        WHERE (lsoa21cd IS NULL OR lsoa21cd = '') AND lon IS NOT NULL AND lat IS NOT NULL
        GROUP BY ALL
        """,
        [excluded],
    ).pl()
    con.close()
    if points.height:
        from lix_pipeline.geo.joins import points_to_lsoa

        placed = (
            points_to_lsoa(points, x="lon", y="lat", crs=4326)
            .filter(pl.col("lsoa21cd").is_not_null())
            .group_by("lsoa21cd", "crime_type", "force")
            .agg(pl.col("n").sum())
        )
        logger.info(
            f"{placed['n'].sum():,} of {points['n'].sum():,} records without an LSOA code "
            f"placed by their coordinates ({', '.join(sorted(placed['force'].unique()))})"
        )
        located = pl.concat([located, placed], how="vertical_relaxed")
    df = (
        located.join(forces, on="force", how="left")
        .with_columns(pl.col("n").cast(pl.Int64))
        .sort("lsoa21cd", "crime_type", "force")
    )

    short = (
        df.select("force", "force_months", "last_month")
        .unique()
        .filter(pl.col("force_months") < pl.col("force_months").max())
    )
    for row in short.iter_rows(named=True):
        logger.warning(
            f"{row['force']} reported only {row['force_months']} months (last {row['last_month']})"
        )
    logger.info(f"{df['n'].sum():,} crimes in {df['lsoa21cd'].n_unique():,} LSOAs")
    return df.with_columns(
        pl.col("n").cast(pl.Int32),
        pl.col("force_months").cast(pl.Int16),
        pl.lit(window_start).alias("window_start"),
        pl.lit(window_end).alias("window_end"),
    )
