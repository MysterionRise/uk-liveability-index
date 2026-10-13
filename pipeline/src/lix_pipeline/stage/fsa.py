"""Food Standards Agency hygiene ratings → food businesses with location.

Ratings run 0–5 in England, Wales and Northern Ireland (FHRS); Scotland's scheme (FHIS)
says Pass or Improvement Required, so a Scottish row has a null ``rating`` and the
outcome in ``rating_status``, with ``scheme`` telling the two apart. Non-numeric states
such as "AwaitingInspection" or "Exempt" keep a null ``rating`` too. The component
scores (hygiene, structural, confidence in management) are penalty points: lower is
better.
"""

import polars as pl

from lix_core.codes import in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.joins import points_to_lsoa
from lix_pipeline.stage.geo import lonlat_to_bng
from lix_pipeline.stage.health import geocode_postcodes

logger = setup_logging("stage.fsa")

PUB_BAR_NIGHTCLUB = 7843


def stage_fsa() -> pl.LazyFrame:
    raw = pl.read_csv(data_dir("raw") / "fsa_fhrs" / "fsa_fhrs.csv", infer_schema_length=0)
    df = raw.filter(pl.col("SchemeType").is_in(["FHRS", "FHIS"])).select(
        pl.col("FHRSID").cast(pl.Int64).alias("fhrs_id"),
        pl.col("SchemeType").alias("scheme"),
        pl.col("BusinessName").alias("name"),
        pl.col("BusinessTypeID").cast(pl.Int32).alias("business_type_id"),
        pl.col("BusinessType").alias("business_type"),
        pl.col("RatingValue").cast(pl.Int8, strict=False).alias("rating"),
        pl.col("RatingValue").alias("rating_status"),
        pl.col("RatingDate").str.to_date(strict=False).alias("rating_date"),
        pl.col("Hygiene").cast(pl.Int16, strict=False).alias("score_hygiene"),
        pl.col("Structural").cast(pl.Int16, strict=False).alias("score_structural"),
        pl.col("ConfidenceInManagement").cast(pl.Int16, strict=False).alias("score_management"),
        pl.col("PostCode").alias("postcode"),
        pl.col("LocalAuthorityName").alias("local_authority"),
        pl.col("Longitude").cast(pl.Float64, strict=False).alias("lon"),
        pl.col("Latitude").cast(pl.Float64, strict=False).alias("lat"),
    )

    # Prefer the FSA's own coordinates; fall back to the postcode centroid
    x, y = lonlat_to_bng(df["lon"].fill_null(0), df["lat"].fill_null(0))
    has_xy = df["lon"].is_not_null() & df["lat"].is_not_null()
    df = df.with_columns(
        x=pl.when(has_xy).then(x), y=pl.when(has_xy).then(y), location_source=pl.lit("fsa")
    )
    fallback = geocode_postcodes(df.filter(~has_xy).drop("x", "y")).with_columns(
        pl.lit("postcode").alias("location_source")
    )
    df = pl.concat([df.filter(has_xy), fallback.drop("lsoa21cd")], how="diagonal_relaxed")
    df = points_to_lsoa(df).filter(in_scope("lsoa21cd"))

    pubs = df.filter(pl.col("business_type_id") == PUB_BAR_NIGHTCLUB).height
    logger.info(f"{df.height:,} food businesses ({pubs:,} pubs, bars and nightclubs)")
    return df.lazy()


# Overture's taxonomy for what the UK calls a pub
OVERTURE_PUB_CATEGORIES = ("pub", "irish_pub", "sports_bar", "beer_bar", "gastropub")


def stage_overture_pubs() -> pl.LazyFrame:
    """Overture Maps pubs in England with BNG coordinates and their existence confidence."""
    from lix_pipeline.geo.joins import points_to_lsoa
    from lix_pipeline.stage.geo import lonlat_to_bng

    raw = pl.read_parquet(data_dir("raw") / "overture_pubs" / "overture_pubs.parquet")
    df = raw.filter(
        pl.col("category").is_in(OVERTURE_PUB_CATEGORIES)
        & (pl.col("operating_status").is_null() | (pl.col("operating_status") == "open"))
    )
    x, y = lonlat_to_bng(df["lon"], df["lat"])
    df = points_to_lsoa(df.with_columns(x=x, y=y), x="x", y="y").filter(
        pl.col("lsoa21cd").is_not_null()
    )
    logger.info(
        f"Overture: {df.height:,} pubs in England; "
        f"median confidence {df['confidence'].median():.2f}"
    )
    return df.select(
        "id", "name", "category", "confidence", "lon", "lat", "x", "y", "lsoa21cd"
    ).lazy()


def stage_active_places() -> pl.LazyFrame:
    """Operational sports facilities the public can use (pay and play, membership, clubs)."""
    raw = pl.read_parquet(data_dir("raw") / "active_places" / "active_places.parquet")
    df = raw.filter(
        (pl.col("facstatus") == "Operational")
        & (pl.col("accessibilitygroupstr") == "Public Access")
    ).select(
        "facilityid",
        "siteid",
        pl.col("facilitytype").alias("type"),
        pl.col("facilitysubtype").alias("subtype"),
        pl.col("accessibilitytypestr").alias("access"),
        pl.col("easting").cast(pl.Float64).alias("x"),
        pl.col("northing").cast(pl.Float64).alias("y"),
    )
    logger.info(
        f"Active Places: {df.height:,} operational public facilities "
        f"at {df['siteid'].n_unique():,} sites"
    )
    return df.lazy()
