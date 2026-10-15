"""Household income (ONS, MSOA), council tax (MHCLG, billing authority) and the
housing stock by council tax band and build period (VOA, LSOA)."""

import fastexcel
import polars as pl

from lix_core.codes import active_nations, in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.housing")

# Workbook sheet → our column. Each sheet: MSOA code, names, then the estimate
INCOME_SHEETS = {
    "Total annual income": "income_total",
    "Net annual income": "income_net",
    "Net income before housing costs": "income_net_bhc_equivalised",
    "Net income after housing costs": "income_net_ahc_equivalised",
}


def _header_row(reader: fastexcel.ExcelReader, sheet: str, first_col: str) -> int:
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=20).to_polars()
    col = probe[probe.columns[0]].to_list()
    return next(i for i, v in enumerate(col) if v and str(v).startswith(first_col))


def stage_msoa_income() -> pl.LazyFrame:
    """Mean household income per MSOA (£ a year), model-based estimates."""
    reader = fastexcel.read_excel(data_dir("raw") / "msoa_income" / "msoa_income.xlsx")
    out = None
    for sheet, name in INCOME_SHEETS.items():
        header = _header_row(reader, sheet, "MSOA code")
        df = reader.load_sheet_by_name(sheet, header_row=header).to_polars()
        df = df.select(
            pl.col(df.columns[0]).alias("msoa21cd"),
            pl.col(df.columns[6]).cast(pl.Float64, strict=False).alias(name),
        ).filter(in_scope("msoa21cd", "mid"))
        out = df if out is None else out.join(df, on="msoa21cd", how="full", coalesce=True)
    logger.info(f"{out.height:,} MSOAs with income estimates")
    return out.lazy()


BANDS = ["A", "B", "C", "D", "E", "F", "G", "H"]


def stage_council_tax() -> pl.LazyFrame:
    """Council tax per band for each billing authority, all precepts included.

    Table 9 is the bill for two adults in the authority's area (county, police, fire and
    an average parish precept), i.e. what a typical household actually pays.
    """
    reader = fastexcel.read_excel(data_dir("raw") / "council_tax" / "council_tax.ods")
    header = _header_row(reader, "Table_9", "E Code")
    df = reader.load_sheet_by_name("Table_9", header_row=header).to_polars()
    df = df.rename({c: c.strip() for c in df.columns})
    out = df.select(
        pl.col("ONS Code").alias("lad_cd"),
        pl.col("Authority").alias("authority"),
        *[pl.col(f"Band {b}").cast(pl.Float64).alias(f"band_{b.lower()}") for b in BANDS],
    ).filter(in_scope("lad_cd", "upper"))
    wales = data_dir("raw") / "wg_council_tax" / "wg_council_tax.xlsx"
    if "W" in active_nations() and wales.exists():
        out = pl.concat([out, _wg_council_tax(wales)], how="vertical_relaxed")
    scotland = data_dir("raw") / "sg_council_tax" / "sg_council_tax.xlsx"
    if "S" in active_nations() and scotland.exists():
        out = pl.concat([out, _sg_council_tax(scotland)], how="vertical_relaxed")
    if "N" in active_nations():
        out = pl.concat([out, _ni_domestic_rates()], how="diagonal_relaxed")
    logger.info(f"{out.height} billing authorities; Band D median £{out['band_d'].median():,.0f}")
    return out.lazy()


def _ni_domestic_rates() -> pl.DataFrame:
    """Northern Ireland has domestic rates, not council tax: the regional rate plus each
    council's district rate, in the pound of a home's capital value (``rate_poundage``;
    the bands stay null)."""
    raw = pl.read_csv(data_dir("raw") / "ni_domestic_rates" / "ni_domestic_rates.csv")
    out = raw.select(
        pl.col("lad_cd"),
        pl.col("council").alias("authority"),
        pl.col("total_rate").cast(pl.Float64).alias("rate_poundage"),
    )
    logger.info(f"{out.height} councils' domestic rate poundages")
    return out


# Council tax bands are fixed multiples of band D (ninths); Wales's band I (21/9) is left out
BAND_RATIOS = {"a": 6, "b": 7, "c": 8, "d": 9, "e": 11, "f": 13, "g": 15, "h": 18}


def _wg_council_tax(path) -> pl.DataFrame:
    """Welsh authorities' average band D (all precepts) from the Welsh Government release,
    the other bands derived from the statutory ratios."""
    reader = fastexcel.read_excel(path)
    header = _header_row(reader, "Table1", "Authority")
    df = reader.load_sheet_by_name("Table1", header_row=header).to_polars()
    band_d = next(c for c in df.columns if c.startswith("Overall average band D"))
    df = df.select(
        pl.col("Authority").str.strip_chars().alias("authority"),
        pl.col(band_d).cast(pl.Float64, strict=False).alias("band_d"),
    ).filter(pl.col("band_d").is_not_null())
    lads = (
        pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet")
        .filter(in_scope("lad_cd", "upper", nations=("W",)))
        .select("lad_cd", pl.col("lad_nm").alias("authority"))
        .unique()
    )
    out = df.join(lads, on="authority", how="inner")
    if out.height != lads.height:
        logger.warning(f"{lads.height - out.height} Welsh authorities missing from council tax")
    return out.select(
        "lad_cd",
        "authority",
        *[(pl.col("band_d") * r / 9).round(2).alias(f"band_{b}") for b, r in BAND_RATIOS.items()],
    )


# The Scottish Government's council names where they differ from ONS's
SG_COUNCIL_ALIASES = {
    "Edinburgh, City of": "City of Edinburgh",
    "Edinburgh City": "City of Edinburgh",
    "Eilean Siar": "Na h-Eileanan Siar",
    "Western Isles": "Na h-Eileanan Siar",
    "Orkney": "Orkney Islands",
    "Shetland": "Shetland Islands",
    "Argyll & Bute": "Argyll and Bute",
    "Dumfries & Galloway": "Dumfries and Galloway",
    "Perth & Kinross": "Perth and Kinross",
}


def _sg_council_tax(path) -> pl.DataFrame:
    """Scottish councils' charge for every band from the Scottish Government's council tax
    by band table (one sheet; the header row names the bands)."""
    reader = fastexcel.read_excel(path)
    sheet = reader.sheet_names[0]
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=20).to_polars()
    header = next(
        i for i, row in enumerate(probe.rows()) if any(str(v).strip() == "Band D" for v in row)
    )
    df = reader.load_sheet_by_name(sheet, header_row=header).to_polars()
    df = df.rename({c: c.strip() for c in df.columns})
    authority = df.columns[0]
    band_d = pl.col("Band D").cast(pl.Float64, strict=False)
    df = df.filter(band_d.is_not_null() & ~pl.col(authority).str.contains("(?i)average|ratio"))
    df = df.select(
        pl.col(authority)
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .replace(SG_COUNCIL_ALIASES)
        .alias("authority"),
        *[
            pl.col(f"Band {b}").cast(pl.Float64, strict=False).round(2).alias(f"band_{b.lower()}")
            for b in BANDS
        ],
    )
    lads = (
        pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet")
        .filter(in_scope("lad_cd", "upper", nations=("S",)))
        .select("lad_cd", pl.col("lad_nm").alias("authority"))
        .unique()
    )
    out = df.join(lads, on="authority", how="inner")
    if out.height != lads.height:
        missing = sorted(set(lads["authority"]) - set(out["authority"]))
        logger.warning(f"{len(missing)} Scottish councils missing from council tax: {missing}")
    return out.select("lad_cd", "authority", *[f"band_{b.lower()}" for b in BANDS])


BUILD_PERIODS = {
    "pre_1919": ["bp_pre_1900", "bp_1900_1918"],
    "1919_1944": ["bp_1919_1929", "bp_1930_1939"],
    "1945_1999": [
        "bp_1945_1954",
        "bp_1955_1964",
        "bp_1965_1972",
        "bp_1973_1982",
        "bp_1983_1992",
        "bp_1993_1999",
    ],
}


def _total(df: pl.DataFrame, cols: list[str]) -> pl.Expr:
    """Row sum of whichever of ``cols`` the file has (0 if none)."""
    present = [c for c in cols if c in df.columns]
    return pl.sum_horizontal(present) if present else pl.lit(0.0)


def stage_voa_ctsop() -> pl.LazyFrame:
    """Dwellings per LSOA by council tax band and by build period.

    Counts are rounded to the nearest 10 and small cells suppressed ("-"), so shares
    are approximate in small LSOAs. Build periods 2000 onwards are summed as new builds.
    """
    path = next((data_dir("raw") / "voa_ctsop").glob("**/CTSOP4_1_*.csv"))
    raw = pl.read_csv(path, infer_schema=False, encoding="utf8-lossy")
    raw = raw.filter((pl.col("geography") == "LSOA") & in_scope("ecode"))
    num = [c for c in raw.columns if c.startswith("bp_") or c == "all_properties"]
    raw = raw.with_columns(pl.col(num).cast(pl.Float64, strict=False))
    new_build = [
        c for c in raw.columns if c.startswith("bp_") and c[3:7].isdigit() and int(c[3:7]) >= 2000
    ]

    totals = raw.filter(pl.col("band") == "All").select(
        pl.col("ecode").alias("lsoa21cd"),
        pl.col("all_properties").alias("dwellings"),
        *[_total(raw, cols).alias(f"built_{name}") for name, cols in BUILD_PERIODS.items()],
        _total(raw, new_build).alias("built_2000_on"),
        pl.col("bp_unkw").alias("built_unknown"),
    )
    bands = (
        raw.filter(pl.col("band").is_in(BANDS))
        .select(pl.col("ecode").alias("lsoa21cd"), "band", "all_properties")
        .pivot(on="band", index="lsoa21cd", values="all_properties")
        .rename({b: f"band_{b.lower()}" for b in BANDS}, strict=False)
    )
    out = totals.join(bands, on="lsoa21cd", how="left")
    if "S" in active_nations():
        out = pl.concat([out, _nrs_dwellings()], how="diagonal_relaxed")
    if "N" in active_nations():
        out = pl.concat([out, _lps_dwellings()], how="diagonal_relaxed")
    out = out.sort("lsoa21cd")
    logger.info(f"{out.height:,} LSOAs, {out['dwellings'].sum():,.0f} dwellings")
    return out.lazy()


def _nrs_dwellings() -> pl.DataFrame:
    """Dwellings per Data Zone 2022 from NRS (council tax bands in three groups, so only
    the totals are comparable with the VOA table; the band and build-period columns stay
    null for Scotland)."""
    raw = pl.read_csv(
        data_dir("raw") / "nrs_dwellings_by_band" / "nrs_dwellings_by_band.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    latest = raw.filter(pl.col("GeographyType").str.contains("2022 Data Zones"))
    year = latest["DateCode"].cast(pl.Int32, strict=False).max()
    out = (
        latest.filter(
            (pl.col("DateCode").cast(pl.Int32, strict=False) == year)
            # The file carries the three band groups and their total; keep the total
            & pl.col("Council Tax Band").str.to_lowercase().str.starts_with("total")
        )
        .group_by(pl.col("GeographyCode").alias("lsoa21cd"))
        .agg(pl.col("Value").cast(pl.Float64, strict=False).sum().alias("dwellings"))
        .filter(in_scope("lsoa21cd", nations=("S",)))
    )
    logger.info(
        f"Scotland {year}: {out.height:,} Data Zones, {out['dwellings'].sum():,.0f} dwellings"
    )
    return out


# LPS capital value bands (April 2016) in order, and their edges in pounds; the open top
# band is taken to run to twice its floor for the median
CAPITAL_VALUE_BANDS = [
    "Up to £40000",
    "£40001 to £52000",
    "£52001 to £68000",
    "£68001 to £88000",
    "£88001 to £120000",
    "£120001 to £160000",
    "£160001 to £320000",
    "more than £320000",
]
CAPITAL_VALUE_EDGES = [0, 40_000, 52_000, 68_000, 88_000, 120_000, 160_000, 320_000, 640_000]


def median_from_bands(counts: list[float], edges: list[float]) -> float | None:
    """The median of values known only by band, interpolated within the band that holds
    the middle home."""
    total = sum(counts)
    if total <= 0:
        return None
    half = total / 2
    before = 0.0
    for i, n in enumerate(counts):
        if before + n >= half and n > 0:
            return edges[i] + (half - before) / n * (edges[i + 1] - edges[i])
        before += n
    return None


def _lps_dwellings() -> pl.DataFrame:
    """Northern Ireland's homes by capital value band per 2001 Super Output Area (LPS,
    April 2016), brought onto the 2021 Super Data Zones by apportioning the counts;
    the eight bands stand in the ``band_a``–``band_h`` columns and the area's median
    capital value feeds the domestic rates figure."""
    from lix_pipeline.geo.crosswalk import crosswalk

    path = data_dir("raw") / "lps_capital_value_bands" / "lps_capital_value_bands.csv"
    text = path.read_bytes().decode("latin-1")
    raw = pl.read_csv(text.encode("utf-8"), infer_schema_length=0)
    raw = raw.rename({c: c.strip() for c in raw.columns})
    raw = raw.select(
        pl.col("SOA2001").str.strip_chars().alias("soa01cd"),
        pl.col("Capital Value").str.strip_chars().alias("band"),
        pl.col("Number_of_Properties").cast(pl.Float64, strict=False).fill_null(0).alias("n"),
    )
    unknown = sorted(set(raw["band"].unique()) - set(CAPITAL_VALUE_BANDS))
    if unknown:
        raise ValueError(f"Unexpected capital value bands {unknown}")
    wide = raw.pivot(on="band", index="soa01cd", values="n", aggregate_function="sum")
    for band in CAPITAL_VALUE_BANDS:
        if band not in wide.columns:
            wide = wide.with_columns(pl.lit(0.0).alias(band))
    letters = {band: f"band_{chr(ord('a') + i)}" for i, band in enumerate(CAPITAL_VALUE_BANDS)}
    wide = wide.rename(letters).with_columns(
        pl.sum_horizontal(list(letters.values())).alias("dwellings")
    )
    medians = [
        median_from_bands([row[c] for c in letters.values()], CAPITAL_VALUE_EDGES)
        for row in wide.iter_rows(named=True)
    ]
    wide = wide.with_columns(pl.Series("capital_value_median", medians, dtype=pl.Float64))
    weights = pl.read_parquet(data_dir("staged") / "soa01_sdz21.parquet")
    counts = crosswalk(
        wide, weights, ["dwellings", *letters.values()], how="sum", key="soa01cd", new_key="sdz21cd"
    )
    value = crosswalk(
        wide, weights, ["capital_value_median"], how="mean", key="soa01cd", new_key="sdz21cd"
    )
    out = counts.join(value, on="lsoa21cd", how="left")
    logger.info(
        f"{out.height:,} Super Data Zones, {out['dwellings'].sum():,.0f} homes by capital value "
        f"(2016); median value £{out['capital_value_median'].median():,.0f}"
    )
    return out
