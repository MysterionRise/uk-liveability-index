"""OpenStreetMap's highway network → a graph-ready ways table and the points measured
from it (#198).

``osm_ways``: one row per way with a ``highway`` tag, from the four Geofabrik extracts in
one pass each: its coordinates (BNG metres, in order; ways meeting at a node share its
exact coordinates, which stand in for node ids), its length, the tags
the walking and cycling rules read, and three derived flags. The cycle-infrastructure
and street-connectivity measures (#80, #81) read the points below; the walking network
of v0.8.x builds its graph from this table.

``osm_network_points``: junctions (a node met by three or more walkable way-ends, so a T
into a through road counts and two ways meeting end to end do not) and points every 50 m
along cycle infrastructure, each weighing the length it stands for in km.

pyosmium caches every node location (``flex_mem``) and hands Python only the ways: the
highway filter runs in C++, and so does each way's geometry (``WKBFactory``), decoded in
bulk by shapely into flat arrays with offsets, so England's ways fit in a few GB.

Licence: derived from OpenStreetMap, © OpenStreetMap contributors, ODbL. Both staged
tables are derivative databases and stay under the ODbL.
"""

from __future__ import annotations

from array import array

import numpy as np
import osmium
import polars as pl
import pyarrow as pa

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.stage.geo import lonlat_to_bng
from lix_pipeline.stage.osm import osm_extracts

logger = setup_logging("stage.osm_ways")

# Tags kept verbatim for the network rules (and the v0.8.x graph)
WAY_TAGS = [
    "highway", "foot", "sidewalk", "sidewalk:left", "sidewalk:right", "sidewalk:both",
    "access", "oneway", "oneway:bicycle", "bicycle", "cycleway", "cycleway:left",
    "cycleway:right", "cycleway:both", "segregated", "service", "area", "lit", "name",
    "footway",
]  # fmt: skip

# Walkable highway types; motorways and trunk roads only with a footway beside them
WALKABLE = {
    "footway", "path", "pedestrian", "steps", "living_street", "residential",
    "unclassified", "service", "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link", "track", "bridleway", "cycleway", "corridor", "road",
}  # fmt: skip
FAST_ROADS = {"motorway", "motorway_link", "trunk", "trunk_link"}
NOT_A_WAY = {"construction", "proposed", "raceway", "busway", "bus_guideway", "abandoned",
             "platform", "elevator", "escape", "emergency_bay", "rest_area",
             "services"}  # fmt: skip
SIDEWALK_YES = {"left", "right", "both", "yes", "separate"}
FOOT_YES = {"yes", "designated", "permissive"}
NO_ACCESS = {"private", "no"}
# Driveways and parking aisles stay in the graph but do not make a junction
MINOR_SERVICE = {"driveway", "parking_aisle", "drive-through"}
# Nor do pavements and crossings mapped as their own ways: where a city maps them, every
# kerb link and crossing met three way-ends, and central Westminster read 1,712 junctions
# per km² against a street grid's 150–350 (v0.7.0 release pass)
MINOR_FOOTWAY = {"sidewalk", "crossing", "traffic_island", "access_aisle", "link"}

# Cycle infrastructure: a cycleway, a path with cycling designated, or a lane or track
CYCLE_PATHS = {"path", "footway", "pedestrian", "bridleway", "track"}
CYCLE_LANES = {"lane", "track", "share_busway", "opposite_lane", "opposite_track", "separate"}
CYCLE_SAMPLE_M = 50.0


def _tag_in(col: str, values: set[str]) -> pl.Expr:
    return pl.col(col).is_in(sorted(values)).fill_null(False)


SIDEWALK_COLS = ("sidewalk", "sidewalk:left", "sidewalk:right", "sidewalk:both")
LANE_COLS = ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both")


def walkable_expr() -> pl.Expr:
    hw = pl.col("highway")
    foot_ok = _tag_in("foot", FOOT_YES)
    barred = (pl.col("foot") == "no").fill_null(False) | (_tag_in("access", NO_ACCESS) & ~foot_ok)
    beside = pl.any_horizontal([_tag_in(c, SIDEWALK_YES - {"separate"}) for c in SIDEWALK_COLS])
    kind = (
        pl.when(_tag_in("highway", FAST_ROADS))
        .then(foot_ok | beside)
        .otherwise(_tag_in("highway", WALKABLE))
    )
    return (hw.is_not_null() & ~_tag_in("highway", NOT_A_WAY) & ~barred & kind).alias("walkable")


def minor_expr() -> pl.Expr:
    driveway = (pl.col("highway") == "service").fill_null(False) & _tag_in("service", MINOR_SERVICE)
    pavement = _tag_in("highway", {"footway", "path", "cycleway"}) & _tag_in(
        "footway", MINOR_FOOTWAY
    )
    return (driveway | pavement).alias("minor")


def cycle_infra_expr() -> pl.Expr:
    hw = pl.col("highway")
    usable = hw.is_not_null() & ~_tag_in("highway", NOT_A_WAY) & ~_tag_in("access", NO_ACCESS)
    cycleway = (hw == "cycleway").fill_null(False) & ~(pl.col("bicycle") == "no").fill_null(False)
    designated = _tag_in("highway", CYCLE_PATHS) & (pl.col("bicycle") == "designated").fill_null(
        False
    )
    lane = pl.any_horizontal([_tag_in(c, CYCLE_LANES) for c in LANE_COLS])
    return (usable & (cycleway | designated | lane)).alias("cycle_infra")


def with_flags(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(walkable_expr(), minor_expr(), cycle_infra_expr())


def _flag(tags: dict[str, str | None], expr: pl.Expr) -> bool:
    row = pl.DataFrame({k: [tags.get(k)] for k in WAY_TAGS}, schema={k: pl.Utf8 for k in WAY_TAGS})
    return bool(row.select(expr).item())


def is_walkable(tags: dict[str, str | None]) -> bool:
    return _flag(tags, walkable_expr())


def is_minor(tags: dict[str, str | None]) -> bool:
    return _flag(tags, minor_expr())


def is_cycle_infra(tags: dict[str, str | None]) -> bool:
    return _flag(tags, cycle_infra_expr())


def extract_ways(pbf_path) -> pl.DataFrame:
    """Every way with a ``highway`` tag: its coordinates in order (BNG metres), length,
    tags and flags. The geometry is built in C++ (``WKBFactory``) and decoded in bulk;
    per-node Python objects made the first version 40 times slower than the file read."""
    import shapely

    factory = osmium.geom.WKBFactory()
    ids = array("q")
    wkbs: list[bytes] = []
    tags: dict[str, list] = {k: [] for k in WAY_TAGS}
    processor = (
        osmium.FileProcessor(str(pbf_path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations("flex_mem")
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    for w in processor:
        try:
            wkbs.append(factory.create_linestring(w))
        except (osmium.InvalidLocationError, RuntimeError):
            continue  # a way with a missing node, or fewer than two distinct ones
        ids.append(w.id)
        d = {tag.k: tag.v for tag in w.tags}
        for k in WAY_TAGS:
            tags[k].append(d.get(k))
    lines = shapely.from_wkb(np.array(wkbs, dtype=object))
    lonlat, which = shapely.get_coordinates(lines, return_index=True)
    n = np.bincount(which, minlength=len(lines)).astype(np.int64)
    offsets = np.concatenate([[0], np.cumsum(n)])
    x, y = lonlat_to_bng(pl.Series(lonlat[:, 0]), pl.Series(lonlat[:, 1]))
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)

    def lists(values: np.ndarray) -> pl.Series:
        arr = pa.ListArray.from_arrays(
            pa.array(offsets, pa.int64()), pa.array(values, pa.float32())
        )
        return pl.from_arrow(arr)

    seg = np.hypot(np.diff(x), np.diff(y))
    within = np.ones(len(seg), dtype=bool)
    within[offsets[1:-1] - 1] = False  # the step from one way's last node to the next's first
    length = np.add.reduceat(np.where(within, seg, 0.0), offsets[:-1]) if len(n) else np.zeros(0)
    df = pl.DataFrame(
        {
            "osm_id": np.frombuffer(ids, dtype=np.int64) if len(ids) else np.zeros(0, np.int64),
            "length_m": length.astype(np.float32),
            "n_nodes": n.astype(np.uint16),
            "xs": lists(x.astype(np.float32)),
            "ys": lists(y.astype(np.float32)),
            **{k: pl.Series(k, v, dtype=pl.Utf8) for k, v in tags.items()},
        }
    )
    return with_flags(df)


def stage_osm_ways() -> pl.LazyFrame:
    frames = []
    for slug in osm_extracts():
        pbf = data_dir("raw") / slug / f"{slug}.pbf"
        logger.info(f"Reading the highway network from {pbf}")
        frames.append(extract_ways(pbf).with_columns(pl.lit(slug).alias("extract")))
    if not frames:
        raise FileNotFoundError("No OpenStreetMap extract fetched for the active nations")
    # Extracts overlap along borders; a way is whole in each, so keep it once
    df = pl.concat(frames).unique("osm_id", keep="first", maintain_order=True)
    logger.info(
        f"{df.height:,} ways, {df['length_m'].sum() / 1000:,.0f} km; walkable "
        f"{df['walkable'].sum():,}, cycle infrastructure {df['cycle_infra'].sum():,} "
        f"({df.filter('cycle_infra')['length_m'].sum() / 1000:,.0f} km)"
    )
    return df.lazy()


def junctions(ways: pl.DataFrame) -> pl.DataFrame:
    """Nodes where three or more walkable, non-minor way-ends meet: an interior node
    counts twice (the way passes through), an endpoint once. Ways that share a node share
    its exact coordinates, so the coordinates are the node's identity."""
    w = ways.filter(pl.col("walkable") & ~pl.col("minor"))
    if w.height == 0:
        return pl.DataFrame(schema={"x": pl.Float64, "y": pl.Float64})
    flat = (
        w.select("xs", "ys", pl.col("n_nodes").cast(pl.Int64))
        .with_columns(pos=pl.int_ranges(0, pl.col("n_nodes")))
        .explode(["xs", "ys", "pos"])
        .with_columns(
            degree=pl.when((pl.col("pos") == 0) | (pl.col("pos") == pl.col("n_nodes") - 1))
            .then(1)
            .otherwise(2)
        )
    )
    deg = flat.group_by("xs", "ys").agg(pl.col("degree").sum())
    return deg.filter(pl.col("degree") >= 3).select(
        pl.col("xs").cast(pl.Float64).alias("x"), pl.col("ys").cast(pl.Float64).alias("y")
    )


def cycle_samples(ways: pl.DataFrame, spacing_m: float = CYCLE_SAMPLE_M) -> pl.DataFrame:
    """Points every ``spacing_m`` along cycle infrastructure (at the middle of each
    stretch), each weighing the km it stands for, so their sum is the network's length."""
    import shapely

    c = ways.filter(pl.col("cycle_infra") & (pl.col("length_m") > 0))
    if c.height == 0:
        return pl.DataFrame(schema={"x": pl.Float64, "y": pl.Float64, "weight": pl.Float64})
    flat = c.select("xs", "ys").with_row_index("way").explode(["xs", "ys"])
    lines = shapely.linestrings(
        flat["xs"].to_numpy().astype(float),
        flat["ys"].to_numpy().astype(float),
        indices=flat["way"].to_numpy(),
    )
    length = shapely.length(lines)
    n = np.maximum(1, np.round(length / spacing_m)).astype(np.int64)
    which = np.repeat(np.arange(len(lines)), n)
    step = length / n
    first = np.concatenate([[0], np.cumsum(n)[:-1]])
    k = np.arange(len(which)) - np.repeat(first, n)
    pts = shapely.line_interpolate_point(lines[which], (k + 0.5) * step[which])
    return pl.DataFrame(
        {
            "x": shapely.get_x(pts),
            "y": shapely.get_y(pts),
            "weight": (step[which] / 1000.0),
        }
    )


def stage_osm_network_points() -> pl.LazyFrame:
    ways = pl.read_parquet(
        data_dir("staged") / "osm_ways.parquet",
        columns=["xs", "ys", "n_nodes", "length_m", "walkable", "minor", "cycle_infra"],
    )
    j = junctions(ways).with_columns(kind=pl.lit("junction"), weight=pl.lit(1.0))
    c = cycle_samples(ways).with_columns(kind=pl.lit("cycle_sample"))
    out = pl.concat([j.select("kind", "x", "y", "weight"), c.select("kind", "x", "y", "weight")])
    logger.info(f"{j.height:,} junctions, {c.height:,} cycle samples ({c['weight'].sum():,.0f} km)")
    return out.lazy()
