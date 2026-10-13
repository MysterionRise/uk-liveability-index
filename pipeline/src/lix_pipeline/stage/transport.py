"""DfT Transport Connectivity Metric → 0–100 scores per LSOA.

The LSOA sheet has two title rows above the header ("LSOA21CD", "Employment
(walking)", ..., "Overall"). Scores are relative within England and Wales, not
travel times; DfT advises comparing places of the same urban/rural type.
"""

import re
from datetime import date, datetime, timedelta

import fastexcel
import polars as pl

from lix_core.codes import in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.transport")


def tcm_column(label: str) -> str:
    """'Employment (walking)' → 'tcm_employment_walking'; 'Overall' → 'tcm_overall'."""
    name = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return f"tcm_{name}"


def stage_dft_connectivity() -> pl.LazyFrame:
    reader = fastexcel.read_excel(data_dir("raw") / "dft_connectivity" / "dft_connectivity.ods")
    sheet = reader.load_sheet_by_name("LSOA", header_row=2).to_polars()
    code_col = next(c for c in sheet.columns if c.upper().startswith("LSOA"))
    df = (
        sheet.rename(
            {code_col: "lsoa21cd", **{c: tcm_column(c) for c in sheet.columns if c != code_col}}
        )
        .filter(in_scope("lsoa21cd"))
        .with_columns(pl.exclude("lsoa21cd").cast(pl.Float64, strict=False))
    )
    logger.info(f"{df.height:,} LSOAs, {df.width - 1} connectivity scores")
    return df.lazy()


# NaPTAN stop types → mode. Rail: station access area, entrance, platform; metro/tram:
# access area, platform, entrance; bus: on-street stop, bus station bay, coach bay
NAPTAN_MODES = {
    "RLY": "rail",
    "RSE": "rail",
    "RPL": "rail",
    "MET": "metro_tram",
    "PLT": "metro_tram",
    "TMU": "metro_tram",
    "BCT": "bus",
    "BCS": "bus",
    "BCQ": "bus",
    "FER": "ferry",
    "FBT": "ferry",
}


def stage_naptan() -> pl.LazyFrame:
    """Active public transport stops in Great Britain with their mode and BNG location."""
    df = (
        pl.scan_csv(data_dir("raw") / "naptan" / "naptan.csv", infer_schema=False)
        .filter(pl.col("Status") == "active")
        .select(
            pl.col("ATCOCode").alias("atco_code"),
            pl.col("CommonName").alias("name"),
            pl.col("LocalityName").alias("locality"),
            pl.col("StopType").alias("stop_type"),
            pl.col("StopType").replace_strict(NAPTAN_MODES, default=None).alias("mode"),
            pl.col("Easting").cast(pl.Float64, strict=False).alias("x"),
            pl.col("Northing").cast(pl.Float64, strict=False).alias("y"),
        )
        .filter(pl.col("mode").is_not_null() & pl.col("x").is_not_null())
        .collect()
    )
    logger.info(f"{df.height:,} active stops: {dict(df['mode'].value_counts().rows())}")
    return df.lazy()


# Ofcom output-area columns (residential premises) → our names
OFCOM_COUNTS = {
    "All Premises": "premises",
    "Number of premises with Gigabit availability": "gigabit",
    "Number of premises with SFBB availability": "superfast",
    "Number of premises unable to receive decent broadband from fixed or FWA": "below_uso",
}


def stage_ofcom_broadband() -> pl.LazyFrame:
    """Residential premises able to get gigabit / superfast broadband, summed to LSOA.

    Superfast is 30 Mbit/s or more; "below USO" is premises that can't get a decent
    connection (10 Mbit/s down, 1 up) from fixed lines or fixed wireless.
    """
    from lix_pipeline.geo.joins import oa_to_lsoa

    path = next((data_dir("raw") / "ofcom_broadband").glob("**/*fixed_oa_res_coverage*.csv"))
    raw = pl.read_csv(path, infer_schema=False)
    df = raw.select(
        pl.col("output_area").alias("oa21cd"),
        *[
            pl.col(src).cast(pl.Float64, strict=False).alias(dst)
            for src, dst in OFCOM_COUNTS.items()
        ],
    ).filter(in_scope("oa21cd", "oa"))
    per_lsoa = oa_to_lsoa(df, list(OFCOM_COUNTS.values())).with_columns(
        (pl.col(c) / pl.col("premises") * 100).alias(f"{c}_pct")
        for c in ("gigabit", "superfast", "below_uso")
    )
    logger.info(
        f"{per_lsoa.height:,} LSOAs; gigabit available to "
        f"{per_lsoa['gigabit'].sum() / per_lsoa['premises'].sum():.1%} of homes"
    )
    return per_lsoa.lazy()


DAYTIME = ("07:00:00", "19:00:00")  # 12 hours


def reference_tuesday(start: date, end: date) -> date:
    """A normal Tuesday about two weeks into the timetable (clear of bank holidays)."""
    day = start + timedelta(days=14)
    while day.weekday() != 1 or (day.month, day.day) in {(12, 25), (12, 26), (1, 1)}:
        day += timedelta(days=1)
    return min(day, end)


def gtfs_feeds() -> list[str]:
    """Registry slugs ``bods_gtfs*`` that cover an active nation and have been fetched."""
    from lix_core.codes import active_nations
    from lix_core.config import load_registry

    active = set(active_nations())
    return [
        slug
        for slug, spec in load_registry().items()
        if slug.startswith("bods_gtfs")
        and active & set(spec.coverage)
        and (data_dir("raw") / slug / "stops.txt").exists()
    ]


def stage_bods_gtfs() -> pl.LazyFrame:
    """Weekday daytime bus departures per stop from the GTFS timetables in scope.

    Trips running on a reference Tuesday (calendar plus calendar_dates exceptions), with
    departures between 07:00 and 19:00 where passengers can board. Feeds overlap at
    borders; a stop in several feeds keeps its busiest count.
    """
    feeds = gtfs_feeds()
    if not feeds:
        raise FileNotFoundError("No GTFS feed fetched for the active nations")
    frames = [_gtfs_departures(data_dir("raw") / slug) for slug in feeds]
    df = (
        pl.concat(frames)
        .sort("departures", descending=True)
        .unique("stop_id", keep="first")
        .sort("stop_id")
    )
    logger.info(f"{df.height:,} stops served across {feeds}")
    return df.lazy()


def _gtfs_departures(root) -> pl.DataFrame:
    import duckdb

    from lix_pipeline.stage.geo import lonlat_to_bng

    con = duckdb.connect()
    cal = f"read_csv('{root / 'calendar.txt'}', all_varchar=true)"
    dates = f"read_csv('{root / 'calendar_dates.txt'}', all_varchar=true)"
    start, end = con.execute(f"SELECT min(start_date), max(end_date) FROM {cal}").fetchone()
    day = reference_tuesday(
        datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
    )
    d = day.strftime("%Y%m%d")
    sql = f"""
    WITH active AS (
        SELECT service_id FROM {cal}
        WHERE tuesday = '1' AND start_date <= '{d}' AND end_date >= '{d}'
        UNION SELECT service_id FROM {dates} WHERE date = '{d}' AND exception_type = '1'
        EXCEPT SELECT service_id FROM {dates} WHERE date = '{d}' AND exception_type = '2'
    ),
    trips AS (
        SELECT trip_id FROM read_csv('{root / "trips.txt"}', all_varchar=true)
        WHERE service_id IN (SELECT service_id FROM active)
    )
    SELECT st.stop_id, count(*) AS departures
    FROM read_csv('{root / "stop_times.txt"}', all_varchar=true) st
    JOIN trips USING (trip_id)
    WHERE st.departure_time >= '{DAYTIME[0]}' AND st.departure_time < '{DAYTIME[1]}'
      AND coalesce(st.pickup_type, '0') != '1'
    GROUP BY st.stop_id
    """
    counts = con.execute(sql).pl()
    stops = pl.read_csv(root / "stops.txt", infer_schema=False).select(
        "stop_id",
        pl.col("stop_name").alias("name"),
        pl.col("stop_lon").cast(pl.Float64).alias("lon"),
        pl.col("stop_lat").cast(pl.Float64).alias("lat"),
    )
    df = stops.join(counts, on="stop_id", how="inner").with_columns(
        (pl.col("departures") / 12).alias("per_hour"), pl.lit(day).alias("reference_day")
    )
    x, y = lonlat_to_bng(df["lon"], df["lat"])
    df = df.with_columns(x=x, y=y)
    logger.info(
        f"{root.name}: {df.height:,} stops served on {day:%a %d %b %Y}; "
        f"median {df['per_hour'].median():.1f} departures an hour (07:00–19:00)"
    )
    return df


def _orr_sheet(reader, sheet: str, first_col: str) -> pl.DataFrame:
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=12).to_polars()
    first = probe[probe.columns[0]].to_list()
    header = next(i for i, v in enumerate(first) if v and str(v).strip() == first_col)
    df = reader.load_sheet_by_name(sheet, header_row=header).to_polars()
    return df.rename({c: " ".join(c.split()) for c in df.columns})


def stage_orr_stations() -> pl.LazyFrame:
    """Mainline stations with a year's passenger entries and exits (ORR Table 1410), placed
    by the grid reference in ORR's station attributes (Table 6329); joined on the national
    location code. ``entries_exits_m`` is in millions, the weight the rail service
    indicator uses."""
    import fastexcel

    usage = pl.read_csv(
        data_dir("raw") / "orr_station_usage" / "orr_station_usage.csv",
        skip_rows=3,
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    usage = usage.rename({c: " ".join(c.split()) for c in usage.columns})
    total = next(c for c in usage.columns if c.startswith("Entries and exits: All"))
    nlc = next(c for c in usage.columns if c.startswith("National Location Code"))
    usage = usage.select(
        pl.col("Station name").alias("station"),
        pl.col(nlc).str.strip_chars().alias("nlc"),
        pl.col(total)
        .str.replace_all(",", "")
        .cast(pl.Float64, strict=False)
        .alias("entries_exits"),
    ).filter(pl.col("nlc").is_not_null() & pl.col("entries_exits").is_not_null())
    reader = fastexcel.read_excel(
        data_dir("raw") / "orr_station_attributes" / "orr_station_attributes.ods"
    )
    sheet = next(n for n in reader.sheet_names if "attributes" in n.lower())
    attrs = _orr_sheet(reader, sheet, "Station name")
    nlc_a = next(c for c in attrs.columns if c.startswith("National Location Code"))
    east = next(c for c in attrs.columns if "Easting" in c)
    north = next(c for c in attrs.columns if "Northing" in c)
    tlc = next(c for c in attrs.columns if c.startswith("Three Letter Code"))
    attrs = attrs.select(
        # ODS numbers arrive as floats (5131.0); the usage table has them as text
        pl.col(nlc_a).cast(pl.Float64, strict=False).cast(pl.Int64).cast(pl.Utf8).alias("nlc"),
        pl.col(tlc).alias("tlc"),
        pl.col(east).cast(pl.Float64, strict=False).alias("x"),
        pl.col(north).cast(pl.Float64, strict=False).alias("y"),
    )
    out = (
        usage.join(attrs, on="nlc", how="left")
        .filter(pl.col("x").is_not_null() & pl.col("y").is_not_null())
        .with_columns((pl.col("entries_exits") / 1e6).alias("entries_exits_m"))
        .sort("station")
    )
    unplaced = usage.height - out.height
    if unplaced:
        logger.warning(f"{unplaced} stations in the usage table have no grid reference")
    logger.info(
        f"{out.height:,} stations, {out['entries_exits'].sum() / 1e6:,.0f}m entries and exits"
    )
    return out.lazy()
