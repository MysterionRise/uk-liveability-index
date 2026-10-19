"""Backbone population: mid-year estimates for every small area.

England and Wales come from one ONS workbook with a ``Mid-YYYY LSOA 2021`` sheet per year
(broad age groups by sex) and a median-age sheet; Scotland from NRS's small area
population estimates, one workbook per year with single years of age by sex per Data
Zone 2022; Northern Ireland from NISRA's Super Data Zone estimates; Ireland from the
Census 2022 Small Area Population Statistics (no mid-year estimates exist below county
level). The latest year of each is kept. These are the denominators of every per-head
indicator in every nation alike.
"""

import re

import fastexcel
import polars as pl

from lix_core.codes import active_nations, in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file

logger = setup_logging("stage.population")

SHEET = re.compile(r"Mid-(\d{4}) LSOA 2021")


def latest_sheet(names: list[str]) -> tuple[str, int]:
    """The ``Mid-YYYY LSOA 2021`` sheet with the latest year, and that year."""
    found = [(int(m.group(1)), n) for n in names if (m := SHEET.fullmatch(n))]
    if not found:
        raise ValueError(f"No 'Mid-YYYY LSOA 2021' sheet among {names}")
    year, name = max(found)
    return name, year


def _header_row(reader: fastexcel.ExcelReader, sheet: str) -> int:
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=20).to_polars()
    first = probe[probe.columns[0]].to_list()
    return next(i for i, v in enumerate(first) if v and str(v).startswith("LAD"))


def tidy_population(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """A year's sheet (header row applied) → one row per LSOA with the counts we keep."""
    code = next(c for c in df.columns if c.startswith("LSOA 2021 Code"))

    def count(col: str) -> pl.Expr:
        return pl.col(col).cast(pl.Float64, strict=False).round().cast(pl.Int32)

    return df.select(
        pl.col(code).str.strip_chars().alias("lsoa21cd"),
        count("Total").alias("population"),
        (count("F0 to 15") + count("M0 to 15")).alias("pop_children_0_15"),
        (count("F65 and over") + count("M65 and over")).alias("pop_65_plus"),
        pl.lit(year, dtype=pl.Int16).alias("pop_year"),
    ).filter(pl.col("lsoa21cd").is_not_null())


def tidy_median_age(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """The median-age sheet → ``median_age`` for ``year`` per LSOA."""
    code = next(c for c in df.columns if c.startswith("LSOA 2021 Code"))
    col = next(c for c in df.columns if c.strip() == f"Median age mid-{year}")
    return df.select(
        pl.col(code).str.strip_chars().alias("lsoa21cd"),
        pl.col(col).cast(pl.Float32, strict=False).alias("median_age"),
    )


SAPE_FILE = re.compile(r"SAPE (\d{4})\.xlsx")


def median_age_from_counts(counts: pl.DataFrame, age_columns: list[str]) -> pl.Series:
    """The median age per row from single-year counts (``Age 0``… ``Age 90 and over``),
    interpolated within the median year."""
    ages = counts.select(age_columns).cast(pl.Float64).fill_null(0.0).to_numpy()
    total = ages.sum(axis=1)
    half = total / 2
    cumulative = ages.cumsum(axis=1)
    before = cumulative - ages
    idx = (cumulative < half[:, None]).sum(axis=1)
    idx = idx.clip(0, len(age_columns) - 1)
    rows = range(len(total))
    within = [
        (half[r] - before[r, idx[r]]) / ages[r, idx[r]] if ages[r, idx[r]] else 0.5 for r in rows
    ]
    return pl.Series("median_age", [float(i) + w for i, w in zip(idx, within)], dtype=pl.Float32)


def tidy_sape(df: pl.DataFrame, year: int) -> pl.DataFrame:
    """A year's NRS sheet (header row applied) → one row per Data Zone with the counts we
    keep: persons, children under 16, people 65 and over, and the median age."""
    code = next(c for c in df.columns if c.strip().lower() == "data zone code")
    sex = next(c for c in df.columns if c.strip().lower() == "sex")
    persons = df.filter(pl.col(sex).str.strip_chars() == "Persons")
    ages = [c for c in persons.columns if re.fullmatch(r"Age \d+( and over)?", c.strip())]
    year_of = {c: int(re.search(r"\d+", c).group()) for c in ages}

    def count(cols: list[str]) -> pl.Expr:
        return (
            pl.sum_horizontal([pl.col(c).cast(pl.Float64, strict=False) for c in cols])
            .round()
            .cast(pl.Int32)
        )

    return persons.select(
        pl.col(code).str.strip_chars().alias("lsoa21cd"),
        count(ages).alias("population"),
        count([c for c in ages if year_of[c] <= 15]).alias("pop_children_0_15"),
        count([c for c in ages if year_of[c] >= 65]).alias("pop_65_plus"),
        pl.lit(year, dtype=pl.Int16).alias("pop_year"),
        median_age_from_counts(persons, ages),
    ).filter(pl.col("lsoa21cd").str.starts_with("S01"))


def _scotland() -> pl.DataFrame:
    folder = data_dir("raw") / "nrs_sape"
    files = {
        int(m.group(1)): f for f in folder.glob("*.xlsx") if (m := SAPE_FILE.fullmatch(f.name))
    }
    if not files:
        raise FileNotFoundError(f"No 'SAPE YYYY.xlsx' under {folder}")
    year = max(files)
    reader = fastexcel.read_excel(files[year])
    sheet = next((n for n in reader.sheet_names if n.strip() == str(year)), reader.sheet_names[-1])
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=20).to_polars()
    header = next(
        i
        for i, v in enumerate(probe[probe.columns[0]].to_list())
        if v and str(v).strip().lower() == "data zone code"
    )
    frame = reader.load_sheet_by_name(sheet, header_row=header).to_polars()
    out = tidy_sape(frame, year)
    logger.info(
        f"Population mid-{year}: {out.height:,} Data Zones, {out['population'].sum():,} people"
    )
    return out


def _england_wales() -> pl.DataFrame:
    reader = fastexcel.read_excel(raw_file("pop_lsoa_mye", "xlsx"))
    sheet, year = latest_sheet(reader.sheet_names)
    frame = reader.load_sheet_by_name(sheet, header_row=_header_row(reader, sheet)).to_polars()
    out = tidy_population(frame, year)
    median = next((n for n in reader.sheet_names if n.startswith("Median age")), None)
    if median:
        frame = reader.load_sheet_by_name(median, header_row=_header_row(reader, median))
        out = out.join(tidy_median_age(frame.to_polars(), year), on="lsoa21cd", how="left")
    logger.info(f"Population mid-{year}: {out.height:,} LSOAs, {out['population'].sum():,} people")
    return out


def tidy_mye(flat: pl.DataFrame) -> pl.DataFrame:
    """NISRA's ``Flat`` sheet (area, year, sex, age band, count) → one row per Super Data
    Zone for the latest year: persons, children under 16 and people 65 and over. The
    bands are broad, so there is no median age."""

    def col(name: str) -> str:
        return next(c for c in flat.columns if c.strip().lower() == name.lower())

    df = flat.select(
        pl.col(col("Area_code")).cast(pl.Utf8).str.strip_chars().alias("lsoa21cd"),
        pl.col(col("Year")).cast(pl.Int64, strict=False).alias("year"),
        pl.col(col("Sex")).cast(pl.Utf8).str.strip_chars().alias("sex"),
        pl.col(col("Age")).cast(pl.Utf8).str.strip_chars().str.replace(r"^00-", "0-").alias("age"),
        pl.col(col("MYE")).cast(pl.Float64, strict=False).round().cast(pl.Int32).alias("n"),
    ).filter(pl.col("lsoa21cd").str.starts_with("N21") & (pl.col("sex") == "All Persons"))
    year = int(df["year"].max())
    latest = df.filter(pl.col("year") == year)

    def band(label: str) -> pl.DataFrame:
        return latest.filter(pl.col("age") == label).select("lsoa21cd", pl.col("n").alias(label))

    return (
        band("All ages")
        .join(band("0-15"), on="lsoa21cd", how="left")
        .join(band("65+"), on="lsoa21cd", how="left")
        .select(
            "lsoa21cd",
            pl.col("All ages").alias("population"),
            pl.col("0-15").alias("pop_children_0_15"),
            pl.col("65+").alias("pop_65_plus"),
            pl.lit(year, dtype=pl.Int16).alias("pop_year"),
            pl.lit(None, pl.Float32).alias("median_age"),
        )
        .sort("lsoa21cd")
    )


SAPS_CHILDREN = [f"T1_1AGE{i}T" for i in range(16)]
SAPS_OLDER = ["T1_1AGE65_69T", "T1_1AGE70_74T", "T1_1AGE75_79T", "T1_1AGE80_84T", "T1_1AGEGE_85T"]


def tidy_saps(df: pl.DataFrame) -> pl.DataFrame:
    """The CSO's Small Area Population Statistics (strings as read) → one row per Small
    Area for Census 2022: persons, children under 16 and people 65 and over, keyed by the
    codes minted from the area ids (``lix_core.codes.minted_codes``). The file's State
    total row (no urban/rural class) is dropped."""
    from lix_core.codes import minted_codes

    df = df.filter(pl.col("UR_Category").is_not_null())
    codes = minted_codes("I", "low", df["GEOGID"].to_list())

    def total(cols: list[str]) -> pl.Expr:
        return pl.sum_horizontal(pl.col(c).cast(pl.Float64, strict=False) for c in cols)

    return df.select(
        pl.col("GEOGID").replace_strict(codes).alias("lsoa21cd"),
        pl.col("T1_1AGETT")
        .cast(pl.Float64, strict=False)
        .round()
        .cast(pl.Int32)
        .alias("population"),
        total(SAPS_CHILDREN).round().cast(pl.Int32).alias("pop_children_0_15"),
        total(SAPS_OLDER).round().cast(pl.Int32).alias("pop_65_plus"),
        pl.lit(2022, dtype=pl.Int16).alias("pop_year"),
        pl.lit(None, pl.Float32).alias("median_age"),
    ).sort("lsoa21cd")


def _ireland() -> pl.DataFrame:
    df = pl.read_csv(raw_file("cso_saps22_sa", "csv"), infer_schema_length=0, encoding="utf8-lossy")
    out = tidy_saps(df)
    logger.info(
        f"Population Census 2022: {out.height:,} Small Areas, {out['population'].sum():,} people"
    )
    return out


def _northern_ireland() -> pl.DataFrame:
    reader = fastexcel.read_excel(raw_file("nisra_sdz_mye", "xlsx"))
    out = tidy_mye(reader.load_sheet_by_name("Flat").to_polars())
    logger.info(
        f"Population mid-{out['pop_year'][0]}: {out.height:,} Super Data Zones, "
        f"{out['population'].sum():,} people"
    )
    return out


def stage_population() -> pl.LazyFrame:
    frames = []
    if any(n in active_nations() for n in ("E", "W")):
        frames.append(_england_wales())
    if "S" in active_nations():
        frames.append(_scotland())
    if "N" in active_nations():
        frames.append(_northern_ireland())
    if "I" in active_nations():
        frames.append(_ireland())
    out = pl.concat(frames, how="diagonal_relaxed").filter(in_scope("lsoa21cd")).sort("lsoa21cd")
    return out.lazy()
