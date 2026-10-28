"""Searchable places from OpenStreetMap, for nations OS Open Names does not cover.

OS Open Names stops at Great Britain, so Northern Ireland's (and later Ireland's)
settlements come from the nation's OpenStreetMap extract: every ``place=*`` node with a
name, typed like the OS places (City, Town, Village, Hamlet, Suburban Area) so the search
ranks them the same way. Irish names come along where mappers have added ``name:ga``.

Licence: derived from OpenStreetMap, © OpenStreetMap contributors, ODbL.
"""

import geopandas as gpd
import osmium
import polars as pl

from lix_core.config import load_nations, load_registry
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.crs import lonlat_to_bng

logger = setup_logging("stage.places_osm")

# OSM place values → the OS Open Names LOCAL_TYPE the search knows
PLACE_TYPES = {
    "city": "City",
    "town": "Town",
    "village": "Village",
    "hamlet": "Hamlet",
    "suburb": "Suburban Area",
    "neighbourhood": "Suburban Area",
    "quarter": "Suburban Area",
}


class _PlaceNodes(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []

    def node(self, n):
        kind = PLACE_TYPES.get(n.tags.get("place", ""))
        name = n.tags.get("name")
        if not kind or not name or not n.location.valid():
            return
        # "Derry/Londonderry" is one node with two names: the first is the name, the
        # second the alternative (as OS Open Names carries name_alt)
        shown, _, alt = (n.tags.get("name:en") or name).partition("/")
        self.rows.append(
            {
                "osm_id": n.id,
                "name": shown.strip(),
                "name_alt": alt.strip() or None,
                "name_ga": n.tags.get("name:ga"),
                "place_type": kind,
                "lon": n.location.lon,
                "lat": n.location.lat,
            }
        )


def places_from_pbf(pbf) -> pl.DataFrame:
    """Every named place node of an extract: osm_id, name, name_ga, place_type, lon, lat."""
    handler = _PlaceNodes()
    handler.apply_file(str(pbf))
    schema = {
        "osm_id": pl.Int64,
        "name": pl.Utf8,
        "name_alt": pl.Utf8,
        "name_ga": pl.Utf8,
        "place_type": pl.Utf8,
        "lon": pl.Float64,
        "lat": pl.Float64,
    }
    return pl.DataFrame(handler.rows, schema=schema)


def extracts_for(nation: str) -> list:
    """The fetched ``osm_*`` extracts covering ``nation``."""
    out = []
    for slug, spec in load_registry().items():
        if slug.startswith("osm_") and nation in spec.coverage:
            pbf = data_dir("raw") / slug / f"{slug}.pbf"
            if pbf.exists():
                out.append(pbf)
    return out


def council_of(df: pl.DataFrame, nation: str) -> pl.Series:
    """The council name under each place's point, from the nation's upper-tier polygons."""
    from lix_pipeline.geo.boundaries import upper_polygons

    lad = upper_polygons(nation)[["lad_nm", "geometry"]]
    pts = gpd.GeoDataFrame(
        {"_row": range(df.height)},
        geometry=gpd.points_from_xy(df["x"].to_numpy(), df["y"].to_numpy()),
        crs=lad.crs,
    )
    hit = gpd.sjoin(pts, lad, how="left", predicate="within").drop_duplicates("_row")
    names = hit.set_index("_row")["lad_nm"].reindex(range(df.height))
    return pl.Series("local_authority", names.where(names.notna(), None).to_list(), dtype=pl.Utf8)


# A place takes the district of the nearest live postcode this close (a village centre
# is rarely more than a few hundred metres from one)
DISTRICT_WITHIN_M = 3_000.0


def nearest_district(places: pl.DataFrame, nation: str) -> pl.Series:
    """The postcode district (outward code) of the live postcode nearest each place, for
    a nation with postcodes in the NSPL (Northern Ireland); null elsewhere and beyond
    ``DISTRICT_WITHIN_M``. OS Open Names gives Great Britain's places theirs (#211)."""
    import numpy as np
    from scipy.spatial import cKDTree

    from lix_pipeline.geo.nspl import load_nspl

    empty = pl.Series("postcode_district", [None] * places.height, dtype=pl.Utf8)
    if nation != "N" or places.height == 0:
        return empty
    pcs = (
        load_nspl(nations=(nation,), live_only=True)
        .select("postcode", "east1m", "north1m")
        .drop_nulls()
        .collect()
    )
    if pcs.height == 0:
        return empty
    tree = cKDTree(np.column_stack([pcs["east1m"].to_numpy(), pcs["north1m"].to_numpy()]))
    dist, idx = tree.query(
        np.column_stack([places["x"].to_numpy(), places["y"].to_numpy()]),
        distance_upper_bound=DISTRICT_WITHIN_M,
    )
    outward = pcs["postcode"].str.split(" ").list.first().to_list()
    return pl.Series(
        "postcode_district",
        [outward[i] if np.isfinite(d) else None for d, i in zip(dist, idx, strict=True)],
        dtype=pl.Utf8,
    )


def osm_places(nation: str) -> pl.DataFrame:
    """Places of ``nation`` in the shape of ``stage_places``' OS Open Names rows."""
    pbfs = extracts_for(nation)
    if not pbfs:
        raise FileNotFoundError(f"No OpenStreetMap extract fetched for nation {nation}")
    frames = [places_from_pbf(p) for p in pbfs]
    df = pl.concat(frames).unique("osm_id", keep="first")
    x, y = lonlat_to_bng(df["lon"], df["lat"])
    df = df.with_columns(x=x, y=y)
    country = load_nations().nations[nation].name
    df = df.with_columns(council_of(df, nation))
    # Places over the border carry no council: outside every active area
    df = df.filter(pl.col("local_authority").is_not_null())
    df = df.with_columns(nearest_district(df, nation))
    out = df.select(
        pl.concat_str([pl.lit("osm:"), pl.col("osm_id").cast(pl.Utf8)]).alias("place_id"),
        "name",
        pl.lit(None, pl.Utf8).alias("name_cy"),
        pl.lit(None, pl.Utf8).alias("name_gd"),
        "name_ga",
        "place_type",
        "x",
        "y",
        "postcode_district",
        "local_authority",
        pl.lit(None, pl.Utf8).alias("region"),
        pl.lit(country).alias("country"),
        "lon",
        "lat",
    )
    logger.info(f"{nation}: {out.height:,} OpenStreetMap places ({len(pbfs)} extract(s))")
    return out
