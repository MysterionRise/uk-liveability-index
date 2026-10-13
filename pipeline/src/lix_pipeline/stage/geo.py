"""Geography backbone: one row per area for every active nation, plus lookups and places.

Every indicator joins onto ``geo_lsoa``; it carries the hierarchy (MSOA, local
authority, region), friendly names, the population-weighted centroid, the urban/rural
class, population and a map bounding box. Each nation names a ``geo_builder`` in
``config/nations.yaml``; nations that share source files (England and Wales) share a
builder and are built in one pass.
"""

from collections.abc import Callable

import polars as pl

from lix_core.codes import active_nations, in_scope
from lix_core.config import load_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file
from lix_pipeline.geo.crs import bng_to_lonlat, lonlat_to_bng  # noqa: F401  (re-exported)

logger = setup_logging("stage.geo")

Builder = Callable[[tuple[str, ...]], pl.DataFrame]


def _builders() -> dict[str, Builder]:
    from lix_pipeline.stage import geo_ew

    return {"geo_ew": geo_ew.build}


def stage_geo_lsoa() -> pl.LazyFrame:
    """The per-area backbone for the active nations, one builder call per builder."""
    cfg = load_nations().nations
    by_builder: dict[str, list[str]] = {}
    for nation in active_nations():
        by_builder.setdefault(cfg[nation].geo_builder, []).append(nation)
    builders = _builders()
    frames = []
    for name, nations in by_builder.items():
        if name not in builders:
            raise ValueError(f"No geo builder {name!r} for nations {nations}")
        frames.append(builders[name](tuple(nations)))
    df = pl.concat(frames, how="diagonal_relaxed").sort("lsoa21cd")
    logger.info(f"Geography backbone: {df.height:,} areas across {sum(by_builder.values(), [])}")
    return df.lazy()


def stage_oa_lookup() -> pl.LazyFrame:
    """Output areas → LSOA → MSOA for the active nations (for OA-level sources such as Ofcom)."""
    return (
        pl.scan_parquet(raw_file("oa_lookup", "parquet"))
        .filter(in_scope("LSOA21CD"))
        .select(
            pl.col("OA21CD").alias("oa21cd"),
            pl.col("LSOA21CD").alias("lsoa21cd"),
            pl.col("MSOA21CD").alias("msoa21cd"),
        )
    )


def stage_lsoa11_lsoa21() -> pl.LazyFrame:
    """LSOA 2011 → 2021 for sources still published on 2011 geography.

    ``change``: U unchanged, S 2011 LSOA split into several 2021 ones, M merged,
    X irregular. Converting splits needs a weight (e.g. population share).
    """
    return (
        pl.scan_parquet(raw_file("lsoa11_lsoa21", "parquet"))
        .filter(in_scope("LSOA21CD"))
        .select(
            pl.col("LSOA11CD").alias("lsoa11cd"),
            pl.col("LSOA21CD").alias("lsoa21cd"),
            pl.col("CHGIND").alias("change"),
        )
    )


def stage_dz11_dz22() -> pl.LazyFrame:
    """Data Zone 2011 → 2022 weights for Scotland (see lix_pipeline.geo.crosswalk).

    SIMD's postcode lookup gives each postcode's 2011 zone, the NSPL its 2022 zone and
    output area, and NRS's output-area populations weight the postcodes.
    """
    import fastexcel

    from lix_pipeline.geo.crosswalk import build_dz11_dz22, crosswalk_problems, summary
    from lix_pipeline.geo.nspl import load_nspl

    simd = (
        fastexcel.read_excel(raw_file("simd_postcode_lookup", "xlsx"))
        .load_sheet_by_name("All postcodes")
        .to_polars()
        .select(
            pl.col("Postcode").str.replace_all(r"\s", "").str.to_uppercase().alias("postcode_norm"),
            pl.col("DZ").alias("dz11cd"),
        )
        .filter(pl.col("dz11cd").str.starts_with("S01"))
    )
    oa_population = pl.read_csv(raw_file("nrs_oa22_population", "csv")).select(
        pl.col("OutputArea2022").alias("oa21cd"),
        pl.col("UsualResidentPopulation").cast(pl.Int64).alias("population"),
    )
    nspl = load_nspl(nations=("S",), live_only=False)
    weights = build_dz11_dz22(simd, nspl, oa_population)
    zones = set(nspl.filter(pl.col("live")).select("lsoa21cd").unique().collect()["lsoa21cd"])
    problems = crosswalk_problems(weights, zones)
    if problems:
        raise ValueError("Data Zone crosswalk: " + "; ".join(problems))
    logger.info(f"Data Zone 2011 → 2022 crosswalk: {summary(weights)}")
    return weights.lazy()


# OS Open Names LOCAL_TYPE values that are places people search for
PLACE_TYPES = ["City", "Town", "Village", "Hamlet", "Suburban Area", "Other Settlement"]


def stage_places() -> pl.LazyFrame:
    """Settlements of the active nations from OS Open Names, located to an LSOA, for search."""
    cfg = load_nations().nations
    countries = [cfg[n].places_country for n in active_nations()]
    root = data_dir("raw") / "os_open_names"
    header = (root / "Doc" / "OS_Open_Names_Header.csv").read_text(encoding="utf-8-sig")
    columns = header.strip().split(",")
    files = sorted((root / "Data").glob("*.csv"))
    places = (
        pl.scan_csv(
            files,
            has_header=False,
            new_columns=columns,
            schema_overrides={c: pl.Utf8 for c in columns},
            encoding="utf8-lossy",
        )
        .filter(
            (pl.col("TYPE") == "populatedPlace")
            & pl.col("LOCAL_TYPE").is_in(PLACE_TYPES)
            & pl.col("COUNTRY").is_in(countries)
        )
        .select(
            pl.col("ID").str.strip_chars_start("﻿").alias("place_id"),
            # Bilingual places list the Welsh or Gaelic form first; until the search
            # matches every name (#66), keep the English one as the name people type
            pl.when(pl.col("NAME2_LANG") == "eng")
            .then(pl.col("NAME2"))
            .otherwise(pl.col("NAME1"))
            .alias("name"),
            pl.when(pl.col("NAME2_LANG") == "eng")
            .then(pl.col("NAME1"))
            .otherwise(pl.col("NAME2"))
            .alias("name_alt"),
            pl.col("LOCAL_TYPE").alias("place_type"),
            pl.col("GEOMETRY_X").cast(pl.Float64).alias("x"),
            pl.col("GEOMETRY_Y").cast(pl.Float64).alias("y"),
            pl.col("POSTCODE_DISTRICT").alias("postcode_district"),
            # Welsh authorities come as "Sir Ddinbych - Denbighshire": keep the English form
            pl.coalesce("DISTRICT_BOROUGH", "COUNTY_UNITARY")
            .str.split(" - ")
            .list.last()
            .str.replace(r"^the ", "")
            .alias("local_authority"),
            pl.col("REGION").alias("region"),
            pl.col("COUNTRY").alias("country"),
        )
        .collect()
    )
    lon, lat = bng_to_lonlat(places["x"], places["y"])
    places = places.with_columns(lon=lon, lat=lat)

    from lix_pipeline.geo.joins import points_to_lsoa

    places = points_to_lsoa(places, x="x", y="y")
    logger.info(f"Places: {places.height:,} settlements in {countries}")
    return places.lazy()
