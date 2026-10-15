"""Schools: every open establishment (GIAS in England, the Welsh Government's maintained
schools list in Wales) and a blended inspection quality per English school.

Ofsted changed frameworks twice in two years:

- to Sept 2024: graded inspections with an overall grade (1 Outstanding … 4 Inadequate)
- Sept 2024 – Nov 2025: graded sub-judgements but overall effectiveness "Not judged"
- from Nov 2025: report cards grading each area on a five-point scale

so each school is scored from whichever is newest, on one 0–1 scale, and the score
fades towards the national middle as the inspection ages (some "Outstanding" grades
are over a decade old). The scale is a modelling choice documented in methodology.md.
"""

from datetime import date

import polars as pl

from lix_core.codes import in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.joins import points_to_lsoa

logger = setup_logging("stage.schools")

REPORT_CARD_SCALE = {
    "Exceptional": 1.0,
    "Strong standard": 0.8,
    "Expected standard": 0.6,
    "Needs attention": 0.35,
    "Urgent improvement": 0.1,
}
REPORT_CARD_AREAS = {
    "Inclusion": "rc_inclusion",
    "Curriculum and teaching": "rc_curriculum_teaching",
    "Achievement": "rc_achievement",
    "Attendance and behaviour": "rc_attendance_behaviour",
    "Personal development and wellbeing": "rc_personal_development",
    "Leadership and governance": "rc_leadership",
}
# Legacy grades: 1 Outstanding, 2 Good, 3 Requires improvement, 4 Inadequate
OEIF_SCALE = {"1": 0.95, "2": 0.7, "3": 0.4, "4": 0.15}
OEIF_SUB = {
    "Latest OEIF quality of education": "oeif_quality_of_education",
    "Latest OEIF behaviour and attitudes": "oeif_behaviour",
    "Latest OEIF personal development": "oeif_personal_development",
    "Latest OEIF effectiveness of leadership and management": "oeif_leadership",
}
# Ungraded inspections only visit schools previously judged Good or Outstanding, so
# their outcome implies a grade; used when no graded result is on record
UNGRADED_IMPLIED = {
    "School remains Outstanding": 0.95,
    "School remains Outstanding (Concerns) - S5 Next": 0.85,
    "School remains Good (Improving) - S5 Next": 0.75,
    "School remains Good": 0.7,
    "Standards maintained": 0.7,
    "Improved significantly": 0.7,
    "School remains Good (Concerns) - S5 Next": 0.6,
    "Some aspects not as strong": 0.6,
}
# Later ungraded inspections nudge the last graded result
UNGRADED_ADJUSTMENT = {
    "Improved significantly": 0.1,
    "School remains Good (Improving) - S5 Next": 0.05,
    "School remains Outstanding (Concerns) - S5 Next": -0.1,
    "School remains Good (Concerns) - S5 Next": -0.1,
    "Standards maintained": 0.0,
    "School remains Good": 0.0,
    "School remains Outstanding": 0.0,
    "Some aspects not as strong": -0.1,
}
NEUTRAL = 0.6  # "expected standard"
HALF_LIFE_YEARS = 6.0

SCHOOL_TYPES_EXCLUDED = ("Welsh schools", "Universities", "Online provider")


def stage_gias() -> pl.LazyFrame:
    """Open schools and colleges in England with phase, ages, size and location."""
    raw = pl.read_csv(
        data_dir("raw") / "gias" / "gias.csv", encoding="cp1252", infer_schema_length=0
    )
    df = raw.filter(
        pl.col("EstablishmentStatus (name)").str.starts_with("Open")
        & ~pl.col("EstablishmentTypeGroup (name)").is_in(SCHOOL_TYPES_EXCLUDED)
        & (pl.col("Easting").str.len_chars() > 0)
    ).select(
        pl.col("URN").cast(pl.Int64).alias("urn"),
        pl.col("EstablishmentName").alias("name"),
        pl.col("PhaseOfEducation (name)").alias("phase"),
        pl.col("TypeOfEstablishment (name)").alias("type"),
        pl.col("EstablishmentTypeGroup (name)").alias("type_group"),
        pl.col("StatutoryLowAge").cast(pl.Int16, strict=False).alias("low_age"),
        pl.col("StatutoryHighAge").cast(pl.Int16, strict=False).alias("high_age"),
        pl.col("Gender (name)").alias("gender"),
        pl.col("AdmissionsPolicy (name)").alias("admissions_policy"),
        pl.col("NurseryProvision (name)").alias("nursery_provision"),
        pl.col("OfficialSixthForm (name)").alias("sixth_form"),
        pl.col("SchoolCapacity").cast(pl.Int32, strict=False).alias("capacity"),
        pl.col("NumberOfPupils").cast(pl.Int32, strict=False).alias("pupils"),
        pl.col("Postcode").alias("postcode"),
        pl.col("Easting").cast(pl.Float64).alias("x"),
        pl.col("Northing").cast(pl.Float64).alias("y"),
    )
    # GIAS carries an LSOA code, but recompute it so the vintage is certainly 2021
    df = points_to_lsoa(df).filter(in_scope("lsoa21cd"))
    logger.info(f"{df.height:,} open schools and colleges")
    return df.lazy()


# Welsh Government sector names (the DataMapWales layer labels them in Welsh)
WG_SECTOR_PHASE = {
    "meithrin": "Nursery",
    "cynradd": "Primary",
    "canol": "All-through",
    "uwchradd": "Secondary",
    "pob oed": "All-through",
    "oed cyfun": "All-through",
    "arbennig": "Special",
    "nursery": "Nursery",
    "primary": "Primary",
    "middle": "All-through",
    "secondary": "Secondary",
    "all-age": "All-through",
    "all age": "All-through",
    "special": "Special",
}


def wg_phase(sector: pl.Expr, school_type: pl.Expr) -> pl.Expr:
    """GIAS-style phase from the Welsh list's sector, falling back on the type text."""
    by_sector = (
        sector.str.to_lowercase().str.strip_chars().replace_strict(WG_SECTOR_PHASE, default=None)
    )
    t = school_type.str.to_lowercase()
    by_type = (
        pl.when(t.str.contains("special"))
        .then(pl.lit("Special"))
        .when(t.str.contains("secondary") | t.str.contains("uwchradd"))
        .then(pl.lit("Secondary"))
        .when(t.str.contains("middle") | t.str.contains("canol"))
        .then(pl.lit("All-through"))
        .when(t.str.contains("nursery") & ~t.str.contains("infants") & ~t.str.contains("juniors"))
        .then(pl.lit("Nursery"))
        .when(t.str.contains("infants") | t.str.contains("juniors") | t.str.contains("primary"))
        .then(pl.lit("Primary"))
    )
    return pl.coalesce(by_sector, by_type)


def stage_wg_schools() -> pl.LazyFrame:
    """Maintained schools in Wales, shaped like the GIAS table (no inspection grades)."""
    raw = pl.read_csv(data_dir("raw") / "wg_schools" / "wg_schools.csv", infer_schema_length=0)
    xy = raw["geom"].str.extract_all(r"[-\d.]+")
    df = raw.select(
        pl.col("school_code").cast(pl.Int64).alias("urn"),
        pl.col("school_name").alias("name"),
        wg_phase(pl.col("sector"), pl.col("school_type")).alias("phase"),
        pl.col("school_type").alias("type"),
        pl.when(pl.col("sector").str.to_lowercase().str.contains("arbennig|special"))
        .then(pl.lit("Special schools"))
        .otherwise(pl.lit("Welsh maintained schools"))
        .alias("type_group"),
        pl.lit(None, pl.Int16).alias("low_age"),
        pl.lit(None, pl.Int16).alias("high_age"),
        pl.lit(None, pl.Utf8).alias("gender"),
        pl.lit(None, pl.Utf8).alias("admissions_policy"),
        pl.when(pl.col("school_type").str.to_lowercase().str.contains("nursery|meithrin"))
        .then(pl.lit("Has Nursery Classes"))
        .otherwise(pl.lit("No Nursery Classes"))
        .alias("nursery_provision"),
        pl.lit(None, pl.Utf8).alias("sixth_form"),
        pl.lit(None, pl.Int32).alias("capacity"),
        pl.col("pupils").cast(pl.Int32, strict=False).alias("pupils"),
        pl.col("postcode"),
        xy.list.get(0).cast(pl.Float64).alias("x"),
        xy.list.get(1).cast(pl.Float64).alias("y"),
        pl.col("welsh_medium"),
        pl.col("local_authority"),
    )
    unmapped = df.filter(pl.col("phase").is_null())
    if unmapped.height:
        kinds = unmapped.select("type").unique()["type"].to_list()[:8]
        logger.warning(f"{unmapped.height} Welsh schools with no phase, e.g. types {kinds}")
    df = points_to_lsoa(df).filter(in_scope("lsoa21cd"))
    logger.info(f"{df.height:,} maintained schools in Wales")
    return df.lazy()


# The Scottish school roll's SchoolType → GIAS-style phase and type group
SG_PHASE = {"Primary": "Primary", "Secondary": "Secondary", "Special": "Special"}


def stage_sg_schools() -> pl.LazyFrame:
    """Publicly funded schools in Scotland from the Scottish Government's school roll and
    locations layer, shaped like the GIAS table (no inspection grades; the SEED code is
    the ``urn``). School nursery classes are registered with the Care Inspectorate, so
    ``nursery_provision`` stays "No Nursery Classes" to avoid counting them twice."""
    import pyogrio

    folder = data_dir("raw") / "sg_school_roll"
    shp = next(iter(sorted(folder.glob("**/*.shp"))), None)
    if shp is None:
        raise FileNotFoundError(f"No school roll shapefile under {folder}")
    raw = pl.from_pandas(pyogrio.read_dataframe(shp, read_geometry=False))
    df = raw.select(
        pl.col("SeedCode").cast(pl.Int64).alias("urn"),
        pl.col("SchoolName").alias("name"),
        pl.col("SchoolType").replace_strict(SG_PHASE, default=None).alias("phase"),
        pl.col("CentreType").alias("type"),
        pl.when(pl.col("SchoolType") == "Special")
        .then(pl.lit("Special schools"))
        .otherwise(pl.lit("Scottish publicly funded schools"))
        .alias("type_group"),
        pl.lit(None, pl.Int16).alias("low_age"),
        pl.lit(None, pl.Int16).alias("high_age"),
        pl.lit(None, pl.Utf8).alias("gender"),
        pl.lit(None, pl.Utf8).alias("admissions_policy"),
        pl.lit("No Nursery Classes").alias("nursery_provision"),
        pl.lit(None, pl.Utf8).alias("sixth_form"),
        pl.lit(None, pl.Int32).alias("capacity"),
        pl.col("PupilRoll").cast(pl.Int32, strict=False).alias("pupils"),
        pl.col("PostCode").alias("postcode"),
        pl.col("GridRefEas").cast(pl.Float64).alias("x"),
        pl.col("GridRefNor").cast(pl.Float64).alias("y"),
        pl.lit(None, pl.Utf8).alias("welsh_medium"),
        pl.col("LAName").alias("local_authority"),
    ).filter(pl.col("urn").is_not_null() & pl.col("x").is_not_null())
    df = points_to_lsoa(df).filter(in_scope("lsoa21cd"))
    logger.info(f"{df.height:,} publicly funded schools in Scotland")
    return df.lazy()


def stage_sg_attainment() -> pl.LazyFrame:
    """Each Scottish secondary's average leaver tariff score for the latest academic year:
    the Attainment for All table gives the lowest 20%, middle 60% and highest 20% of
    attainers, so the school's mean is their 1:3:1 average."""
    raw = pl.read_csv(
        data_dir("raw") / "sg_attainment" / "sg_attainment.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    schools = raw.filter(
        (pl.col("School Comparator") == "Real Establishment")
        & pl.col("GeographyCode").str.contains(r"^\d{7}$")
        & pl.col("Value").is_not_null()
    ).with_columns(pl.col("Value").cast(pl.Float64, strict=False))
    latest = schools["DateCode"].max()
    weights = {"Lowest 20%": 0.2, "Middle 60%": 0.6, "Highest 20%": 0.2}
    out = (
        schools.filter(pl.col("DateCode") == latest)
        .with_columns(
            pl.col("Attainment Distribution").replace_strict(weights, default=None).alias("_w")
        )
        .filter(pl.col("_w").is_not_null() & pl.col("Value").is_not_null())
        .group_by(pl.col("GeographyCode").cast(pl.Int64).alias("urn"))
        .agg(
            ((pl.col("Value") * pl.col("_w")).sum() / pl.col("_w").sum()).alias("tariff_avg"),
            pl.col("_w").count().alias("_parts"),
        )
        .filter(pl.col("_parts") == 3)
        .with_columns(pl.lit(latest).alias("period"))
        .drop("_parts")
        .sort("urn")
    )
    logger.info(f"{out.height:,} Scottish secondaries with a leaver tariff for {latest}")
    return out.lazy()


# Department of Education workbooks → phase and type group
DE_FILES = {
    "de_schools_primary": ("Primary", "Northern Irish grant-aided schools"),
    "de_schools_post_primary": ("Secondary", "Northern Irish grant-aided schools"),
    "de_schools_special": ("Special", "Special schools"),
}


def de_pupils(enrolments: pl.DataFrame) -> pl.DataFrame:
    """``urn, pupils`` from a DE Enrolments sheet: the total column when there is one,
    else the sum of the year-group columns."""
    ref = next(c for c in enrolments.columns if c.strip().lower() == "deni ref")
    totals = [c for c in enrolments.columns if c.strip().lower().startswith("total")]
    if totals:
        total = next((c for c in totals if "total" in c.lower().split(":")[-1]), totals[-1])
        expr = pl.col(total).cast(pl.Float64, strict=False)
    else:
        years = [c for c in enrolments.columns if c not in (ref,) and ":" in c]
        expr = pl.sum_horizontal([pl.col(c).cast(pl.Float64, strict=False) for c in years])
    return enrolments.select(
        pl.col(ref).cast(pl.Utf8).str.strip_chars().alias("urn_text"),
        expr.round().cast(pl.Int32).alias("pupils"),
    )


def stage_de_schools() -> pl.LazyFrame:
    """Northern Ireland's grant-aided primary, post-primary and special schools from the
    Department of Education's school-level workbooks, shaped like the GIAS table: no
    inspection grades or results are published, so quality is not available; the DE
    reference number is the ``urn``; located by postcode."""
    from lix_pipeline.stage.geo_ni import load_sheet

    frames = []
    for slug, (phase, group) in DE_FILES.items():
        path = data_dir("raw") / slug / f"{slug}.xlsx"
        if not path.exists():
            logger.warning(f"{path} not fetched")
            continue
        ref = load_sheet(path, "Reference Data", "DENI ref")
        ref = ref.rename({c: c.strip() for c in ref.columns})
        df = ref.select(
            pl.col("DENI ref").cast(pl.Utf8).str.strip_chars().alias("urn_text"),
            pl.col("School name").str.strip_chars().alias("name"),
            pl.lit(phase).alias("phase"),
            pl.col("School Type").alias("type"),
            pl.lit(group).alias("type_group"),
            pl.lit(None, pl.Int16).alias("low_age"),
            pl.lit(None, pl.Int16).alias("high_age"),
            pl.lit(None, pl.Utf8).alias("gender"),
            pl.col("Management Type").alias("admissions_policy"),
            pl.lit("No Nursery Classes").alias("nursery_provision"),
            pl.lit(None, pl.Utf8).alias("sixth_form"),
            pl.lit(None, pl.Int32).alias("capacity"),
            pl.col("Postcode").str.strip_chars().alias("postcode"),
            pl.lit(None, pl.Utf8).alias("welsh_medium"),
            pl.col("District Council (2014)").alias("local_authority"),
        ).filter(pl.col("urn_text").is_not_null())
        import fastexcel

        sheets = fastexcel.read_excel(path).sheet_names
        if "Enrolments" in sheets:
            df = df.join(
                de_pupils(load_sheet(path, "Enrolments", "DENI ref")), on="urn_text", how="left"
            )
        else:
            df = df.with_columns(pl.lit(None, pl.Int32).alias("pupils"))
        frames.append(df)
    if not frames:
        raise FileNotFoundError("No Department of Education school workbooks fetched")
    df = pl.concat(frames, how="diagonal_relaxed")
    # DE references are seven digits for schools (pre-school settings carry letters and
    # are not in these files)
    df = df.with_columns(pl.col("urn_text").cast(pl.Int64, strict=False).alias("urn")).drop(
        "urn_text"
    )
    from lix_pipeline.stage.health import geocode_postcodes

    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("N",)))
    logger.info(f"{df.height:,} grant-aided schools in Northern Ireland (DE)")
    return df.lazy()


def stage_schools() -> pl.LazyFrame:
    """One table of schools for every active nation: GIAS, the Welsh list and the Scottish
    school roll, each row saying which (``source``)."""
    from lix_core.codes import active_nations

    frames = []
    if "E" in active_nations():
        frames.append(
            pl.read_parquet(data_dir("staged") / "gias.parquet").with_columns(
                pl.lit("gias").alias("source")
            )
        )
    if "W" in active_nations():
        frames.append(
            pl.read_parquet(data_dir("staged") / "wg_schools.parquet").with_columns(
                pl.lit("wg").alias("source")
            )
        )
    if "S" in active_nations():
        frames.append(
            pl.read_parquet(data_dir("staged") / "sg_schools.parquet").with_columns(
                pl.lit("sg").alias("source")
            )
        )
    if "N" in active_nations():
        frames.append(
            pl.read_parquet(data_dir("staged") / "de_schools.parquet").with_columns(
                pl.lit("de").alias("source")
            )
        )
    df = pl.concat(frames, how="diagonal_relaxed").sort("urn")
    logger.info(f"{df.height:,} schools across {active_nations()}")
    return df.lazy()


def _date(col: str) -> pl.Expr:
    return pl.col(col).replace("NULL", None).str.to_date("%d/%m/%Y", strict=False)


def _grade(col: str, scale: dict[str, float]) -> pl.Expr:
    return pl.col(col).replace_strict(scale, default=None, return_dtype=pl.Float64)


def school_quality(df: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """Add ``quality`` (0–1), ``framework`` and ``inspection_date`` to staged Ofsted rows."""
    rc_cols = list(REPORT_CARD_AREAS.values())
    sub_cols = list(OEIF_SUB.values())
    df = df.with_columns(
        pl.mean_horizontal(rc_cols).alias("_rc"),
        pl.mean_horizontal(sub_cols).alias("_sub"),
    )
    implied = pl.col("ungraded_outcome").replace_strict(UNGRADED_IMPLIED, default=None)
    base = (
        pl.when(pl.col("_rc").is_not_null())
        .then(
            # Unmet safeguarding outweighs everything else
            pl.when(pl.col("rc_safeguarding_met") == False)  # noqa: E712
            .then(pl.min_horizontal(pl.col("_rc"), pl.lit(0.2)))
            .otherwise(pl.col("_rc"))
        )
        .when(pl.col("oeif_overall").is_not_null())
        .then(pl.col("oeif_overall"))
        .when(pl.col("_sub").is_not_null())
        .then(pl.col("_sub"))
        .otherwise(implied)
    )
    framework = (
        pl.when(pl.col("_rc").is_not_null())
        .then(pl.lit("report_card"))
        .when(pl.col("oeif_overall").is_not_null())
        .then(pl.lit("oeif"))
        .when(pl.col("_sub").is_not_null())
        .then(pl.lit("oeif_not_judged"))
        .when(implied.is_not_null())
        .then(pl.lit("ungraded_only"))
    )
    graded_date = pl.when(pl.col("_rc").is_not_null()).then("rc_date").otherwise("oeif_date")
    # An ungraded inspection adjusts a graded one only if it came after it; with no
    # graded result on record it is the evidence itself (and has no adjustment)
    later_ungraded = (pl.col("ungraded_date") > graded_date) | (
        graded_date.is_null() & pl.col("ungraded_date").is_not_null()
    )
    adjust = (
        pl.when(later_ungraded & pl.col("_rc").is_null() & graded_date.is_not_null())
        .then(pl.col("ungraded_outcome").replace_strict(UNGRADED_ADJUSTMENT, default=0.0))
        .otherwise(0.0)
    )
    inspected = pl.when(later_ungraded).then("ungraded_date").otherwise(graded_date)
    age_years = (pl.lit(as_of) - inspected).dt.total_days() / 365.25
    fade = (2.0 ** (-age_years / HALF_LIFE_YEARS)).fill_null(0.0)
    quality = (NEUTRAL + ((base + adjust).clip(0, 1) - NEUTRAL) * fade).clip(0, 1)
    return df.with_columns(
        quality.alias("quality"),
        framework.alias("framework"),
        inspected.alias("inspection_date"),
        age_years.alias("inspection_age_years"),
    ).drop("_rc", "_sub")


def stage_ofsted_schools(as_of: date | None = None) -> pl.LazyFrame:
    """Latest inspection per state-funded school with a blended quality score."""
    raw = pl.read_csv(
        data_dir("raw") / "ofsted_schools" / "ofsted_schools.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    rc_dates = [f"{label} - date of grade" for label in REPORT_CARD_AREAS]
    df = raw.select(
        pl.col("URN").cast(pl.Int64).alias("urn"),
        pl.col("Ofsted phase").alias("ofsted_phase"),
        *[
            _grade(label, REPORT_CARD_SCALE).alias(name)
            for label, name in REPORT_CARD_AREAS.items()
        ],
        pl.col("Safeguarding standards")
        .replace({"NULL": None})
        .eq("Met")
        .alias("rc_safeguarding_met"),
        pl.max_horizontal(_date(c) for c in rc_dates).alias("rc_date"),
        _grade("Latest OEIF overall effectiveness", OEIF_SCALE).alias("oeif_overall"),
        *[_grade(label, OEIF_SCALE).alias(name) for label, name in OEIF_SUB.items()],
        _date("Inspection start date of latest OEIF graded inspection").alias("oeif_date"),
        pl.col("Ungraded inspection overall outcome")
        .replace("NULL", None)
        .alias("ungraded_outcome"),
        _date("Date of latest ungraded inspection").alias("ungraded_date"),
        pl.col("Category of concern").replace("NULL", None).alias("category_of_concern"),
    )
    df = school_quality(df, as_of or date.today())
    summary = df.group_by("framework").agg(pl.len(), pl.col("quality").mean().round(3))
    logger.info(f"{df.height:,} inspected schools; by framework: {summary.rows()}")
    return df.lazy()


def stage_ks2_results() -> pl.LazyFrame:
    """Share of pupils meeting the expected standard in reading, writing and maths.

    The three-year average (steadier for small primaries) where published, otherwise
    the latest year. Special schools and suppressed results are left out downstream.
    """
    raw = pl.scan_csv(data_dir("raw") / "ks2_results" / "ks2_results.csv", infer_schema=False)
    rwm = raw.filter(pl.col("subject") == "Reading, writing and maths").with_columns(
        pl.col("expected_standard_pupil_percent").cast(pl.Float64, strict=False).alias("rwm_pct")
    )
    urn = pl.col("school_urn").cast(pl.Int64, strict=False).alias("urn")  # as in GIAS
    three_year = rwm.filter(pl.col("breakdown") == "3 year average").select(
        urn, pl.col("rwm_pct").alias("rwm_pct_3y")
    )
    latest = rwm.select(pl.col("time_period").max()).collect().item()
    one_year = rwm.filter(
        (pl.col("breakdown") == "Total") & (pl.col("time_period") == latest)
    ).select(urn, "rwm_pct")
    df = (
        one_year.join(three_year, on="urn", how="full", coalesce=True)
        .with_columns(
            pl.coalesce("rwm_pct_3y", "rwm_pct").alias("ks2_rwm_pct"),
            pl.lit(latest).alias("period"),
        )
        .filter(pl.col("ks2_rwm_pct").is_not_null())
        .unique("urn")
        .collect()
    )
    logger.info(
        f"KS2: {df.height:,} primaries ({latest}); "
        f"median {df['ks2_rwm_pct'].median():.0f}% meet the standard"
    )
    return df.lazy()


def stage_ks4_results() -> pl.LazyFrame:
    """Average Attainment 8 per state secondary school, all pupils, latest year."""
    raw = pl.scan_csv(data_dir("raw") / "ks4_results" / "ks4_results.csv", infer_schema=False)
    latest = raw.select(pl.col("time_period").max()).collect().item()
    all_pupils = ("breakdown", "sex", "disadvantage_status", "first_language",
                  "prior_attainment", "mobility")  # fmt: skip
    totals = [pl.col(c) == "Total" for c in all_pupils]
    df = (
        raw.filter(pl.all_horizontal(totals) & (pl.col("time_period") == latest))
        .filter(~pl.col("establishment_type_group").str.contains("(?i)independent|special"))
        .select(
            pl.col("school_urn").cast(pl.Int64, strict=False).alias("urn"),
            pl.col("attainment8_average").cast(pl.Float64, strict=False).alias("attainment8"),
            pl.col("pupil_count").cast(pl.Int32, strict=False).alias("pupils"),
            pl.lit(latest).alias("period"),
        )
        .filter(pl.col("attainment8").is_not_null() & (pl.col("pupils") >= 10))
        .unique("urn")
        .collect()
    )
    logger.info(
        f"KS4: {df.height:,} state secondaries ({latest}); "
        f"median Attainment 8 {df['attainment8'].median():.1f}"
    )
    return df.lazy()


def stage_wg_ks4_la() -> pl.LazyFrame:
    """Key Stage 4 attainment by Welsh local authority (Capped 9 points score).

    Wales publishes no per-school results or inspection grades in open data, so the
    authority's score stands in for its schools (flagged ``broadcast_lad``).
    """
    import fastexcel

    reader = fastexcel.read_excel(data_dir("raw") / "wg_ks4_la" / "wg_ks4_la.ods")
    sheet = next(s for s in reader.sheet_names if s.endswith("_GCSE_indicators_LA"))
    probe = reader.load_sheet_by_name(sheet, header_row=None, n_rows=12).to_polars()
    first = probe[probe.columns[0]].to_list()
    header = next(i for i, v in enumerate(first) if str(v).startswith("Local authority code"))
    df = reader.load_sheet_by_name(sheet, header_row=header).to_polars()
    score = next(c for c in df.columns if c.startswith("Capped 9"))
    df = df.select(
        pl.col(df.columns[1]).str.strip_chars().alias("lad_nm"),
        pl.col(score).cast(pl.Float64, strict=False).alias("capped9"),
    ).filter(pl.col("capped9").is_not_null())
    lads = (
        pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet")
        .filter(in_scope("lad_cd", "upper", nations=("W",)))
        .select("lad_cd", "lad_nm")
        .unique()
    )
    # Spellings differ between publications ("Rhondda Cynon Taff" vs the ONS "Taf")
    key = lambda c: (  # noqa: E731
        pl.col(c)
        .str.to_lowercase()
        .str.replace_all(r"^the ", "")
        .str.replace_all("taff", "taf")
        .str.replace_all(r"\s+", "")
    )
    out = df.with_columns(key("lad_nm").alias("_key")).join(
        lads.with_columns(key("lad_nm").alias("_key")).drop("lad_nm"), on="_key", how="left"
    )
    missing = out.filter(pl.col("lad_cd").is_null())["lad_nm"].to_list()
    if missing:
        logger.warning(f"Welsh authorities not matched to a code: {missing}")
    out = out.filter(pl.col("lad_cd").is_not_null()).select("lad_cd", "lad_nm", "capped9")
    logger.info(f"{out.height} Welsh authorities; Capped 9 median {out['capped9'].median():.1f}")
    return out.lazy()
