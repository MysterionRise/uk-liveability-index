"""Public charging sites, one row each: OpenStreetMap everywhere, ESB's network on the
island of Ireland, and Open Charge Map's openly licensed locations where the build has a
key. A site within ``DEDUP_M`` of one already kept is the same site (OpenStreetMap leads,
then ESB, then Open Charge Map)."""

import json

import numpy as np
import polars as pl
from scipy.spatial import cKDTree

from lix_core.codes import active_nations, in_scope
from lix_core.geo import lonlat_to_working, working_to_lonlat
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.chargers")

DEDUP_M = 50.0
ESB_TERRITORIES = {"RoI": "I", "NI": "N"}
OCM_FILES = {"open_charge_map_gb": ("E", "W", "S", "N"), "open_charge_map_ie": ("I",)}
# Open Charge Map's own contributors (data provider 1) publish under CC BY 4.0
OCM_CONTRIBUTORS = 1

SCHEMA = {
    "charger_id": pl.Utf8,
    "name": pl.Utf8,
    "points": pl.Float64,
    "provider": pl.Utf8,
    "source": pl.Utf8,
    "licence": pl.Utf8,
    "x": pl.Float64,
    "y": pl.Float64,
}


def _osm() -> pl.DataFrame:
    osm = pl.read_parquet(data_dir("staged") / "osm_pois.parquet").filter(
        pl.col("value") == "charging_station"
    )
    return osm.select(
        pl.concat_str([pl.lit("osm:"), "osm_type", pl.col("osm_id").cast(pl.Utf8)]).alias(
            "charger_id"
        ),
        "name",
        pl.lit(None, pl.Float64).alias("points"),
        pl.lit(None, pl.Utf8).alias("provider"),
        pl.lit("OpenStreetMap").alias("source"),
        pl.lit("ODbL-1.0").alias("licence"),
        "x",
        "y",
    )


def tidy_esb(raw: pl.DataFrame, nations: tuple[str, ...]) -> pl.DataFrame:
    """ESB's sites CSV in the chargers shape, for the island's active nations."""
    raw = raw.rename({c: c.replace("﻿", "").strip() for c in raw.columns})
    df = raw.select(
        pl.col("Territory")
        .str.strip_chars()
        .replace_strict(ESB_TERRITORIES, default=None)
        .alias("nation"),
        pl.col("Address").alias("name"),
        pl.col("Nr. Chargers").cast(pl.Float64, strict=False).alias("points"),
        pl.col("Latitude").cast(pl.Float64, strict=False).alias("lat"),
        pl.col("Longitude").cast(pl.Float64, strict=False).alias("lon"),
    ).filter(
        pl.col("nation").is_in(list(nations))
        & pl.col("lat").is_not_null()
        & pl.col("lon").is_not_null()
    )
    x, y = lonlat_to_working(df["lon"].to_numpy(), df["lat"].to_numpy())
    return df.with_row_index("i").select(
        pl.concat_str([pl.lit("esb:"), pl.col("i").cast(pl.Utf8)]).alias("charger_id"),
        "name",
        "points",
        pl.lit(None, pl.Utf8).alias("provider"),
        pl.lit("ESB ecars").alias("source"),
        pl.lit("CC-BY-4.0").alias("licence"),
        pl.Series("x", x),
        pl.Series("y", y),
    )


def tidy_ocm(pois: list[dict]) -> pl.DataFrame:
    """Open Charge Map locations in the chargers shape: only those whose data provider
    publishes under an open licence (the map's own contributors, CC BY 4.0, or an import
    the provider marks open), each with the provider named."""
    rows = []
    for poi in pois:
        provider = poi.get("DataProvider") or {}
        own = (
            poi.get("DataProviderID") == OCM_CONTRIBUTORS or provider.get("ID") == OCM_CONTRIBUTORS
        )
        if not (own or provider.get("IsOpenDataLicensed")):
            continue
        address = poi.get("AddressInfo") or {}
        lat, lon = address.get("Latitude"), address.get("Longitude")
        if lat is None or lon is None:
            continue
        rows.append(
            {
                "charger_id": f"ocm:{poi.get('ID')}",
                "name": address.get("Title"),
                "points": float(poi["NumberOfPoints"])
                if poi.get("NumberOfPoints") is not None
                else None,
                "provider": provider.get("Title"),
                "source": "Open Charge Map",
                "licence": "CC-BY-4.0" if own else (provider.get("License") or "open"),
                "lat": float(lat),
                "lon": float(lon),
            }
        )
    df = pl.DataFrame(rows, schema={**{k: v for k, v in SCHEMA.items() if k not in ("x", "y")},
                                    "lat": pl.Float64, "lon": pl.Float64})  # fmt: skip
    x, y = lonlat_to_working(df["lon"].to_numpy(), df["lat"].to_numpy())
    return df.drop("lat", "lon").with_columns(pl.Series("x", x), pl.Series("y", y))


def dedup(kept: pl.DataFrame, new: pl.DataFrame, within_m: float = DEDUP_M) -> pl.DataFrame:
    """``new`` without the rows within ``within_m`` of a row of ``kept``."""
    if kept.height == 0 or new.height == 0:
        return new
    tree = cKDTree(np.column_stack([kept["x"].to_numpy(), kept["y"].to_numpy()]))
    distance, _ = tree.query(
        np.column_stack([new["x"].to_numpy(), new["y"].to_numpy()]),
        k=1,
        distance_upper_bound=within_m,
    )
    return new.filter(pl.Series(np.isinf(distance)))


def stage_chargers() -> pl.LazyFrame:
    """Charging sites across the active nations, located to a small area."""
    from lix_pipeline.geo.joins import points_to_lsoa

    nations = active_nations()
    frames = [_osm()]
    logger.info(f"{frames[0].height:,} charging stations in OpenStreetMap")

    island = tuple(n for n in nations if n in ESB_TERRITORIES.values())
    esb_path = data_dir("raw") / "esb_ev_sites" / "esb_ev_sites.csv"
    if island and esb_path.exists():
        esb = tidy_esb(pl.read_csv(esb_path, infer_schema_length=0, encoding="utf8-lossy"), island)
        kept = dedup(frames[0], esb)
        logger.info(f"ESB: {esb.height:,} sites, {kept.height:,} not already in OpenStreetMap")
        frames.append(kept)

    for slug, covers in OCM_FILES.items():
        if not any(n in nations for n in covers):
            continue
        path = data_dir("raw") / slug / f"{slug}.json"
        if not path.exists():
            logger.warning(
                f"{slug}: no download (is OCM_API_KEY set?); chargers from OpenStreetMap"
                + (" and ESB" if island else "")
                + " only"
            )
            continue
        ocm = tidy_ocm(json.loads(path.read_text()))
        kept = dedup(pl.concat(frames), ocm)
        logger.info(f"{slug}: {ocm.height:,} open locations, {kept.height:,} new")
        frames.append(kept)

    df = points_to_lsoa(pl.concat(frames)).filter(in_scope("lsoa21cd"))
    lon, lat = working_to_lonlat(df["x"].to_numpy(), df["y"].to_numpy())
    df = df.with_columns(pl.Series("lon", lon), pl.Series("lat", lat))
    logger.info(
        f"{df.height:,} charging sites in {list(nations)}: "
        f"{dict(df['source'].value_counts().rows())}"
    )
    return df.lazy()
