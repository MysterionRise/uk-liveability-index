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
    from lix_pipeline.stage import geo_ew, geo_ni, geo_scotland

    return {"geo_ew": geo_ew.build, "geo_scotland": geo_scotland.build, "geo_ni": geo_ni.build}


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
    """Output areas → small area → mid area for the active nations (for OA-level sources
    such as Ofcom): the ONS lookup for England and Wales, NRS's for Scotland."""
    cfg = load_nations().nations
    frames = []
    for slug in dict.fromkeys(cfg[n].geo.oa_lookup for n in active_nations()):
        if slug == "oa_lookup":
            frames.append(
                pl.scan_parquet(raw_file("oa_lookup", "parquet")).select(
                    pl.col("OA21CD").alias("oa21cd"),
                    pl.col("LSOA21CD").alias("lsoa21cd"),
                    pl.col("MSOA21CD").alias("msoa21cd"),
                )
            )
        elif slug == "nrs_oa22_lookup":
            frames.append(
                pl.scan_csv(data_dir("raw") / "nrs_oa22_lookup" / "OA22_DZ22_IZ22.csv").select(
                    pl.col("OA22").alias("oa21cd"),
                    pl.col("DZ22").alias("lsoa21cd"),
                    pl.col("IZ22").alias("msoa21cd"),
                )
            )
        elif slug == "nisra_dz21_lookup":
            from lix_pipeline.stage.geo_ni import read_dz_lookup

            # Data Zones are the output-area tier; the Super Data Zone is both the low
            # and the mid tier
            frames.append(
                read_dz_lookup().with_columns(pl.col("lsoa21cd").alias("msoa21cd")).lazy()
            )
        else:
            raise ValueError(f"No reader for the output-area lookup {slug!r}")
    return pl.concat(frames).filter(in_scope("lsoa21cd")).sort("oa21cd")


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


def stage_soa01_sdz21() -> pl.LazyFrame:
    """Super Output Area 2001 → Super Data Zone 2021 weights for Northern Ireland (the
    deprivation measure and the capital value bands are still on the 2001 areas).

    No open postcode lookup bridges the two (the Central Postcode Directory is
    licensed), so the weights come from the Data Zones: each zone's population-weighted
    centroid falls in one 2001 area and carries its Census 2021 residents, and a Super
    Data Zone's weights are its residents' shares by 2001 area.
    """
    from lix_pipeline.geo.boundaries import load_boundaries
    from lix_pipeline.geo.crosswalk import build_from_points, crosswalk_problems, summary
    from lix_pipeline.stage.geo_ni import load_sheet, read_centroids, read_dz_lookup

    spec = load_nations().nations["N"]
    soa = load_boundaries("osni_soa01_boundaries", code_column="SOA_CODE")
    folder = data_dir("raw") / "nisra_census_2021_p1"
    a01 = next(iter(sorted(folder.glob("**/census-2021-ms-a01.xlsx"))), None)
    if a01 is None:
        raise FileNotFoundError(f"No census-2021-ms-a01.xlsx under {folder}")
    people = load_sheet(a01, "DZ", "Geography")
    code = next(c for c in people.columns if c.strip().lower() == "geography code")
    count = next(c for c in people.columns if c.strip().lower().startswith("all usual residents"))
    people = people.select(
        pl.col(code).cast(pl.Utf8).str.strip_chars().alias("oa21cd"),
        pl.col(count).cast(pl.Float64, strict=False).alias("population"),
    ).filter(pl.col("oa21cd").str.starts_with("N20"))
    points = (
        read_centroids(spec.input_crs, level="data-zone")
        .rename({"lsoa21cd": "oa21cd", "pwc_x": "x", "pwc_y": "y"})
        .join(read_dz_lookup().rename({"lsoa21cd": "sdz21cd"}), on="oa21cd", how="inner")
        .join(people, on="oa21cd", how="left")
        .select("sdz21cd", "x", "y", "population")
    )
    weights = build_from_points(points, soa, new_key="sdz21cd", old_key="soa01cd")
    zones = set(read_dz_lookup()["lsoa21cd"].to_list())
    problems = crosswalk_problems(weights, zones, new_key="sdz21cd")
    if problems:
        raise ValueError("Super Output Area crosswalk: " + "; ".join(problems))
    logger.info(
        f"Super Output Area 2001 → Super Data Zone 2021 crosswalk: "
        f"{summary(weights, key='soa01cd', new_key='sdz21cd')}"
    )
    return weights.lazy()


def _name_in(lang: str) -> pl.Expr:
    """The OS Open Names form in ``lang`` (``cym``, ``gla``), if the place has one."""
    return (
        pl.when(pl.col("NAME1_LANG") == lang)
        .then(pl.col("NAME1"))
        .when(pl.col("NAME2_LANG") == lang)
        .then(pl.col("NAME2"))
        .otherwise(None)
    )


# OS Open Names LOCAL_TYPE values that are places people search for
PLACE_TYPES = ["City", "Town", "Village", "Hamlet", "Suburban Area", "Other Settlement"]


def stage_places() -> pl.LazyFrame:
    """Settlements of the active nations, located to an LSOA, for search: OS Open Names
    where it reaches (``places_builder: os_open_names``), the nation's OpenStreetMap
    extract elsewhere (``osm``)."""
    cfg = load_nations().nations
    from_os = [n for n in active_nations() if cfg[n].places_builder == "os_open_names"]
    from_osm = [n for n in active_nations() if cfg[n].places_builder == "osm"]
    frames = []
    if from_os:
        frames.append(_os_open_names_places([cfg[n].places_country for n in from_os]))
    if from_osm:
        from lix_pipeline.stage.places_osm import osm_places

        frames.extend(osm_places(n) for n in from_osm)
    from lix_pipeline.stage.place_names import with_curated_names

    places = with_curated_names(pl.concat(frames, how="diagonal_relaxed"))

    from lix_pipeline.geo.joins import points_to_lsoa

    places = points_to_lsoa(places, x="x", y="y")
    logger.info(f"Places: {places.height:,} settlements across {list(active_nations())}")
    return places.lazy()


def _os_open_names_places(countries: list[str]) -> pl.DataFrame:
    """OS Open Names' populated places of ``countries`` (its COUNTRY values)."""
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
            # Bilingual places list the Welsh or Gaelic form first; ``name`` is the English
            # one, and each language's form sits in its own column for the search and
            # the interface (#66)
            pl.when(pl.col("NAME2_LANG") == "eng")
            .then(pl.col("NAME2"))
            .otherwise(pl.col("NAME1"))
            .alias("name"),
            _name_in("cym").alias("name_cy"),
            _name_in("gla").alias("name_gd"),
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
    # Until the search reads every name column (#66), the other-language form is ``name_alt``
    places = places.with_columns(
        lon=lon,
        lat=lat,
        name_alt=pl.coalesce("name_cy", "name_gd"),
        name_ga=pl.lit(None, pl.Utf8),
    )
    logger.info(f"OS Open Names: {places.height:,} settlements in {countries}")
    return places
