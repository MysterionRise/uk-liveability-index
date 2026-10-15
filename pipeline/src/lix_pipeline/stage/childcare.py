"""Ofsted-registered nurseries and other group childcare, with places and quality.

Only childcare on non-domestic premises on the Early Years Register (care for under-5s)
is located: childminders' addresses are withheld, and out-of-school clubs serve school
children. Quality uses the same 0–1 scale as schools: the renewed early years
framework's grades (from November 2025) where inspected under it, otherwise the
previous overall effectiveness grade, otherwise "expected standard".
"""

import polars as pl

from lix_core.codes import in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.stage.health import geocode_postcodes
from lix_pipeline.stage.schools import NEUTRAL, OEIF_SCALE, REPORT_CARD_SCALE

logger = setup_logging("stage.childcare")

REIF_PREFIX = "EYR REIF: Most recent: "
REIF_AREAS = [
    "Inclusion",
    "Curriculum and teaching",
    "Achievement",
    "Behaviour, attitudes and establishing routines",
    "Children's welfare and wellbeing",
    "Leadership and governance",
]
OEIF_OVERALL = "EYR OEIF/CIF: Most recent: Overall effectiveness"


def _read(path) -> pl.DataFrame:
    """The CSV has title lines above the header; find the header by its first column."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        skip = next(i for i, line in enumerate(f) if line.startswith("Web link,Provider URN"))
    return pl.read_csv(path, skip_rows=skip, infer_schema=False, encoding="utf8-lossy")


def stage_childcare() -> pl.LazyFrame:
    """Nurseries for every active nation: Ofsted's register in England, the Care
    Inspectorate's in Scotland, OpenStreetMap kindergartens and childcare elsewhere
    (located to an LSOA, quality "expected")."""
    from lix_core.codes import active_nations, nation_of
    from lix_pipeline.geo.joins import points_to_lsoa

    frames = []
    if "E" in active_nations():
        frames.append(
            pl.read_parquet(data_dir("staged") / "ofsted_childcare.parquet").with_columns(
                pl.lit("ofsted").alias("source")
            )
        )
    if "S" in active_nations():
        frames.append(
            pl.read_parquet(data_dir("staged") / "care_inspectorate.parquet").with_columns(
                pl.lit("care_inspectorate").alias("source")
            )
        )
    if "N" in active_nations():
        frames.append(_de_nurseries().with_columns(pl.lit("de").alias("source")))
    others = [n for n in active_nations() if n not in ("E", "S", "N")]
    if others:
        osm = pl.read_parquet(data_dir("staged") / "osm_pois.parquet").filter(
            pl.col("value").is_in(["kindergarten", "childcare"])
        )
        osm = points_to_lsoa(osm.select("name", "x", "y"), x="x", y="y")
        osm = osm.filter(nation_of("lsoa21cd").is_in(others)).with_columns(
            pl.lit(NEUTRAL).alias("quality"),
            pl.lit("osm: not inspected").alias("quality_source"),
            pl.lit("osm").alias("source"),
        )
        logger.info(f"{osm.height:,} OpenStreetMap nurseries in {others}")
        frames.append(osm)
    return pl.concat(frames, how="diagonal_relaxed").lazy()


DE_NURSERY_FILES = {"de_schools_nursery": "nursery school", "de_schools_preschool": "pre-school"}


def _de_nurseries() -> pl.DataFrame:
    """Northern Ireland's nursery schools and funded pre-school settings from the
    Department of Education's workbooks (places from the enrolment total; no
    inspection grades, so quality is "expected")."""
    from lix_pipeline.stage.geo_ni import load_sheet
    from lix_pipeline.stage.schools import de_pupils

    frames = []
    for slug, subtype in DE_NURSERY_FILES.items():
        path = data_dir("raw") / slug / f"{slug}.xlsx"
        if not path.exists():
            logger.warning(f"{path} not fetched")
            continue
        ref = load_sheet(path, "Reference Data", "DENI ref")
        ref = ref.rename({c: c.strip() for c in ref.columns})
        name_col = next(c for c in ref.columns if c.lower() in ("school name", "centre name"))
        df = ref.select(
            pl.col("DENI ref").cast(pl.Utf8).str.strip_chars().alias("urn"),
            pl.col(name_col).str.strip_chars().alias("name"),
            pl.lit(subtype).alias("subtype"),
            pl.col("Postcode").str.strip_chars().alias("postcode"),
        ).filter(pl.col("urn").is_not_null())
        pupils = de_pupils(load_sheet(path, "Enrolments", "DENI ref")).rename(
            {"urn_text": "urn", "pupils": "places"}
        )
        frames.append(df.join(pupils, on="urn", how="left"))
    if not frames:
        raise FileNotFoundError("No Department of Education nursery workbooks fetched")
    df = pl.concat(frames).with_columns(
        pl.lit(NEUTRAL).alias("quality"), pl.lit("not inspected").alias("quality_source")
    )
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("N",)))
    logger.info(f"{df.height:,} nursery schools and pre-school settings in Northern Ireland (DE)")
    return df


CI_KEY_QUESTIONS = [
    "KQ_Support_Wellbeing",
    "KQ_Care_and_Support_Planning",
    "KQ_Setting",
    "KQ_Staff_Team",
    "KQ_Leadership",
    "KQ_Care_Play_and_Learning",
]


def stage_care_inspectorate() -> pl.LazyFrame:
    """Scotland's registered day care of children (nurseries, playgroups and school nursery
    classes), with places and a quality from the Care Inspectorate's grades: the mean of
    the key questions graded 1 (unsatisfactory) to 6 (excellent), on the 0–1 scale."""
    raw = pl.read_csv(
        data_dir("raw") / "care_inspectorate" / "care_inspectorate.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    df = raw.filter(
        (pl.col("CareService") == "Day Care of Children")
        & pl.col("ServiceStatus").str.to_lowercase().str.starts_with("active")
    )
    grades = [
        pl.col(c).cast(pl.Float64, strict=False) / 6 for c in CI_KEY_QUESTIONS if c in df.columns
    ]
    mean_grade = pl.mean_horizontal(grades)
    df = df.select(
        pl.col("CSNumber").alias("urn"),
        pl.col("ServiceName").alias("name"),
        pl.coalesce("Subtype", "CareService").alias("subtype"),
        pl.col("Service_Postcode").alias("postcode"),
        pl.col("Registered_Places").cast(pl.Int32, strict=False).alias("places"),
        pl.coalesce(mean_grade, pl.lit(NEUTRAL)).alias("quality"),
        pl.when(mean_grade.is_not_null())
        .then(pl.lit("care inspectorate grades"))
        .otherwise(pl.lit("not yet graded"))
        .alias("quality_source"),
    )
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd"))
    logger.info(
        f"{df.height:,} day care of children services, {df['places'].sum():,} places; "
        f"{dict(df['quality_source'].value_counts().rows())}"
    )
    return df.lazy()


def stage_ofsted_childcare() -> pl.LazyFrame:
    raw = _read(data_dir("raw") / "ofsted_childcare" / "ofsted_childcare.csv")
    df = raw.filter(
        (pl.col("Provider type") == "Childcare on non-domestic premises")
        & (pl.col("Provider Early Years Register flag") == "Y")
        & (pl.col("Provider subtype") != "Out-of-school day care")
    )
    reif = [
        pl.col(REIF_PREFIX + area).replace_strict(REPORT_CARD_SCALE, default=None)
        for area in REIF_AREAS
        if REIF_PREFIX + area in df.columns
    ]
    oeif = pl.col(OEIF_OVERALL).replace_strict(OEIF_SCALE, default=None)
    df = df.select(
        pl.col("Provider URN").alias("urn"),
        pl.col("Provider name").alias("name"),
        pl.col("Provider subtype").alias("subtype"),
        pl.col("Provider postcode").alias("postcode"),
        pl.col("Places").cast(pl.Int32, strict=False).alias("places"),
        pl.coalesce(pl.mean_horizontal(reif), oeif, pl.lit(NEUTRAL)).alias("quality"),
        pl.when(pl.mean_horizontal(reif).is_not_null())
        .then(pl.lit("renewed framework"))
        .when(oeif.is_not_null())
        .then(pl.lit("previous framework"))
        .otherwise(pl.lit("not yet inspected"))
        .alias("quality_source"),
    )
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd"))
    logger.info(
        f"{df.height:,} nurseries and pre-schools, {df['places'].sum():,} places; "
        f"{dict(df['quality_source'].value_counts().rows())}"
    )
    return df.lazy()
