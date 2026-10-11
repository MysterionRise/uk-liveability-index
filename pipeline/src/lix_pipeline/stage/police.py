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
    months = police_months(data_dir("raw") / "police_crime")
    if not months:
        raise FileNotFoundError("No police.uk month folders in data/raw/police_crime")
    files = [str(f) for m in months for f in sorted(m.glob("*-street.csv"))]
    window_start, window_end = months[0].name, months[-1].name
    logger.info(f"{len(months)} months, {window_start} to {window_end}, {len(files)} files")
    con = duckdb.connect()
    con.execute("SET VARIABLE files = ?", [files])
    df = con.execute(
        """
        WITH crimes AS (
            SELECT "LSOA code" AS lsoa21cd, "Crime type" AS crime_type,
                   "Reported by" AS force, "Month" AS month
            FROM read_csv(getvariable('files'), union_by_name = true, all_varchar = true)
            WHERE "Reported by" NOT IN (SELECT unnest(?::VARCHAR[]))
        ),
        force_months AS (
            SELECT force, count(DISTINCT month) AS force_months,
                   min(month) AS first_month, max(month) AS last_month
            FROM crimes GROUP BY force
        )
        SELECT c.lsoa21cd, c.crime_type, c.force, count(*) AS n,
               f.force_months, f.first_month, f.last_month
        FROM crimes c JOIN force_months f USING (force)
        WHERE regexp_matches(c.lsoa21cd, ?)
        GROUP BY ALL
        ORDER BY c.lsoa21cd, c.crime_type, c.force
        """,
        [list(EXCLUDED_FORCES), area_code_regex()],
    ).pl()
    con.close()

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
    ).lazy()
