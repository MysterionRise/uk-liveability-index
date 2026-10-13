"""One deprivation table for every nation: IoD 2025 in England, WIMD 2025 in Wales, SIMD
2020 in Scotland (on the 2022 Data Zones through the crosswalk).

The indices are built the same way (income and employment domains are rates of people
affected; the others are weighted combinations) but by different governments on
different inputs, so values are only comparable within a nation: the indicators that
read this table are benchmarked ``nation``. Columns are harmonised by concept:

    imd_score, imd_rank, imd_decile          overall index (rank and decile within the nation)
    income_score, employment_score           share of people deprived (both nations)
    health_score, education_score            domain scores
    crime_score                              IoD crime domain / WIMD community safety
    housing_score                            IoD indoors sub-domain / WIMD housing domain
    outdoors_score                           IoD outdoors sub-domain / WIMD physical environment
"""

import fastexcel
import polars as pl

from lix_core.codes import active_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file

logger = setup_logging("stage.deprivation")

COLUMNS = [
    "lsoa21cd", "imd_score", "imd_rank", "imd_decile", "income_score", "employment_score",
    "health_score", "education_score", "crime_score", "housing_score", "outdoors_score",
]  # fmt: skip

# WIMD sheet headers (stripped) → harmonised names
WIMD_SCORES = {
    "WIMD 2025": "imd_score", "Income": "income_score", "Employment": "employment_score",
    "Health": "health_score", "Education": "education_score", "Housing": "housing_score",
    "Community Safety": "crime_score", "Physical Environment": "outdoors_score",
}  # fmt: skip


def _sheet(path, sheet: str, first_col: str) -> pl.DataFrame:
    reader = fastexcel.read_excel(path)
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=12).to_polars()
    first = probe[probe.columns[0]].to_list()
    header = next(i for i, v in enumerate(first) if str(v).startswith(first_col))
    df = reader.load_sheet_by_name(sheet, header_row=header).to_polars()
    return df.rename({c: c.strip() for c in df.columns})


def _england() -> pl.DataFrame:
    iod = pl.read_parquet(data_dir("staged") / "iod_2025.parquet")
    return iod.select(
        "lsoa21cd",
        "imd_score",
        "imd_rank",
        "imd_decile",
        "income_score",
        "employment_score",
        "health_score",
        "education_score",
        "crime_score",
        pl.col("sub_indoors_score").alias("housing_score"),
        pl.col("sub_outdoors_score").alias("outdoors_score"),
    )


def _wales() -> pl.DataFrame:
    scores = _sheet(raw_file("wimd_2025_scores", "ods"), "Data", "LSOA code")
    missing = [c for c in WIMD_SCORES if c not in scores.columns]
    if missing:
        raise ValueError(f"WIMD scores sheet lacks columns {missing}; has {scores.columns}")
    out = scores.select(
        pl.col("LSOA code").alias("lsoa21cd"),
        *[
            pl.col(src).cast(pl.Float64, strict=False).alias(dst)
            for src, dst in WIMD_SCORES.items()
        ],
    )
    ranks = _sheet(raw_file("wimd_2025_ranks", "ods"), "WIMD_2025_ranks", "LSOA code")
    rank_col = next(c for c in ranks.columns if c.startswith("WIMD 2025"))
    ranks = ranks.select(
        pl.col("LSOA code").alias("lsoa21cd"),
        pl.col(rank_col).cast(pl.Int32, strict=False).alias("imd_rank"),
    )
    out = out.join(ranks, on="lsoa21cd", how="left").with_columns(
        # Decile within Wales, 1 = most deprived, as the IoD's is within England
        ((pl.col("imd_rank") - 1) * 10 // pl.col("imd_rank").count() + 1)
        .cast(pl.Int32)
        .alias("imd_decile")
    )
    return out.select(COLUMNS)


# SIMD 2020v2 rank columns → harmonised domain names (ranks become within-Scotland scores)
SIMD_RANKS = {
    "SIMD2020v2_Rank": "imd_score",
    "SIMD2020_Health_Domain_Rank": "health_score",
    "SIMD2020_Education_Domain_Rank": "education_score",
    "SIMD2020_Access_Domain_Rank": "access_score",
    "SIMD2020_Crime_Domain_Rank": "crime_score",
    "SIMD2020_Housing_Domain_Rank": "housing_score",
}


def rank_to_score(rank: pl.Expr, n: pl.Expr) -> pl.Expr:
    """A rank (1 = most deprived) as a 0–100 score where more is more deprived, like the
    IoD's and WIMD's scores."""
    return (n - rank + 1) / n * 100


def _scotland() -> pl.DataFrame:
    """SIMD 2020v2 on the 2022 Data Zones: every rank becomes a within-Scotland score and
    the income and employment rates stay rates; values cross from the 2011 zones as
    population-weighted means (``dz11_dz22``), and the overall rank and decile are
    recomputed on the 2022 zones. Extra columns feed the Scottish substitutes:
    ``access_score`` (geographic access) and ``crime_rate_1000`` (recorded crimes per
    1,000 people a year).
    """
    from lix_pipeline.geo.crosswalk import crosswalk

    ranks = _sheet(raw_file("simd_2020_ranks", "xlsx"), "SIMD 2020v2 ranks", "Data_Zone")
    n = pl.col("SIMD2020v2_Rank").cast(pl.Float64).count()
    scores = ranks.select(
        pl.col("Data_Zone").alias("dz11cd"),
        *[
            rank_to_score(pl.col(src).cast(pl.Float64, strict=False), n).alias(dst)
            for src, dst in SIMD_RANKS.items()
        ],
    )
    data = _sheet(raw_file("simd_2020_indicators", "xlsx"), "Data", "Data_Zone")
    rates = data.select(
        pl.col("Data_Zone").alias("dz11cd"),
        pl.col("Income_rate").cast(pl.Float64, strict=False).alias("income_score"),
        pl.col("Employment_rate").cast(pl.Float64, strict=False).alias("employment_score"),
        # crime_rate is per 10,000 people; per 1,000 matches the police.uk indicators
        (pl.col("crime_rate").cast(pl.Float64, strict=False) / 10).alias("crime_rate_1000"),
    )
    columns = [*SIMD_RANKS.values(), "income_score", "employment_score", "crime_rate_1000"]
    values = scores.join(rates, on="dz11cd", how="full", coalesce=True)
    weights = pl.read_parquet(data_dir("staged") / "dz11_dz22.parquet")
    out = crosswalk(values, weights, columns, how="mean")
    # Rank and decile within Scotland, 1 = most deprived, on the new zones
    out = out.with_columns(
        pl.col("imd_score").rank("ordinal", descending=True).cast(pl.Int32).alias("imd_rank")
    ).with_columns(
        ((pl.col("imd_rank") - 1) * 10 // pl.col("imd_rank").count() + 1)
        .cast(pl.Int32)
        .alias("imd_decile"),
        pl.lit(None, pl.Float64).alias("outdoors_score"),
    )
    logger.info(f"SIMD 2020v2 on {out.height:,} Data Zones 2022")
    return out.select(*COLUMNS, "access_score", "crime_rate_1000")


def stage_deprivation() -> pl.LazyFrame:
    frames = []
    if "E" in active_nations():
        frames.append(_england())
    if "W" in active_nations():
        frames.append(_wales())
    if "S" in active_nations():
        frames.append(_scotland())
    df = pl.concat(frames, how="diagonal_relaxed").sort("lsoa21cd")
    logger.info(f"Deprivation: {df.height:,} LSOAs across {active_nations()}")
    return df.lazy()
