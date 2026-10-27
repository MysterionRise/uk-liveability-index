"""The coastline as points, for the distance from homes to the sea (#130).

Great Britain's coast is OS Boundary-Line's high water mark, Ireland's Tailte Éireann's
high water mark (National Water Marks), Northern Ireland's the OSNI outline with its land
border and its loughs dropped: an outline runs along the border with Ireland, which is no
coast, and holds Lough Neagh as a hole and the Fermanagh lakes on its outer ring, whose
shores are no coast either (the v0.6.3 release pass found Lurgan 2 km "from the sea").
Each line is densified to a vertex every 100 m, so the nearest vertex is within 50 m of
the true distance, well inside the kilometre the indicator shows.
"""

import geopandas as gpd
import numpy as np
import polars as pl
import shapely

from lix_core.codes import active_nations
from lix_core.geo import working_crs
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file

logger = setup_logging("stage.coastline")

SPACING_M = 100.0
# Vertices this close to the neighbouring state's land are its border, not the sea: the
# border runs through rivers and loughs, where the outline sits up to a kilometre from
# the neighbour's generalised polygons
BORDER_M = 1000.0
# Inland water on an outline's outer ring, as a box of (west, south, east, north) in
# longitude and latitude: the OSNI outline opens to the border through the Fermanagh lake
# basin (Lower and Upper Lough Erne, Lough MacNean, Lough Melvin), and no sea coast of
# Northern Ireland lies in this box (Carlingford Lough is east of it, Lough Foyle north)
INLAND_WATER_LONLAT = {"osni_ni_outline": (-8.25, 54.0, -7.15, 54.62)}


def _outer_rings(g):
    """A polygon's exterior ring; a hole is a lake, and a lake's shore is no coast."""
    if g.geom_type == "Polygon":
        return g.exterior
    if g.geom_type == "MultiPolygon":
        return shapely.MultiLineString([p.exterior for p in g.geoms])
    return g


def coast_points(
    lines: gpd.GeoDataFrame, source: str, spacing_m: float = SPACING_M
) -> pl.DataFrame:
    """A vertex at most ``spacing_m`` apart along every line (polygons contribute their
    outer rings, not their holes), in the working CRS."""
    geoms = lines.geometry
    if lines.crs is not None and lines.crs.to_epsg() != working_crs():
        geoms = geoms.to_crs(working_crs())
    rings = geoms.apply(_outer_rings)
    dense = shapely.segmentize(np.asarray(rings.values, dtype=object), spacing_m)
    xy = shapely.get_coordinates(dense)
    return pl.DataFrame({"x": xy[:, 0], "y": xy[:, 1], "source": source}).unique(
        maintain_order=True
    )


def drop_land_border(
    points: pl.DataFrame, land: gpd.GeoDataFrame, within_m: float = BORDER_M
) -> pl.DataFrame:
    """The vertices within ``within_m`` of ``land`` (the neighbouring state's polygons)
    dropped: an outline's land border is not coast."""
    if points.is_empty() or len(land) == 0:
        return points
    geoms = land.geometry
    if land.crs is not None and land.crs.to_epsg() != working_crs():
        geoms = geoms.to_crs(working_crs())
    tree = shapely.STRtree(geoms.values)
    pts = shapely.points(points["x"].to_numpy(), points["y"].to_numpy())
    hit, _ = tree.query(pts, predicate="dwithin", distance=within_m)
    keep = np.ones(points.height, dtype=bool)
    keep[np.unique(hit)] = False
    return points.filter(pl.Series(keep))


def drop_inland_water(
    points: pl.DataFrame, box_lonlat: tuple[float, float, float, float]
) -> pl.DataFrame:
    """The vertices inside the box (west, south, east, north; degrees) dropped: a lake
    basin on an outline's outer ring."""
    from pyproj import Transformer

    if points.is_empty():
        return points
    to_lonlat = Transformer.from_crs(working_crs(), 4326, always_xy=True)
    lon, lat = to_lonlat.transform(points["x"].to_numpy(), points["y"].to_numpy())
    west, south, east, north = box_lonlat
    inside = (lon >= west) & (lon <= east) & (lat >= south) & (lat <= north)
    return points.filter(pl.Series(~inside))


def _gb_high_water() -> gpd.GeoDataFrame:
    import pyogrio

    folder = data_dir("raw") / "os_boundary_line"
    path = next(iter(sorted(folder.glob("**/*.gpkg"))), None)
    if path is None:
        raise FileNotFoundError(f"No GeoPackage under {folder}")
    layers = [name for name, _kind in pyogrio.list_layers(path)]
    layer = next((n for n in layers if "high_water" in n.lower()), None)
    if layer is None:
        raise ValueError(f"{path.name}: no high water layer among {layers}")
    return gpd.read_file(path, layer=layer, columns=[])


def stage_coastline() -> pl.LazyFrame:
    """One row per coastline vertex of every active nation: ``x``, ``y`` (working CRS)
    and the source it came from."""
    nations = set(active_nations())
    frames = []
    if nations & {"E", "W", "S"}:
        frames.append(coast_points(_gb_high_water(), "os_boundary_line"))
    if "I" in nations:
        frames.append(
            coast_points(
                gpd.read_file(raw_file("te_high_water_mark", "gpkg")), "te_high_water_mark"
            )
        )
    if "N" in nations:
        outline = coast_points(
            gpd.read_file(raw_file("osni_ni_outline", "geojson")), "osni_ni_outline"
        )
        land = raw_file("te_admin_areas", "gpkg")
        if land.exists():
            before = outline.height
            outline = drop_land_border(outline, gpd.read_file(land, columns=[]))
            logger.info(f"Northern Ireland: {before - outline.height:,} border vertices dropped")
        else:
            logger.warning("te_admin_areas not fetched: the Irish land border counts as coast")
        before = outline.height
        outline = drop_inland_water(outline, INLAND_WATER_LONLAT["osni_ni_outline"])
        logger.info(f"Northern Ireland: {before - outline.height:,} lake-basin vertices dropped")
        frames.append(outline)
    if not frames:
        raise ValueError("stage_coastline: no active nation has a coastline source")
    out = pl.concat(frames)
    for source, n in out.group_by("source").len().iter_rows():
        logger.info(f"{source}: {n:,} coastline vertices")
    return out.lazy()
