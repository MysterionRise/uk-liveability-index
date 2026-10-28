"""Storm overflows and their spills (#83), from the Environment Agency's Event Duration
Monitoring annual return: one row per overflow with its outlet's location (from the
National Grid reference), the hours it spilled in the year and the counted spills.

The total duration is the raw monitored time of every spill, before the 12/24-hour
counting rule that turns them into "counted spills". An overflow whose monitor worked
for less than 90 % of the year under-reports; the share is kept so the caveat can say so.
"""

from __future__ import annotations

import re

import fastexcel
import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.ngr import ngr_to_bng_arrays

logger = setup_logging("stage.storm_overflows")

SHEET = "All WaSC"
# Day 0 of Excel's 1900 date system as spreadsheet readers count it
EXCEL_EPOCH = __import__("datetime").datetime(1899, 12, 30)
COLUMNS = {
    "Unique ID": "overflow_id",
    "Water Company Name": "company",
    "Site Name (EA Consents Database)": "site",
    "Outlet Discharge NGR (EA Consents Database)": "ngr",
    (
        "Total Duration (hh:mm:ss) all spills prior to processing through 12-24h count method"
    ): "duration",
    "Counted spills using 12-24h count method": "spills",
    "EDM Operation - % of reporting period EDM operational": "edm_operation_pct",
}


def duration_hours(value) -> float | None:
    """'1234:56:07' (hours can pass 24), or an Excel day fraction, → hours."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value) * 24.0
    text = str(value).strip()
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", text)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60 + int(m.group(3) or 0) / 3600
    # A duration over a day reads as Excel's date for the serial number, counted from
    # 1899-12-30 ("1900-01-16 21:00:00" is 17 days and 21 hours): most of the 2025 file
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?", text)
    if m:
        from datetime import datetime

        y, mo, d, h, mi, s = (int(g or 0) for g in m.groups())
        return (datetime(y, mo, d, h, mi, s) - EXCEL_EPOCH).total_seconds() / 3600
    try:
        return float(text) * 24.0  # a day fraction written as text
    except ValueError:
        return None


def read_return(path) -> pl.DataFrame:
    raw = (
        fastexcel.read_excel(str(path))
        .load_sheet_by_name(SHEET, header_row=None, dtypes="string")
        .to_polars()
    )
    hdr = next(i for i, r in enumerate(raw.iter_rows()) if str(r[0]).strip() == "Unique ID")
    names = [re.sub(r"\s+", " ", str(c)).strip() for c in raw.row(hdr)]
    body = raw.slice(hdr + 1)
    body.columns = [f"{n}#{i}" for i, n in enumerate(names)]
    picked = {}
    for src, dst in COLUMNS.items():
        col = next((c for c in body.columns if c.split("#")[0] == src), None)
        if col is None:
            raise ValueError(f"{path}: no column {src!r}")
        picked[col] = dst
    df = body.select(list(picked)).rename(picked).filter(pl.col("overflow_id").is_not_null())
    x, y = ngr_to_bng_arrays(df["ngr"].to_list())
    return df.with_columns(
        pl.Series("x", x),
        pl.Series("y", y),
        pl.col("duration")
        .map_elements(duration_hours, return_dtype=pl.Float64)
        .alias("spill_hours"),
        pl.col("spills").cast(pl.Float64, strict=False),
        pl.col("edm_operation_pct").cast(pl.Float64, strict=False),
    ).drop("duration")


def stage_storm_overflows() -> pl.LazyFrame:
    files = sorted((data_dir("raw") / "ea_edm_annual").glob("**/*.xlsx"))
    if not files:
        raise FileNotFoundError("No EDM annual return under data/raw/ea_edm_annual")
    df = read_return(files[-1])
    located = df.filter(pl.col("x").is_not_nan())
    logger.info(
        f"{df.height:,} overflows ({df.height - located.height} without a usable grid reference), "
        f"{located['spill_hours'].sum():,.0f} spill hours; monitors under 90% of the year: "
        f"{located.filter(pl.col('edm_operation_pct') < 90).height:,}"
    )
    return located.lazy()
