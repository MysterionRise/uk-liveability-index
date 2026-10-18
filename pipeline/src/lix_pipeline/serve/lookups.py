"""Lookup tables the API serves: postcodes, places, named areas and points of interest.

    data/serve/postcodes.parquet  live postcodes of the active nations → LSOA, lat/lon
    data/serve/places.parquet     settlements (OS Open Names) for place search
    data/serve/areas.parquet      MSOAs, local authorities, regions and nations with
                                  bounding boxes
    data/serve/pois.parquet       points of interest by category, with source and licence

POIs derived from OpenStreetMap are a derivative database under the ODbL; every row
carries its source and licence so API responses and exports can attribute it.
"""

import json

import polars as pl

from lix_core.codes import in_scope
from lix_core.config import load_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.stage.geo import bng_to_lonlat

logger = setup_logging("serve.lookups")

ODBL = "ODbL-1.0"
OGL = "OGL-3.0"

# OSM tag value → POI category exposed by the API
OSM_CATEGORIES = {
    "supermarket": "supermarket", "convenience": "convenience_store", "pharmacy": "pharmacy",
    "dentist": "dentist", "cafe": "cafe", "restaurant": "restaurant", "bar": "bar",
    "fast_food": "fast_food", "park": "park", "nature_reserve": "park", "playground": "playground",
    "fitness_centre": "gym", "sports_centre": "sports_centre",
    "library": "library", "cinema": "cinema", "theatre": "theatre", "arts_centre": "arts_centre",
    "museum": "museum", "gallery": "museum", "post_office": "post_office", "bakery": "bakery",
    "butcher": "butcher", "greengrocer": "greengrocer", "kindergarten": "nursery",
    "childcare": "nursery", "community_centre": "community_centre", "marketplace": "market",
    "charging_station": "ev_charging",
}  # fmt: skip


def _detail(cols: list[str]) -> pl.Expr:
    """Pack extra columns into a JSON string (nulls dropped)."""
    return pl.struct(cols).map_elements(
        lambda s: json.dumps({k: v for k, v in s.items() if v is not None}, default=str),
        return_dtype=pl.Utf8,
    )


def _with_lonlat(df: pl.DataFrame) -> pl.DataFrame:
    lon, lat = bng_to_lonlat(df["x"], df["y"])
    return df.with_columns(lon=lon, lat=lat)


# GP practices come from NHS England's ODS (England and Wales) or Public Health Scotland
GP_SOURCES = {"E": "NHS England ODS", "W": "NHS England ODS", "S": "Public Health Scotland"}

SCHOOL_SOURCES = {
    "gias": "DfE Get Information About Schools + Ofsted",
    "wg": "Welsh Government schools list",
    "sg": "Scottish Government school roll and locations",
}


def build_pois() -> pl.DataFrame:
    staged, ind = data_dir("staged"), data_dir("indicators")
    frames = []

    # No pubs table in a build without a hygiene scheme (Ireland alone)
    pubs = (
        pl.read_parquet(ind / "pubs.parquet")
        if (ind / "pubs.parquet").exists()
        else pl.DataFrame(schema={"pub_id": pl.Utf8})
    )
    if pubs.height:
        frames.append(
            pubs.select(
                pl.lit("pub").alias("category"),
                pl.col("pub_id").alias("poi_id"),
                "name",
                "x",
                "y",
                "lon",
                "lat",
                pl.col("source_name").alias("source"),
                "licence",
                _detail(
                    [
                        "well_run",
                        "rating",
                        "rating_date",
                        "fsa_id",
                        "real_ale",
                        "food",
                        "outdoor_seating",
                        "beer_garden",
                    ]
                ).alias("detail"),
            )  # fmt: skip
        )

    gp = _with_lonlat(pl.read_parquet(staged / "ods_gp.parquet").filter(pl.col("x").is_not_null()))
    frames.append(
        gp.select(
            pl.lit("gp").alias("category"),
            pl.concat_str(pl.lit("ods:"), "practice_code").alias("poi_id"),
            "name",
            pl.col("x").cast(pl.Float64),
            pl.col("y").cast(pl.Float64),
            "lon",
            "lat",
            pl.when(pl.col("lsoa21cd").str.starts_with("S"))
            .then(pl.lit(GP_SOURCES["S"]))
            .otherwise(pl.lit(GP_SOURCES["E"]))
            .alias("source"),
            pl.lit(OGL).alias("licence"),
            _detail(["practice_code", "postcode"]).alias("detail"),
        )  # fmt: skip
    )

    schools = pl.read_parquet(staged / "schools.parquet")
    if (staged / "ofsted_schools.parquet").exists():
        schools = schools.join(
            pl.read_parquet(staged / "ofsted_schools.parquet").select(
                "urn", "quality", "framework", "inspection_date"
            ),
            on="urn",
            how="left",
        )
    else:
        # A build without England has no Ofsted table: every school is ungraded
        schools = schools.with_columns(
            pl.lit(None, pl.Float64).alias("quality"),
            pl.lit(None, pl.Utf8).alias("framework"),
            pl.lit(None, pl.Date).alias("inspection_date"),
        )
    schools = _with_lonlat(schools)
    category = (
        pl.when(pl.col("phase").is_in(["Primary", "Middle deemed primary"]))
        .then(pl.lit("primary_school"))
        .when(pl.col("phase").is_in(["Secondary", "Middle deemed secondary", "All-through"]))
        .then(pl.lit("secondary_school"))
        .when(pl.col("phase") == "Nursery")
        .then(pl.lit("nursery"))
        .otherwise(pl.lit("other_school"))
    )
    frames.append(
        schools.select(
            category.alias("category"),
            pl.concat_str(pl.lit("urn:"), pl.col("urn").cast(pl.Utf8)).alias("poi_id"),
            "name",
            "x",
            "y",
            "lon",
            "lat",
            pl.col("source")
            .replace_strict(SCHOOL_SOURCES, default="DfE Get Information About Schools + Ofsted")
            .alias("source"),
            pl.lit(OGL).alias("licence"),
            _detail(
                [
                    "urn",
                    "phase",
                    "type",
                    "type_group",
                    "low_age",
                    "high_age",
                    "pupils",
                    "capacity",
                    "quality",
                    "framework",
                    "inspection_date",
                ]
            ).alias("detail"),
        )  # fmt: skip
    )

    osm = pl.read_parquet(staged / "osm_pois.parquet").filter(
        pl.col("value").is_in(list(OSM_CATEGORIES))
    )
    frames.append(
        osm.select(
            pl.col("value").replace_strict(OSM_CATEGORIES).alias("category"),
            pl.concat_str("osm_type", pl.col("osm_id").cast(pl.Utf8)).alias("poi_id"),
            "name",
            "x",
            "y",
            "lon",
            "lat",
            pl.lit("OpenStreetMap").alias("source"),
            pl.lit(ODBL).alias("licence"),
            _detail(
                ["key", "value", "brand", "opening_hours", "website", "cuisine", "wheelchair"]
            ).alias("detail"),
        )  # fmt: skip
    )
    return pl.concat(frames, how="vertical_relaxed").filter(pl.col("x").is_not_null())


def build_areas(features: pl.DataFrame) -> pl.DataFrame:
    """Named areas above the LSOA with bounding boxes, for search and map fitting.

    Levels: msoa, lad, region, nation. A nation without real regions (Wales) has no
    region rows: its nation row stands in.
    """
    names = {code: spec.name for code, spec in load_nations().nations.items()}
    features = features.with_columns(
        pl.col("nation").replace_strict(names, default=None).alias("nation_name")
    )

    def other(column: str) -> pl.Expr:
        return pl.col(column) if column in features.columns else pl.lit(None, pl.Utf8)

    none = pl.lit(None, pl.Utf8)
    # level, code, English name, Welsh, Gaelic and Irish names, which rows
    levels = [
        # A nation whose small areas are their own mid tier (Northern Ireland's Super
        # Data Zones) gets no second search row for the same code
        (
            "msoa",
            "msoa21cd",
            pl.col("msoa_name"),
            other("msoa_name_cy"),
            none,
            other("msoa_name_ga"),
            pl.col("msoa21cd") != pl.col("lsoa21cd"),
        ),
        (
            "lad",
            "lad_cd",
            pl.col("lad_nm"),
            other("lad_nm_cy"),
            other("lad_nm_gd"),
            other("lad_nm_ga"),
            pl.lit(True),
        ),
        (
            "region",
            "rgn_cd",
            pl.col("rgn_nm"),
            none,
            none,
            none,
            pl.col("rgn_cd") != pl.col("ctry_cd"),
        ),
        ("nation", "ctry_cd", pl.col("nation_name"), none, none, none, pl.lit(True)),
    ]
    frames = []
    for level, code, name, name_cy, name_gd, name_ga, keep in levels:
        frames.append(
            features.filter(keep)
            .group_by(code)
            .agg(
                name.first().alias("name"),
                name_cy.first().alias("name_cy"),
                name_gd.first().alias("name_gd"),
                name_ga.first().alias("name_ga"),
                pl.col("lad_nm").first().alias("lad_nm"),
                pl.col("nation").first().alias("nation"),
                pl.col("population").sum(),
                pl.len().alias("lsoas"),
                pl.col("bbox_w").min(),
                pl.col("bbox_s").min(),
                pl.col("bbox_e").max(),
                pl.col("bbox_n").max(),
            )  # fmt: skip
            .rename({code: "code"})
            .with_columns(pl.lit(level).alias("level"))
        )
    return pl.concat(frames, how="vertical_relaxed")


def build_lookups(features: pl.DataFrame) -> dict:
    out, staged = data_dir("serve"), data_dir("staged")
    files = {}
    postcodes = (
        pl.scan_parquet(staged / "nspl.parquet")
        .filter(pl.col("live") & in_scope("lsoa21cd"))
        .select("postcode", "postcode_norm", "lsoa21cd", pl.col("lat"), pl.col("long").alias("lon"))
        .collect()
    )
    places = pl.read_parquet(staged / "places.parquet").filter(pl.col("lsoa21cd").is_not_null())
    if (staged / "logainm.parquet").exists():
        from lix_pipeline.stage.logainm import with_irish_names

        places = with_irish_names(places, pl.read_parquet(staged / "logainm.parquet"))
    tables = {
        "postcodes.parquet": postcodes,
        "places.parquet": places.drop("x", "y"),
        "areas.parquet": build_areas(features),
        "pois.parquet": build_pois(),
    }
    for name, df in tables.items():
        path = out / name
        df.write_parquet(path, compression="zstd")
        files[name] = path
        logger.info(f"{name}: {df.height:,} rows, {path.stat().st_size / 1e6:.1f} MB")
    return files
