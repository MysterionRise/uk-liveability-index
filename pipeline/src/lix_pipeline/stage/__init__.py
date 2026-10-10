"""Stagers turn raw downloads into tidy, typed Parquet in data/staged/{slug}.parquet."""

from collections.abc import Callable
from pathlib import Path

import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage")

# Raw datasets each stager reads, when that isn't just its own slug
STAGE_INPUTS: dict[str, list[str]] = {
    "price_paid": ["price_paid", "nspl"],
    "population": ["pop_lsoa_mye"],
    "geo_lsoa": [
        "oa_lookup",
        "msoa_names",
        "nspl",
        "lad_boundaries",
        "ruc_2021",
        "lsoa_centroids",
        "pop_lsoa_mye",
        "lsoa_boundaries",
    ],
    "places": ["os_open_names", "lsoa_boundaries"],
    "defra_pcm_no2": ["defra_pcm_no2", "nspl"],
    "defra_pcm_pm25": ["defra_pcm_pm25", "nspl"],
    "defra_pcm_pm10": ["defra_pcm_pm10", "nspl"],
    "ods_gp": ["ods_gp", "nspl"],
    "gias": ["gias", "lsoa_boundaries"],
    "fsa_fhrs": ["fsa_fhrs", "nspl", "lsoa_boundaries"],
    "overture_pubs": ["overture_pubs", "lsoa_boundaries"],
    "osm_pois": ["osm_england"],
    "ods_dentists": ["ods_dentists", "nspl"],
    "nhsbsa_pharmacies": ["nhsbsa_pharmacies", "nspl"],
    "ofsted_childcare": ["ofsted_childcare", "nspl"],
    "ea_flood_postcodes": ["ea_flood_postcodes", "nspl"],
    "ofcom_broadband": ["ofcom_broadband", "oa_lookup"],
    "wg_schools": ["wg_schools", "lsoa_boundaries"],
    "schools": ["gias", "lsoa_boundaries"],
    "childcare": ["ofsted_childcare", "nspl"],
    "pharmacies": ["nhsbsa_pharmacies", "nspl"],
    "ea_flood_all_postcodes": ["ea_flood_postcode_tool", "nspl"],
    "flood": ["ea_flood_postcodes", "ea_flood_postcode_tool", "nspl"],
    "tree_cover": ["esa_worldcover", "nspl"],
    "night_lights": ["viirs_vnl", "nspl"],
    "noise": [
        "defra_noise_road_lden",
        "defra_noise_rail_lden",
        "wg_noise_road_lden",
        "wg_noise_rail_lden",
        "nspl",
    ],
    "deprivation": ["iod_2025"],
    "wg_ks4_la": ["wg_ks4_la"],
}


# Sources every build rests on whatever the indicators: the geography, postcodes, place
# names and the boundaries the tiles are cut from. The manifest credits them alongside
# the indicators' sources.
BACKBONE_INPUTS: list[str] = sorted(
    set(STAGE_INPUTS["geo_lsoa"]) | set(STAGE_INPUTS["places"]) | {"msoa_boundaries"}
)


def save_staged(df: pl.LazyFrame | pl.DataFrame, slug: str) -> Path:
    """Write a DataFrame to data/staged/{slug}.parquet with Snappy compression."""
    if isinstance(df, pl.LazyFrame):
        df = df.collect()

    out_path = data_dir("staged") / f"{slug}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    df.write_parquet(out_path, compression="snappy")
    logger.info(f"Saved {len(df):,} rows to {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")

    return out_path


def stagers() -> dict[str, Callable[[], pl.LazyFrame]]:
    """Slug → stager, in run order (later stagers read earlier staged outputs)."""
    from functools import partial

    from lix_core.config import load_registry
    from lix_pipeline.stage import (
        childcare,
        community,
        environment,
        geo,
        health,
        housing,
        landcover,
        night_lights,
        noise,
        safety,
        schools,
        transport,
    )
    from lix_pipeline.stage.census import stage_census_table
    from lix_pipeline.stage.deprivation import stage_deprivation
    from lix_pipeline.stage.fsa import stage_active_places, stage_fsa, stage_overture_pubs
    from lix_pipeline.stage.iod import stage_iod
    from lix_pipeline.stage.nspl import stage_nspl
    from lix_pipeline.stage.osm import stage_osm
    from lix_pipeline.stage.pcm import stage_pcm
    from lix_pipeline.stage.police import stage_police
    from lix_pipeline.stage.population import stage_population
    from lix_pipeline.stage.price_paid import stage_price_paid

    registry = load_registry()
    census = {s: partial(stage_census_table, s) for s in registry if s.startswith("census_")}
    pcm = {s: partial(stage_pcm, s) for s in registry if s.startswith("defra_pcm_")}

    return {
        "nspl": stage_nspl,
        "iod_2025": stage_iod,
        "population": stage_population,
        "oa_lookup": geo.stage_oa_lookup,
        "lsoa11_lsoa21": geo.stage_lsoa11_lsoa21,
        "geo_lsoa": geo.stage_geo_lsoa,  # needs staged nspl and population
        "places": geo.stage_places,
        "price_paid": stage_price_paid,
        **census,
        "police_crime": stage_police,
        **pcm,  # needs staged nspl
        "ods_gp": health.stage_ods_gp,
        "gp_registrations": health.stage_gp_registrations,
        "gp_workforce": health.stage_gp_workforce,
        "gias": schools.stage_gias,
        "wg_schools": schools.stage_wg_schools,
        "schools": schools.stage_schools,  # needs staged gias and wg_schools
        "wg_ks4_la": schools.stage_wg_ks4_la,  # needs staged geo_lsoa
        "ofsted_schools": schools.stage_ofsted_schools,
        "ks2_results": schools.stage_ks2_results,
        "ks4_results": schools.stage_ks4_results,
        "dft_connectivity": transport.stage_dft_connectivity,
        "fsa_fhrs": stage_fsa,
        "overture_pubs": stage_overture_pubs,
        "active_places": stage_active_places,
        "osm_pois": stage_osm,
        "stats19": safety.stage_stats19,
        "ods_dentists": health.stage_ods_dentists,
        "nhsbsa_pharmacies": health.stage_nhsbsa_pharmacies,
        "cqc_locations": health.stage_cqc_locations,
        "ofsted_childcare": childcare.stage_ofsted_childcare,
        "childcare": childcare.stage_childcare,  # needs staged ofsted_childcare and osm_pois
        "pharmacies": health.stage_pharmacies,  # needs staged nhsbsa_pharmacies and osm_pois
        "os_greenspace": environment.stage_os_greenspace,
        "ea_flood_postcodes": environment.stage_ea_flood_postcodes,
        "ea_flood_all_postcodes": environment.stage_ea_flood_all_postcodes,
        "naptan": transport.stage_naptan,
        "bods_gtfs": transport.stage_bods_gtfs,
        "ofcom_broadband": transport.stage_ofcom_broadband,  # needs staged oa_lookup
        "msoa_income": housing.stage_msoa_income,
        "council_tax": housing.stage_council_tax,
        "voa_ctsop": housing.stage_voa_ctsop,
        "claimant_count": community.stage_claimant_count,
        "life_expectancy": community.stage_life_expectancy,
        # Concept tables that union per-nation sources staged above
        "flood": environment.stage_flood,  # needs staged ea_flood_postcodes and voa_ctsop
        "noise": noise.stage_noise,  # needs staged nspl
        "tree_cover": landcover.stage_tree_cover,  # needs staged nspl
        "night_lights": night_lights.stage_night_lights,  # manual source; needs staged nspl
        "deprivation": stage_deprivation,  # needs staged iod_2025
    }
