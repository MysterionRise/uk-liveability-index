"""HM Land Registry Price Paid → median prices and sales counts per LSOA (England and Wales)."""

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


ROS_FILE = "ros_house_prices.csv"


def _ros_zone_years(raw: pl.DataFrame) -> pl.DataFrame:
    """Registers of Scotland's long table → one row per 2011 Data Zone and calendar year
    with the sales count and the median price (null where sales were under five)."""
    df = raw.filter(
        (pl.col("GeographyType") == "2011 Data Zones")
        & pl.col("Measurement").is_in(["Count", "Median"])
    ).select(
        pl.col("GeographyCode").alias("dz11cd"),
        pl.col("DateCode").cast(pl.Int32, strict=False).alias("year"),
        "Measurement",
        pl.col("Value").cast(pl.Float64, strict=False).alias("value"),
    )
    out = df.pivot(
        on="Measurement", index=["dz11cd", "year"], values="value", aggregate_function="first"
    ).rename({"Count": "sales", "Median": "median"}, strict=False)
    for c in ("sales", "median"):
        if c not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias(c))
    return out.filter(pl.col("year").is_not_null()).sort("dz11cd", "year")


def _ros_summary(zone_years: pl.DataFrame, latest: int) -> pl.DataFrame:
    """Per 2011 zone: the latest year's median and sales, the year before's median, and
    the latest five years pooled (the sales-weighted mean of the annual medians, the
    sales summed)."""
    recent = zone_years.filter(pl.col("year") > latest - 5)
    pooled = recent.group_by("dz11cd").agg(
        pl.col("sales").sum().alias("transaction_count_5y"),
        (
            (pl.col("median") * pl.col("sales")).sum()
            / pl.col("sales").filter(pl.col("median").is_not_null()).sum()
        )
        .fill_nan(None)
        .alias("median_price_5y"),
    )
    this_year = zone_years.filter(pl.col("year") == latest).select(
        "dz11cd",
        pl.col("median").alias("median_price_12m"),
        pl.col("sales").alias("transaction_count_12m"),
    )
    previous = zone_years.filter(pl.col("year") == latest - 1).select(
        "dz11cd", pl.col("median").alias("median_prev")
    )
    return this_year.join(previous, on="dz11cd", how="full", coalesce=True).join(
        pooled, on="dz11cd", how="full", coalesce=True
    )


def _scotland() -> pl.DataFrame | None:
    """Scottish Data Zones from Registers of Scotland's small-area series (annual, on the
    2011 zones, prices suppressed under five sales), brought onto the 2022 zones through
    the postcode crosswalk: medians as population-weighted means, sales apportioned. The
    latest calendar year stands for the 12 months, the latest five pooled for the five
    years. A zone with no price takes its Intermediate Zone's (the population-weighted
    mean of its sibling zones' medians), flagged ``broadcast_msoa``. None until fetched."""
    from lix_pipeline.geo.crosswalk import crosswalk

    path = data_dir("raw") / "ros_house_prices" / ROS_FILE
    if not path.exists():
        logger.warning(f"{path} not fetched: Scottish prices skipped")
        return None
    raw = pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy")
    zone_years = _ros_zone_years(raw)
    latest = int(zone_years["year"].max())
    per_zone = _ros_summary(zone_years, latest)
    weights = pl.read_parquet(data_dir("staged") / "dz11_dz22.parquet")
    prices = crosswalk(
        per_zone, weights, ["median_price_12m", "median_prev", "median_price_5y"], how="mean"
    )
    counts = crosswalk(
        per_zone, weights, ["transaction_count_12m", "transaction_count_5y"], how="sum"
    )
    zones = (
        pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet")
        .filter(pl.col("nation") == "S")
        .select("lsoa21cd", "msoa21cd", "population")
    )
    out = (
        zones.join(prices, on="lsoa21cd", how="left")
        .join(counts, on="lsoa21cd", how="left")
        .with_columns(
            (
                (pl.col("median_price_12m") - pl.col("median_prev")) / pl.col("median_prev") * 100
            ).alias("yoy_change_pct")
        )
    )
    iz = out.group_by("msoa21cd").agg(
        (
            (pl.col("median_price_12m") * pl.col("population")).sum()
            / pl.col("population").filter(pl.col("median_price_12m").is_not_null()).sum()
        )
        .fill_nan(None)
        .alias("iz_median")
    )
    out = (
        out.join(iz, on="msoa21cd", how="left")
        .with_columns(
            pl.when(pl.col("median_price_12m").is_null() & pl.col("iz_median").is_not_null())
            .then(pl.lit("broadcast_msoa"))
            .otherwise(pl.lit("ok"))
            .alias("quality"),
            pl.coalesce("median_price_12m", "iz_median").alias("median_price_12m"),
            pl.lit(date(latest, 12, 31)).alias("as_of"),
        )
        .drop("median_prev", "iz_median", "msoa21cd", "population")
    )
    broadcast = out.filter(pl.col("quality") == "broadcast_msoa").height
    logger.info(
        f"Registers of Scotland {latest}: {out.height:,} Data Zones, "
        f"{out['median_price_12m'].null_count():,} without a price, {broadcast:,} from the "
        f"Intermediate Zone"
    )
    return out
