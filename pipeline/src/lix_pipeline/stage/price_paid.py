"""HM Land Registry Price Paid → median prices and sales counts per LSOA (England and Wales)."""

import re
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from lix_core.codes import active_nations, area_code_regex
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.nspl import default_nspl_path, load_nspl

logger = setup_logging("stage.price_paid")

# Price Paid CSV has no headers; these are the official column names
PP_COLUMNS = [
    "transaction_id",
    "price",
    "date_of_transfer",
    "postcode",
    "property_type",
    "old_new",
    "duration",
    "paon",
    "saon",
    "street",
    "locality",
    "town_city",
    "district",
    "county",
    "ppd_category",
    "record_status",
]


def _pp_column_aliases() -> str:
    """SELECT list mapping DuckDB's zero-padded column names to PP_COLUMNS."""
    # DuckDB zero-pads column names based on total column count (column00..column15 for 16 cols)
    pad_width = len(str(len(PP_COLUMNS) - 1))
    return ", ".join(f"column{i:0{pad_width}d} AS {name}" for i, name in enumerate(PP_COLUMNS))


def stage_price_paid(
    raw_path: Path | None = None,
    nspl: pl.LazyFrame | None = None,
    as_of: date | None = None,
) -> pl.LazyFrame:
    """Aggregate Price Paid sales to LSOA medians (active nations) in a single DuckDB pass.

    Only standard market sales (PPD category A) count. Postcodes are matched against
    the full NSPL including terminated postcodes, since older sales use retired ones.

    Args:
        as_of: end of the 12-month window. Defaults to the latest transfer date in the
            data, so results don't shift with the day the pipeline happens to run.
    """
    if raw_path is None:
        raw_path = data_dir("raw") / "price_paid" / "price_paid.csv"
    if nspl is None:
        nspl = load_nspl(default_nspl_path(), live_only=False, nations=None)

    logger.info(f"Reading Price Paid data from {raw_path}")

    lookup = (
        nspl.select("postcode_norm", "lsoa21cd")
        .unique(subset=["postcode_norm"], keep="first")
        .collect()
        .to_arrow()
    )

    con = duckdb.connect()
    con.execute("SET memory_limit = '8GB'")
    con.register("nspl_lookup", lookup)
    # Use parameterised path via DuckDB variable to avoid SQL injection
    con.execute("SET VARIABLE csv_path = ?", [str(raw_path)])

    logger.info("Geocoding and filtering sales via DuckDB (this may take a few minutes)...")
    con.execute(f"""
        CREATE TEMP TABLE sales AS
        WITH pp AS (
            SELECT {_pp_column_aliases()}
            FROM read_csv(getvariable('csv_path'), header=false, all_varchar=true)
            WHERE column15 = 'A' AND column14 = 'A'
        )
        SELECT
            CAST(pp.price AS BIGINT) AS price,
            CAST(strptime(pp.date_of_transfer, '%Y-%m-%d %H:%M') AS DATE) AS date_of_transfer,
            n.lsoa21cd
        FROM pp
        LEFT JOIN nspl_lookup n
            ON n.postcode_norm = upper(regexp_replace(pp.postcode, '\\s', '', 'g'))
    """)

    if as_of is None:
        as_of = con.execute("SELECT max(date_of_transfer) FROM sales").fetchone()[0]
    logger.info(f"Price Paid as-of date: {as_of}")

    matched, total = con.execute(
        """
        SELECT count(lsoa21cd), count(*) FROM sales
        WHERE date_of_transfer > ?::DATE - INTERVAL 24 MONTH
        """,
        [as_of],
    ).fetchone()
    if total:
        pct = matched / total * 100
        msg = f"Price Paid geocoding (last 24 months): {matched:,}/{total:,} matched ({pct:.2f}%)"
        (logger.warning if pct < 99 else logger.info)(msg)

    logger.info("Aggregating to LSOA level...")
    result = con.execute(
        """
        WITH windows AS (
            SELECT
                lsoa21cd,
                price,
                date_of_transfer > ?::DATE - INTERVAL 12 MONTH AS in_12m,
                date_of_transfer <= ?::DATE - INTERVAL 12 MONTH
                    AND date_of_transfer > ?::DATE - INTERVAL 24 MONTH AS in_prev_12m,
                date_of_transfer > ?::DATE - INTERVAL 5 YEAR AS in_5y
            FROM sales
            WHERE regexp_matches(lsoa21cd, ?) AND date_of_transfer <= ?::DATE
        ),
        agg AS (
            SELECT
                lsoa21cd,
                median(price) FILTER (WHERE in_12m) AS median_price_12m,
                count(*) FILTER (WHERE in_12m) AS transaction_count_12m,
                median(price) FILTER (WHERE in_5y) AS median_price_5y,
                count(*) FILTER (WHERE in_5y) AS transaction_count_5y,
                median(price) FILTER (WHERE in_prev_12m) AS median_price_prev_12m
            FROM windows
            GROUP BY lsoa21cd
        )
        SELECT
            lsoa21cd,
            median_price_12m,
            transaction_count_12m,
            median_price_5y,
            transaction_count_5y,
            (median_price_12m - median_price_prev_12m) / median_price_prev_12m * 100
                AS yoy_change_pct,
            ?::DATE AS as_of
        FROM agg
        WHERE transaction_count_5y > 0
        ORDER BY lsoa21cd
        """,
        [as_of, as_of, as_of, as_of, area_code_regex(), as_of, as_of],
    ).fetch_arrow_table()
    con.close()

    df = pl.from_arrow(result)
    logger.info(f"Aggregated to {len(df):,} LSOAs")
    if "S" in active_nations():
        scotland = _scotland()
        if scotland is not None:
            df = pl.concat([df, scotland], how="diagonal_relaxed").sort("lsoa21cd")
    return df.lazy()


def _ros_sheet(reader, pattern: str):
    """A Registers of Scotland sheet whose name matches ``pattern`` (case apart), with its
    header found by the first row that names a code column."""
    name = next((n for n in reader.sheet_names if re.search(pattern, n, re.I)), None)
    if name is None:
        return None
    probe = reader.load_sheet_by_name(name, header_row=None, n_rows=15).to_polars()
    header = next(
        (
            i
            for i, row in enumerate(probe.rows())
            if any(re.search(r"(?i)data ?zone|intermediate|code", str(v) or "") for v in row)
        ),
        0,
    )
    df = reader.load_sheet_by_name(name, header_row=header).to_polars()
    return df.rename({c: " ".join(str(c).split()) for c in df.columns})


def _ros_table(df: pl.DataFrame, code_prefix: str) -> pl.DataFrame:
    """One Registers of Scotland sheet → code, year, sales, median (the columns are found
    by their names, which the workbook may spell in several ways)."""

    def find(*patterns: str) -> str | None:
        for pattern in patterns:
            found = next((c for c in df.columns if re.search(pattern, c, re.I)), None)
            if found:
                return found
        return None

    code = find(r"zone.*code", r"^code$", r"code")
    year = find(r"^year$", r"year")
    sales = find(r"sales", r"number", r"count", r"volume")
    median = find(r"median")
    if not (code and year and sales and median):
        raise ValueError(f"Registers of Scotland sheet lacks columns among {df.columns}")
    return df.select(
        pl.col(code).cast(pl.Utf8).str.strip_chars().alias("code"),
        pl.col(year).cast(pl.Utf8).str.extract(r"(\d{4})").cast(pl.Int32).alias("year"),
        pl.col(sales).cast(pl.Float64, strict=False).alias("sales"),
        pl.col(median).cast(pl.Float64, strict=False).alias("median"),
    ).filter(pl.col("code").str.starts_with(code_prefix) & pl.col("year").is_not_null())


def _scotland() -> pl.DataFrame | None:
    """Scottish Data Zones from Registers of Scotland's small-area statistics (an annual
    workbook downloaded by hand): the latest year's median and sales as the 12-month
    figures, the five latest years pooled (sales-weighted mean of the annual medians) as
    the five-year ones, and the Intermediate Zone's median where a zone's sales are
    suppressed (under five), flagged ``broadcast_msoa``. None when the file is absent."""
    import fastexcel

    folder = data_dir("manual") / "ros_house_prices"
    files = sorted(folder.glob("*.xlsx"))
    if not files:
        logger.warning(f"No Registers of Scotland workbook under {folder}: Scottish prices skipped")
        return None
    reader = fastexcel.read_excel(files[-1])
    dz = _ros_sheet(reader, r"data ?zone")
    iz = _ros_sheet(reader, r"intermediate")
    if dz is None:
        raise ValueError(f"No Data Zone sheet among {reader.sheet_names}")
    zones = _ros_table(dz, "S01")
    latest = int(zones["year"].max())
    recent = zones.filter(pl.col("year") > latest - 5)
    pooled = recent.group_by("code").agg(
        pl.col("sales").sum().alias("transaction_count_5y"),
        (
            (pl.col("median") * pl.col("sales")).sum()
            / pl.col("sales").filter(pl.col("median").is_not_null()).sum()
        ).alias("median_price_5y"),
    )
    this_year = zones.filter(pl.col("year") == latest).select(
        "code",
        pl.col("median").alias("median_price_12m"),
        pl.col("sales").alias("transaction_count_12m"),
    )
    previous = zones.filter(pl.col("year") == latest - 1).select(
        "code", pl.col("median").alias("median_prev")
    )
    out = (
        this_year.join(previous, on="code", how="left")
        .join(pooled, on="code", how="left")
        .with_columns(
            (
                (pl.col("median_price_12m") - pl.col("median_prev")) / pl.col("median_prev") * 100
            ).alias("yoy_change_pct"),
            pl.lit(date(latest, 12, 31)).alias("as_of"),
        )
    )
    if iz is not None:
        lookup = (
            pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet")
            .filter(pl.col("nation") == "S")
            .select(pl.col("lsoa21cd").alias("code"), "msoa21cd")
        )
        iz_latest = (
            _ros_table(iz, "S02")
            .filter(pl.col("year") == latest)
            .select(pl.col("code").alias("msoa21cd"), pl.col("median").alias("iz_median"))
        )
        out = (
            out.join(lookup, on="code", how="left")
            .join(iz_latest, on="msoa21cd", how="left")
            .with_columns(
                pl.when(pl.col("median_price_12m").is_null() & pl.col("iz_median").is_not_null())
                .then(pl.lit("broadcast_msoa"))
                .otherwise(pl.lit("ok"))
                .alias("quality"),
                pl.coalesce("median_price_12m", "iz_median").alias("median_price_12m"),
            )
            .drop("msoa21cd", "iz_median")
        )
    out = out.rename({"code": "lsoa21cd"}).drop("median_prev")
    logger.info(f"Registers of Scotland {latest}: {out.height:,} Data Zones with prices")
    return out
