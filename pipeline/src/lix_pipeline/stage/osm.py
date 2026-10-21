"""OpenStreetMap extracts (one per nation) → one row per point of interest.

Amenities are mapped either as nodes or as building outlines (closed ways and
multipolygons); outlines are reduced to a representative point. Only the keys listed
in ``POI_KEYS`` are read, filtered in C++ by pyosmium, so a full England pass takes a
few minutes.

Licence: derived from OpenStreetMap, © OpenStreetMap contributors, ODbL. The staged
table is a derivative database and must stay under the ODbL.
"""

import osmium
import polars as pl
import shapely

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.stage.geo import lonlat_to_bng

logger = setup_logging("stage.osm")

# Key → values to keep (None keeps every value of the key)
POI_KEYS: dict[str, set[str] | None] = {
    "amenity": {
        "pub", "bar", "biergarten", "nightclub", "restaurant", "cafe", "fast_food",
        "ice_cream", "food_court", "library", "cinema", "theatre", "arts_centre",
        "community_centre", "pharmacy", "doctors", "dentist", "clinic", "hospital",
        "post_office", "atm", "bank", "marketplace", "kindergarten", "childcare",
        "charging_station", "gambling", "casino", "veterinary", "toilets",
    },
    "shop": None,
    # Not "garden" (mostly private gardens) or "pitch" (every sports pitch): too noisy
    "leisure": {
        "fitness_centre", "sports_centre", "swimming_pool", "park", "playground",
        "nature_reserve", "dog_park", "golf_course", "ice_rink",
    },
    "tourism": {"museum", "gallery", "attraction", "zoo", "aquarium", "theme_park"},
    "landuse": {"allotments"},
}  # fmt: skip

# Extra tags carried through for scoring and display
EXTRA_TAGS = [
    "name", "brand", "fhrs:id", "real_ale", "outdoor_seating", "beer_garden", "food",
    "microbrewery", "opening_hours", "website", "wheelchair", "cuisine", "access", "emergency",
    "healthcare", "healthcare:speciality", "boundary", "protect_class",
]  # fmt: skip


def _poi_tag(tags: osmium.osm.TagList) -> tuple[str, str] | None:
    for key, allowed in POI_KEYS.items():
        value = tags.get(key)
        if value and (allowed is None or value in allowed):
            return key, value
    return None


def extract_pois(pbf_path) -> pl.DataFrame:
    """Read every matching node and area from a .osm.pbf file."""
    wkb = osmium.geom.WKBFactory()
    rows: dict[str, list] = {
        k: [] for k in ["osm_type", "osm_id", "key", "value", "lon", "lat", *EXTRA_TAGS]
    }
    processor = (
        osmium.FileProcessor(str(pbf_path))
        .with_areas(osmium.filter.KeyFilter(*POI_KEYS))
        .with_filter(osmium.filter.KeyFilter(*POI_KEYS))
    )
    for obj in processor:
        match = _poi_tag(obj.tags)
        if match is None:
            continue
        if obj.is_node():
            if not obj.location.valid():
                continue
            lon, lat, osm_type, osm_id = obj.location.lon, obj.location.lat, "n", obj.id
        elif obj.is_area():
            try:
                point = shapely.from_wkb(wkb.create_multipolygon(obj)).representative_point()
            except Exception:  # broken multipolygons are common; skip them
                continue
            lon, lat = point.x, point.y
            osm_type = "w" if obj.from_way() else "r"
            osm_id = obj.orig_id()
        else:
            continue  # ways and relations arrive again as areas
        rows["osm_type"].append(osm_type)
        rows["osm_id"].append(osm_id)
        rows["key"].append(match[0])
        rows["value"].append(match[1])
        rows["lon"].append(lon)
        rows["lat"].append(lat)
        for tag in EXTRA_TAGS:
            rows[tag].append(obj.tags.get(tag))
    df = pl.DataFrame(rows, schema_overrides={"osm_id": pl.Int64})
    return df.rename({"fhrs:id": "fhrs_id"})


def osm_extracts() -> list[str]:
    """Registry slugs ``osm_*`` that cover an active nation and have been fetched."""
    from lix_core.codes import active_nations
    from lix_core.config import load_registry

    active = set(active_nations())
    return [
        slug
        for slug, spec in load_registry().items()
        if slug.startswith("osm_")
        and active & set(spec.coverage)
        and (data_dir("raw") / slug / f"{slug}.pbf").exists()
    ]


def stage_osm() -> pl.LazyFrame:
    frames = []
    for slug in osm_extracts():
        pbf = data_dir("raw") / slug / f"{slug}.pbf"
        logger.info(f"Extracting points of interest from {pbf} (a few minutes)")
        frames.append(extract_pois(pbf).with_columns(pl.lit(slug).alias("extract")))
    if not frames:
        raise FileNotFoundError("No OpenStreetMap extract fetched for the active nations")
    # Extracts overlap along borders: one row per OSM object
    df = pl.concat(frames).unique(["osm_type", "osm_id"], keep="first")
    x, y = lonlat_to_bng(df["lon"], df["lat"])
    df = df.with_columns(x=x, y=y)
    counts = df.group_by("key").len().sort("len", descending=True)
    logger.info(f"{df.height:,} POIs: {dict(counts.iter_rows())}")
    return df.lazy()
