"""NHS GP practices: locations (ODS), real catchments (registrations by LSOA) and staffing.

Joining registrations to staffing gives each LSOA the GP capacity of the practices its
residents actually use, rather than of whichever practice happens to be nearest.
"""

import polars as pl

from lix_core.codes import active_nations, in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.nspl import load_nspl

logger = setup_logging("stage.health")

# Positions in the headerless ODS DSE "epraccur" report (1-based in NHS documentation)
EPRACCUR_COLUMNS = {
    1: "practice_code",
    2: "name",
    10: "postcode",
    11: "open_date",
    12: "close_date",
    13: "status",
    15: "commissioner",
    26: "role",
}
GP_PRACTICE_ROLE = "RO76"

OGL = "OGL-3.0"
ODBL = "ODbL-1.0"
# A clinic in OpenStreetMap is a GP practice when it says so; a doctors' surgery is one
# unless its name or speciality says otherwise (Ireland has no open GP register)
GP_NAME = (
    r"(?i)\b(gp|g\.p\.|general practi|medical (centre|center|practice|hall)|"
    r"family (practice|doctor|medicine)|surgery|health centre|doctors?|primary care)\b"
)
NOT_GP_NAME = (
    r"(?i)(chiropod|podiatr|physio|dermat|cosmetic|aesthetic|laser|dental|dentist|optic|optom|"
    r"psych|counsel|osteopath|chiropract|acupunct|veterinar|\bvet\b|skin|hair|beauty|botox|"
    r"fertility|ivf|maternity hospital|hospital\b|ultrasound|x-?ray|scan|audiolog|hearing)"
)
NOT_GP_SPECIALITY = (
    "podiatry", "chiropody", "dermatology", "physiotherapy", "psychiatry", "psychology",
    "optometry", "dentistry", "osteopathy", "chiropractic", "acupuncture", "cosmetic",
    "fertility", "radiology", "audiology", "occupational", "sexual_health", "travel",
)  # fmt: skip


def geocode_postcodes(df: pl.DataFrame, postcode_col: str = "postcode") -> pl.DataFrame:
    """Add BNG ``x``/``y`` and ``lsoa21cd`` from NSPL (terminated postcodes included)."""
    nspl = (
        load_nspl(live_only=False, nations=None)
        .select("postcode_norm", "east1m", "north1m", "lsoa21cd")
        .unique("postcode_norm", keep="first")
        .collect()
    )
    norm = pl.col(postcode_col).str.replace_all(r"\s", "").str.to_uppercase()
    return (
        df.with_columns(norm.alias("_pc"))
        .join(nspl, left_on="_pc", right_on="postcode_norm", how="left")
        .rename({"east1m": "x", "north1m": "y"})
        .drop("_pc")
    )


def stage_ods_gp() -> pl.LazyFrame:
    """Active GP practices with their location (ODS lists England and Wales)."""
    raw = pl.read_csv(
        data_dir("raw") / "ods_gp" / "ods_gp.csv", has_header=False, infer_schema=False
    )
    df = raw.select(
        pl.col(f"column_{i}").alias(name) for i, name in EPRACCUR_COLUMNS.items()
    ).filter((pl.col("status") == "ACTIVE") & (pl.col("role") == GP_PRACTICE_ROLE))
    df = (
        geocode_postcodes(df)
        .filter(in_scope("lsoa21cd"))
        .drop("role", "status")
        # Every row says where it comes from, so the map's points and the API credit it
        .with_columns(pl.lit("NHS England ODS").alias("source"), pl.lit(OGL).alias("licence"))
    )
    if "S" in active_nations():
        df = pl.concat([df, _phs_gp_practices()], how="diagonal_relaxed")
    if "N" in active_nations():
        df = pl.concat([df, _bso_gp_practices()], how="diagonal_relaxed")
    if "I" in active_nations():
        df = pl.concat([df, _osm_gp_practices(("I",))], how="diagonal_relaxed")
    logger.info(f"{df.height:,} active GP practices")
    return df.lazy()


def _osm_points(nations: tuple[str, ...], values: list[str], what: str) -> pl.DataFrame:
    """OpenStreetMap amenities of ``values`` placed in ``nations``, for nations without a
    machine-readable register (Ireland: the HSE publishes no current GP, dental or
    pharmacy list)."""
    from lix_core.codes import nation_of
    from lix_pipeline.geo.joins import points_to_lsoa

    osm = pl.read_parquet(data_dir("staged") / "osm_pois.parquet").filter(
        (pl.col("key") == "amenity") & pl.col("value").is_in(values)
    )
    extra = [c for c in ("healthcare", "healthcare:speciality") if c in osm.columns]
    osm = points_to_lsoa(
        osm.select("osm_type", "osm_id", "name", "value", "x", "y", *extra), x="x", y="y"
    )
    osm = osm.filter(nation_of("lsoa21cd").is_in(list(nations)))
    logger.info(f"{osm.height:,} OpenStreetMap {what} in {nations}")
    return osm


def is_gp_practice(df: pl.DataFrame) -> pl.Expr:
    """Which OpenStreetMap doctors and clinics are GP practices: a doctors' surgery unless
    its name or ``healthcare:speciality`` says it is something else (a chiropodist, a
    skin clinic), a clinic only when its name says GP, surgery, medical or health centre.
    """
    name = pl.col("name").fill_null("")
    speciality = (
        pl.col("healthcare:speciality").fill_null("")
        if "healthcare:speciality" in df.columns
        else pl.lit("")
    )
    other = name.str.contains(NOT_GP_NAME) | pl.any_horizontal(
        speciality.str.contains(s) for s in NOT_GP_SPECIALITY
    )
    says_gp = name.str.contains(GP_NAME) | (speciality == "general")
    # A doctors' surgery is a GP practice unless it says otherwise; a clinic only when it
    # says it is one
    return pl.when(pl.col("value") == "doctors").then(~other).otherwise(says_gp & ~other)


def _osm_gp_practices(nations: tuple[str, ...]) -> pl.DataFrame:
    """GP practices from OpenStreetMap (``amenity=doctors|clinic`` that read as GP
    practices, see ``is_gp_practice``), without list sizes, opening dates or a
    commissioner."""
    osm = _osm_points(nations, ["doctors", "clinic"], "GP surgeries and clinics")
    kept = osm.filter(is_gp_practice(osm))
    logger.info(f"{kept.height:,} of them read as GP practices")
    return kept.select(
        pl.concat_str([pl.lit("osm:"), pl.col("osm_type"), pl.col("osm_id").cast(pl.Utf8)]).alias(
            "practice_code"
        ),
        "name",
        pl.lit(None, pl.Utf8).alias("postcode"),
        pl.lit(None, pl.Utf8).alias("open_date"),
        pl.lit(None, pl.Utf8).alias("close_date"),
        pl.lit(None, pl.Utf8).alias("commissioner"),
        pl.lit(None, pl.Int32).alias("list_size"),
        "lsoa21cd",
        "x",
        "y",
        pl.lit("OpenStreetMap").alias("source"),
        pl.lit(ODBL).alias("licence"),
    )


def _bso_gp_practices() -> pl.DataFrame:
    """Northern Ireland's GP practices from the Business Services Organisation's reference
    file, with their registered patients."""
    raw = pl.read_csv(
        data_dir("raw") / "bso_gp_list_sizes" / "bso_gp_list_sizes.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    raw = raw.rename({c: c.strip() for c in raw.columns})
    df = raw.select(
        pl.col("PracNo").str.strip_chars().alias("practice_code"),
        pl.col("PracticeName").str.strip_chars().alias("name"),
        pl.col("Postcode").str.strip_chars().alias("postcode"),
        pl.lit(None, pl.Utf8).alias("open_date"),
        pl.lit(None, pl.Utf8).alias("close_date"),
        pl.col("LCG").alias("commissioner"),
        pl.col("Registered_Patients").cast(pl.Int32, strict=False).alias("list_size"),
    ).filter(pl.col("practice_code").is_not_null() & pl.col("postcode").is_not_null())
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("N",)))
    logger.info(f"{df.height:,} GP practices in Northern Ireland (BSO)")
    return df.with_columns(
        pl.lit("Business Services Organisation").alias("source"), pl.lit(OGL).alias("licence")
    )


def _phs_gp_practices() -> pl.DataFrame:
    """Scotland's GP practices from Public Health Scotland, with their list sizes."""
    raw = pl.read_csv(
        data_dir("raw") / "phs_gp_practices" / "phs_gp_practices.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    df = raw.select(
        pl.col("PracticeCode").alias("practice_code"),
        pl.col("GPPracticeName").alias("name"),
        pl.col("Postcode").alias("postcode"),
        pl.lit(None, pl.Utf8).alias("open_date"),
        pl.lit(None, pl.Utf8).alias("close_date"),
        pl.col("HB").alias("commissioner"),
        pl.col("PracticeListSize").cast(pl.Int32, strict=False).alias("list_size"),
    )
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("S",)))
    logger.info(f"{df.height:,} GP practices in Scotland (PHS)")
    return df.with_columns(
        pl.lit("Public Health Scotland").alias("source"), pl.lit(OGL).alias("licence")
    )


def stage_gp_registrations() -> pl.LazyFrame:
    """Registered patients by practice and the LSOA they live in."""
    path = data_dir("raw") / "gp_registrations" / "gp-reg-pat-prac-lsoa-all.csv"
    df = (
        pl.read_csv(path, infer_schema_length=0)
        .filter((pl.col("SEX") == "ALL") & in_scope("LSOA_CODE"))
        .select(
            pl.col("PRACTICE_CODE").alias("practice_code"),
            pl.col("LSOA_CODE").alias("lsoa21cd"),
            pl.col("NUMBER_OF_PATIENTS").cast(pl.Int32).alias("patients"),
            pl.col("EXTRACT_DATE").str.to_date().alias("extract_date"),
        )
    )
    logger.info(f"{df['patients'].sum():,} patients in {df['lsoa21cd'].n_unique():,} LSOAs")
    return df.lazy()


def stage_gp_workforce() -> pl.LazyFrame:
    """Full-time-equivalent staff per practice (GPs excluding trainees, nurses, others)."""
    files = sorted((data_dir("raw") / "gp_workforce").glob("*High level.csv"))
    if not files:
        raise FileNotFoundError("No high-level practice CSV in data/raw/gp_workforce")
    df = pl.read_csv(files[-1], infer_schema_length=0).filter(pl.col("MEASURE") == "FTE")
    value = pl.col("VALUE").cast(pl.Float64, strict=False)  # "NA" for suppressed values
    role = pl.col("DETAILED_STAFF_ROLE")
    group = pl.col("STAFF_GROUP")
    # Sum individual roles: the published "Total" row is 0 for some staffed practices
    roles = group.is_not_null() & (role != "Total")
    trainee = role.str.starts_with("GP in Training")
    out = df.group_by(pl.col("PRAC_CODE").alias("practice_code")).agg(
        value.filter(roles & (group == "GP")).sum().alias("gp_fte"),
        value.filter(roles & (group == "GP") & ~trainee).sum().alias("qualified_gp_fte"),
        value.filter(roles & (group == "Nurses")).sum().alias("nurse_fte"),
        value.filter(roles & (group == "Direct Patient Care")).sum().alias("direct_care_fte"),
        pl.lit(files[-1].stem).first().alias("source_file"),
    )
    logger.info(f"{out.height:,} practices, {out['qualified_gp_fte'].sum():,.0f} qualified GP FTE")
    return out.lazy()


# Positions in the headerless ODS DSE "egdpprac" report (same layout as epraccur)
EGDPPRAC_COLUMNS = {
    1: "practice_code",
    2: "name",
    10: "postcode",
    11: "open_date",
    12: "close_date",
    13: "status",
}


def stage_ods_dentists() -> pl.LazyFrame:
    """Active dental practices with an NHS contract, with their location."""
    raw = pl.read_csv(
        data_dir("raw") / "ods_dentists" / "ods_dentists.csv", has_header=False, infer_schema=False
    )
    df = raw.select(
        pl.col(f"column_{i}").alias(name) for i, name in EGDPPRAC_COLUMNS.items()
    ).filter(pl.col("status") == "ACTIVE")
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd")).drop("status")
    if "S" in active_nations():
        df = pl.concat([df, _phs_dentists()], how="diagonal_relaxed")
    if "N" in active_nations():
        df = pl.concat([df, _bso_dentists()], how="diagonal_relaxed")
    if "I" in active_nations():
        osm = _osm_points(("I",), ["dentist"], "dental practices")
        df = pl.concat(
            [
                df,
                osm.select(
                    pl.concat_str(
                        [pl.lit("osm:"), pl.col("osm_type"), pl.col("osm_id").cast(pl.Utf8)]
                    ).alias("practice_code"),
                    "name",
                    "lsoa21cd",
                    "x",
                    "y",
                ),
            ],
            how="diagonal_relaxed",
        )
    logger.info(f"{df.height:,} active dental practices")
    return df.lazy()


def _bso_dentists() -> pl.DataFrame:
    """Northern Ireland's health-service dental surgeries (BSO's surgery list)."""
    raw = pl.read_csv(
        data_dir("raw") / "bso_dental" / "bso_dental.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    raw = raw.rename({c: c.strip() for c in raw.columns})
    postcode = next(c for c in raw.columns if c.upper() == "POSTCODE")
    address = next(c for c in raw.columns if c.upper() == "ADDRESS")
    df = raw.select(
        pl.col(address).str.strip_chars().alias("name"),
        pl.col(postcode).str.strip_chars().alias("postcode"),
    ).filter(pl.col("postcode").is_not_null())
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("N",)))
    logger.info(f"{df.height:,} dental surgeries in Northern Ireland (BSO)")
    return df


def _phs_dentists() -> pl.DataFrame:
    """Scotland's NHS dental practices from Public Health Scotland."""
    raw = pl.read_csv(
        data_dir("raw") / "phs_dentists" / "phs_dentists.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    df = raw.select(
        pl.col("Dental_Practice_Code").alias("practice_code"),
        pl.col("address1").alias("name"),
        pl.col("pc7").alias("postcode"),
        pl.lit(None, pl.Utf8).alias("open_date"),
        pl.lit(None, pl.Utf8).alias("close_date"),
    )
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("S",)))
    logger.info(f"{df.height:,} NHS dental practices in Scotland (PHS)")
    return df


# Contract types that dispense to the public; DAC (appliance contractors) supply
# stoma and incontinence products by delivery
PHARMACY_CONTRACTS = ("Community", "LPS")


def stage_pharmacies() -> pl.LazyFrame:
    """Community pharmacies for every active nation: NHSBSA's list in England (with
    opening hours), Public Health Scotland's dispenser list in Scotland, OpenStreetMap
    elsewhere (no hours; Wales has no machine-readable list)."""
    from lix_core.codes import active_nations, nation_of
    from lix_pipeline.geo.joins import points_to_lsoa

    frames = []
    if "E" in active_nations():
        frames.append(
            pl.read_parquet(data_dir("staged") / "nhsbsa_pharmacies.parquet").with_columns(
                pl.lit("nhsbsa").alias("source")
            )
        )
    if "S" in active_nations():
        frames.append(_phs_pharmacies())
    if "N" in active_nations():
        frames.append(_bso_pharmacies())
    others = [n for n in active_nations() if n not in ("E", "S", "N")]
    if others:
        osm = pl.read_parquet(data_dir("staged") / "osm_pois.parquet").filter(
            pl.col("value") == "pharmacy"
        )
        osm = points_to_lsoa(osm.select("name", "x", "y", "opening_hours"), x="x", y="y")
        osm = osm.filter(nation_of("lsoa21cd").is_in(others)).with_columns(
            pl.lit("osm").alias("source")
        )
        logger.info(f"{osm.height:,} OpenStreetMap pharmacies in {others}")
        frames.append(osm)
    return pl.concat(frames, how="diagonal_relaxed").lazy()


def _phs_pharmacies() -> pl.DataFrame:
    """Scotland's community pharmacies from Public Health Scotland's dispenser list."""
    raw = pl.read_csv(
        data_dir("raw") / "phs_pharmacies" / "phs_pharmacies.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    df = raw.select(
        pl.col("DispCode").alias("pharmacy_code"),
        pl.col("DispLocationName").alias("name"),
        pl.col("DispLocationPostcode").alias("postcode"),
        pl.lit("phs").alias("source"),
    )
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("S",)))
    logger.info(f"{df.height:,} community pharmacies in Scotland (PHS)")
    return df


def _bso_pharmacies() -> pl.DataFrame:
    """Northern Ireland's community pharmacies: every contractor that dispensed in the
    month, from BSO's dispensing-by-contractor file (no standalone list is published)."""
    raw = pl.read_csv(
        data_dir("raw") / "bso_dispensing" / "bso_dispensing.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    raw = raw.rename({c: c.strip() for c in raw.columns})
    code = next(c for c in raw.columns if c.lower() == "chemist")
    name = next(c for c in raw.columns if c.lower() == "contractor name")
    postcode = next(c for c in raw.columns if c.lower() == "contractor postcode")
    df = (
        raw.select(
            pl.col(code).str.strip_chars().alias("pharmacy_code"),
            pl.col(name).str.strip_chars().alias("name"),
            pl.col(postcode).str.strip_chars().alias("postcode"),
        )
        .filter(pl.col("pharmacy_code").is_not_null() & pl.col("postcode").is_not_null())
        .unique("pharmacy_code")
        .with_columns(pl.lit("bso").alias("source"))
    )
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd", nations=("N",)))
    logger.info(f"{df.height:,} community pharmacies in Northern Ireland (BSO)")
    return df


def stage_nhsbsa_pharmacies() -> pl.LazyFrame:
    """Community pharmacies in England with their weekly and Sunday opening hours."""
    raw = pl.read_csv(
        data_dir("raw") / "nhsbsa_pharmacies" / "nhsbsa_pharmacies.csv",
        infer_schema=False,
        encoding="utf8-lossy",
    )
    df = raw.select(
        pl.col("PHARMACY_ODS_CODE_F_CODE").alias("pharmacy_code"),
        pl.col("PHARMACY_TRADING_NAME").str.strip_chars().alias("name"),
        pl.col("POST_CODE").alias("postcode"),
        pl.col("WEEKLY_TOTAL").cast(pl.Float64, strict=False).alias("weekly_hours"),
        pl.col("SUN_TOTAL").cast(pl.Float64, strict=False).alias("sunday_hours"),
        pl.col("CONTRACT_TYPE").alias("contract_type"),
    ).filter(pl.col("contract_type").is_in(PHARMACY_CONTRACTS))
    df = geocode_postcodes(df).filter(in_scope("lsoa21cd"))
    logger.info(f"{df.height:,} community pharmacies")
    return df.lazy()


# CQC overall ratings on a 0–1 scale (as for schools: an "expected" rating is mid-high)
CQC_SCALE = {"Outstanding": 1.0, "Good": 0.75, "Requires improvement": 0.4, "Inadequate": 0.1}


def stage_cqc_locations() -> pl.LazyFrame:
    """Active CQC locations with their latest overall rating and location.

    GP practices carry their ODS code (the practice code in GP registrations), so a
    practice's rating can be weighted by where its patients live. Ratings published
    before a location changed provider can be "inherited"; they are kept.
    """
    import fastexcel

    from lix_pipeline.stage.geo import lonlat_to_bng

    reader = fastexcel.read_excel(data_dir("raw") / "cqc_locations" / "cqc_locations.ods")
    raw = reader.load_sheet_by_name(
        "HSCA_Active_Locations", header_row=0, dtypes="string"
    ).to_polars()
    df = raw.filter(pl.col("Dormant (Y/N)") != "Y").select(
        pl.col("Location ID").alias("location_id"),
        pl.col("Location Name").alias("name"),
        pl.col("Location ODS Code").alias("ods_code"),
        pl.col("Location Primary Inspection Category").alias("category"),
        (pl.col("Care home?") == "Y").alias("care_home"),
        pl.col("Care homes beds").cast(pl.Float64, strict=False).cast(pl.Int32).alias("beds"),
        pl.col("Location Latest Overall Rating").alias("rating"),
        pl.col("Location Latest Overall Rating")
        .replace_strict(CQC_SCALE, default=None, return_dtype=pl.Float64)
        .alias("rating_score"),
        pl.col("Publication Date").str.slice(0, 10).alias("rated_on"),
        pl.col("Location Postal Code").alias("postcode"),
        pl.col("Location Latitude").cast(pl.Float64, strict=False).alias("lat"),
        pl.col("Location Longitude").cast(pl.Float64, strict=False).alias("lon"),
    )
    x, y = lonlat_to_bng(df["lon"], df["lat"])
    df = df.with_columns(x=x, y=y)
    gps = df.filter(pl.col("category") == "GP Practices")
    logger.info(
        f"{df.height:,} active CQC locations; {gps.height:,} GP practices, "
        f"{gps['rating_score'].is_not_null().mean():.0%} with a rating; "
        f"{df['care_home'].sum():,} care homes"
    )
    return df.lazy()


def stage_ae_sites() -> pl.LazyFrame:
    """Emergency departments and other urgent-care sites from the curated list
    (config/data/ae_sites.csv): placed by their coordinates, or by postcode where the row
    has none, and located to a small area."""
    from lix_pipeline.geo.joins import points_to_lsoa
    from lix_pipeline.stage.geo import lonlat_to_bng

    raw = pl.read_csv(
        data_dir("raw") / "ae_sites" / "ae_sites.csv", infer_schema_length=0, encoding="utf8-lossy"
    )
    df = raw.select(
        pl.col("name"),
        pl.col("nation"),
        pl.col("type"),
        pl.col("postcode"),
        pl.col("lat").cast(pl.Float64, strict=False),
        pl.col("lon").cast(pl.Float64, strict=False),
        pl.col("source"),
    )
    has_xy = df["lat"].is_not_null() & df["lon"].is_not_null()
    x, y = lonlat_to_bng(df["lon"].fill_null(0), df["lat"].fill_null(0))
    placed = df.filter(has_xy).with_columns(
        x=pl.Series(x.to_numpy()[has_xy.to_numpy()]), y=pl.Series(y.to_numpy()[has_xy.to_numpy()])
    )
    by_postcode = geocode_postcodes(df.filter(~has_xy)).drop("lsoa21cd")
    df = pl.concat([placed, by_postcode], how="diagonal_relaxed")
    df = points_to_lsoa(df).filter(in_scope("lsoa21cd"))
    logger.info(
        f"{df.height:,} emergency sites ({df.filter(pl.col('type') == 'type1').height:,} type 1)"
    )
    return df.lazy()
