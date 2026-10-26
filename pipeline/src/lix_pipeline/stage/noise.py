"""Noise at homes: the strategic noise maps sampled at residential postcodes.

Each nation publishes its Round 4 maps differently: England (Defra, 2021 traffic) a 10m
grid per source, road, rail and aircraft; Wales (2022) road and rail; Scotland one
consolidated grid of every mapped source (road, rail, airports, industry) plus an
industry grid for its five cities.
``NOISE_SOURCES`` lists the grids per nation by kind, and every kind is sampled where the
nation has it; ``share_any_*`` takes the loudest kind at each home, so the scored
indicator means the same everywhere: the share of homes in mapped noise. Cells with no
mapped source hold 0 dB (or nodata), so a home on an unmapped minor road reads as quiet.
"""

import re
from pathlib import Path

import numpy as np
import polars as pl

from lix_core.codes import active_nations, nation_of
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes
from lix_pipeline.geo.rasters import sample_tiles_at_points

logger = setup_logging("stage.noise")

# nation → kind (road, rail, air, industry, all) → metric (lden, lnight) → registry slug(s)
NOISE_SOURCES: dict[str, dict[str, dict[str, str | list[str]]]] = {
    "E": {
        "road": {"lden": "defra_noise_road_lden", "lnight": "defra_noise_road_lnight"},
        "rail": {"lden": "defra_noise_rail_lden", "lnight": "defra_noise_rail_lnight"},
        "air": {"lden": "defra_noise_air_lden", "lnight": "defra_noise_air_lnight"},
    },
    "W": {
        "road": {"lden": "wg_noise_road_lden", "lnight": "wg_noise_road_lnight"},
        "rail": {"lden": "wg_noise_rail_lden", "lnight": "wg_noise_rail_lnight"},
        "industry": {"lden": "wg_noise_industry_lden", "lnight": "wg_noise_industry_lnight"},
    },
    "S": {
        "all": {"lden": "sepa_noise_all_lden", "lnight": "sepa_noise_all_lnight"},
        "industry": {"lden": "sepa_noise_industry_lden", "lnight": "sepa_noise_industry_lnight"},
    },
    # Northern Ireland: round 3 contour polygons (DAERA), one shapefile per source and metric
    "N": {
        "all": {"lden": "daera_noise_r3", "lnight": "daera_noise_r3"},
        "road": {"lden": "daera_noise_r3", "lnight": "daera_noise_r3"},
        "rail": {"lden": "daera_noise_r3", "lnight": "daera_noise_r3"},
        "air": {"lden": "daera_noise_r3", "lnight": "daera_noise_r3"},
        "industry": {"lden": "daera_noise_r3", "lnight": "daera_noise_r3"},
    },
    # Ireland: round 4 contour polygons (EPA), one WFS layer per source and metric, the
    # agglomerations and the national roads, railways and airports in separate layers
    "I": {
        "road": {
            "lden": ["epa_noise_road_agg", "epa_noise_road_nat"],
            "lnight": ["epa_noise_road_agg_lnight", "epa_noise_road_nat_lnight"],
        },
        "rail": {
            "lden": ["epa_noise_rail_agg", "epa_noise_rail_nat"],
            "lnight": ["epa_noise_rail_agg_lnight", "epa_noise_rail_nat_lnight"],
        },
        "air": {"lden": ["epa_noise_air_nat"], "lnight": ["epa_noise_air_nat_lnight"]},
        "industry": {
            "lden": ["epa_noise_industry_agg"],
            "lnight": ["epa_noise_industry_agg_lnight"],
        },
    },
}
# Contour files per metric and kind inside a polygon source (globs under its raw folder):
# DAERA's shapefiles by name (the night-time files are "lngt" or "lnight" by source), the
# EPA's WFS pages one layer per source and metric
CONTOUR_FILES: dict[str, dict[str, dict[str, list[str]]]] = {
    "daera_noise_r3": {
        "lden": {
            "all": ["**/R3_con_lden.shp"],
            "road": ["**/R3_agg_road_lden.shp", "**/R3_mroad_lden.shp"],
            "rail": ["**/R3_agg_rail_lden.shp", "**/R3_mrail_lden.shp"],
            "air": ["**/R3_bca_lden.shp", "**/R3_bia_lden.shp"],
            "industry": ["**/R3_agg_ind_lden.shp"],
        },
        "lnight": {
            "all": ["**/R3_con_lngt.shp"],
            "road": ["**/R3_agg_road_lngt.shp", "**/R3_mroad_lngt.shp"],
            "rail": ["**/R3_agg_rail_lnight.shp", "**/R3_mrail_lnight.shp"],
            "air": ["**/R3_bca_lngt.shp", "**/R3_bia_lngt_all.shp"],
            "industry": ["**/R3_agg_ind_lngt.shp"],
        },
    },
    **{
        f"epa_noise_{kind_slug}{suffix}": {metric: {kind: ["page_*.geojson"]}}
        for kind, kind_slug in (
            ("road", "road_agg"),
            ("road", "road_nat"),
            ("rail", "rail_agg"),
            ("rail", "rail_nat"),
            ("air", "air_nat"),
            ("industry", "industry_agg"),
        )
        for metric, suffix in (("lden", ""), ("lnight", "_lnight"))
    },  # fmt: skip
}
# DAERA's gridcode is the top of a 5 dB band (54 = 50–54.9 dB … 74 = 70–74.9; 1000 = 75+):
# a home takes the band's floor, as the rasters' cells hold the modelled level
CONTOUR_BANDS = {54: 50.0, 59: 55.0, 64: 60.0, 69: 65.0, 74: 70.0, 1000: 75.0}
# The EPA writes the band as text ("55-59dB", ">=75dB"): the floor is its first number
BAND_TEXT = re.compile(r"(\d+)")


def band_floor(label: str | None) -> float:
    """ "55-59dB" → 55, ">=75dB" → 75, "75+" → 75; NaN for an unreadable label."""
    m = BAND_TEXT.search(str(label or ""))
    return float(m.group(1)) if m else float("nan")


KINDS = ("road", "rail", "air", "industry", "all")
# 55 dB Lden is the level at which the Environmental Noise Directive counts people as
# exposed, but at that level the answer depends on whether minor roads were mapped, which
# they are everywhere in Wales and only inside the larger urban areas in England (39% of
# Welsh postcodes pass it against 30% of English ones, most on ordinary streets). At
# 60 dB a home is beside a busy road wherever it is (12% and 14%), so that is the
# cut-off the indicator uses; 55 and 65 are kept.
NOISY_DB = 60.0
REPORTED_DB = 55.0
# The Environmental Noise Directive reports night-time exposure from 50 dB Lnight
NIGHT_DB = 50.0
LOUD_DB = 65.0


def _tiles(slug: str) -> list[Path]:
    return sorted((data_dir("raw") / slug).glob("**/*.tif"))


def _contours(slug: str, kind: str, metric: str = "lden") -> list[Path]:
    folder = data_dir("raw") / slug
    patterns = CONTOUR_FILES.get(slug, {}).get(metric, {}).get(kind, [])
    return sorted(p for pattern in patterns for p in folder.glob(pattern))


def _slugs(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _tiles_of(value: str | list[str]) -> list[Path]:
    return [p for slug in _slugs(value) for p in _tiles(slug)]


def _contours_of(value: str | list[str], kind: str, metric: str = "lden") -> list[Path]:
    return [p for slug in _slugs(value) for p in _contours(slug, kind, metric)]


def contour_db_at_points(paths: list[Path], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The noise level under each point from contour polygons: the floor of the loudest
    band any of the files puts it in, NaN where none does."""
    import geopandas as gpd
    import pyogrio

    from lix_core.geo import working_crs
    from lix_pipeline.geo.contours import band_at_points

    # The EPA's contour files hold a few enormous multipolygons (a gigabyte of GeoJSON for
    # the road agglomerations): GDAL refuses objects over 200 MB unless told otherwise
    pyogrio.set_gdal_config_options({"OGR_GEOJSON_MAX_OBJ_SIZE": "0"})
    out = np.full(len(x), np.nan)
    for path in paths:
        polys = gpd.read_file(path, use_arrow=True)
        if polys.crs is not None and polys.crs.to_epsg() != working_crs():
            polys = polys.to_crs(working_crs())
        # A band is one multipolygon of thousands of parts (every noisy road of a city):
        # split into parts so the spatial index narrows each point to a few small polygons
        polys = polys.explode(index_parts=False, ignore_index=True)
        if "gridcode" in polys.columns:
            polys = polys.assign(db=polys["gridcode"].map(CONTOUR_BANDS))
        else:
            band = next((c for c in polys.columns if c.lower() in ("db_value", "db", "band")), None)
            if band is None:
                raise ValueError(
                    f"{path.name}: no gridcode or dB_Value column in {list(polys.columns)}"
                )
            polys = polys.assign(db=polys[band].map(band_floor))
        unknown = polys["db"].isna().sum()
        if unknown:
            raise ValueError(f"{path.name}: {unknown} polygons with an unknown band")
        level = band_at_points(polys, "db", x, y)
        out = np.where(np.isnan(out), level, np.fmax(out, level))
    return out


def _sample(
    x: np.ndarray,
    y: np.ndarray,
    layers: dict[str, list[Path]],
    contours: dict[str, list[Path]] | None,
) -> dict[str, np.ndarray]:
    """The level of each kind at each home: grids first, contours where a kind has them;
    a cell no source reaches reads 0 dB."""
    sampled = {
        kind: np.nan_to_num(sample_tiles_at_points(tiles, x, y), nan=0.0)
        for kind, tiles in layers.items()
        if tiles
    }
    for kind, paths in (contours or {}).items():
        if paths:
            sampled[kind] = np.nan_to_num(contour_db_at_points(paths, x, y), nan=0.0)
    return sampled


def noise_at_homes(
    homes: pl.DataFrame,
    layers: dict[str, list[Path]],
    contours: dict[str, list[Path]] | None = None,
    night_layers: dict[str, list[Path]] | None = None,
    night_contours: dict[str, list[Path]] | None = None,
) -> pl.DataFrame:
    """Per area: the share of residential postcodes at or above 60 dB Lden from any mapped
    kind (and at 55 and 65), each kind's own share at 60 and at 55, the share from road
    or rail alone at 60, and the mean of the loudest kind; with night-time maps, the share
    at or above 50 and 55 dB Lnight from any kind and the mean of the loudest.

    ``layers`` maps a kind (road, rail, air, industry, all) to its grid tiles; a kind with
    no grid counts as 0 dB, like a cell no source reaches. Aircraft and industry are
    reported at 55 dB too: those maps cover whole contours, so the minor-road caveat
    behind the 60 dB cut-off does not apply to them. The night-time columns are null for
    a nation without night maps.
    """
    x = homes["east1m"].to_numpy().astype(float)
    y = homes["north1m"].to_numpy().astype(float)
    sampled = _sample(x, y, layers, contours)
    if not sampled:
        raise ValueError("noise_at_homes needs at least one grid or contour set")
    night = _sample(x, y, night_layers or {}, night_contours)
    db_columns = [pl.Series(f"{kind}_db", values) for kind, values in sampled.items()]
    db_columns += [pl.Series(f"{kind}_night_db", values) for kind, values in night.items()]
    loudest = pl.max_horizontal([f"{kind}_db" for kind in sampled])
    loudest_night = pl.max_horizontal([f"{kind}_night_db" for kind in night]) if night else None
    night_columns = (
        [
            (loudest_night >= NIGHT_DB).mean().alias("share_any_night_50"),
            (loudest_night >= REPORTED_DB).mean().alias("share_any_night_55"),
            loudest_night.mean().alias("mean_night_db"),
        ]
        if loudest_night is not None
        else [
            pl.lit(None, pl.Float64).alias(c)
            for c in ("share_any_night_50", "share_any_night_55", "mean_night_db")
        ]
    )
    surface = [k for k in ("road", "rail") if k in sampled]
    road_rail = (
        (pl.max_horizontal([f"{k}_db" for k in surface]) >= NOISY_DB).mean()
        if surface
        else pl.lit(None, pl.Float64)
    )
    return (
        homes.select("lsoa21cd")
        .with_columns(*db_columns)
        .group_by("lsoa21cd")
        .agg(
            pl.len().alias("n_postcodes"),
            *[
                (pl.col(f"{kind}_db") >= NOISY_DB).mean().alias(f"share_{kind}_60")
                for kind in sampled
            ],
            *[
                (pl.col(f"{kind}_db") >= REPORTED_DB).mean().alias(f"share_{kind}_55")
                for kind in sampled
            ],
            road_rail.alias("share_road_rail_60"),
            (loudest >= NOISY_DB).mean().alias("share_any_60"),
            (loudest >= REPORTED_DB).mean().alias("share_any_55"),
            (loudest >= LOUD_DB).mean().alias("share_any_65"),
            loudest.mean().alias("mean_db"),
            *night_columns,
        )
        .sort("lsoa21cd")
    )


def stage_noise() -> pl.LazyFrame:
    """Noise exposure per area for every active nation with a noise map; each nation's
    kinds appear as ``share_<kind>_60`` (null where a nation has no such grid)."""
    homes = residential_postcodes()
    frames = []
    for nation, kinds in NOISE_SOURCES.items():
        if nation not in active_nations():
            continue
        layers = {kind: _tiles_of(slugs["lden"]) for kind, slugs in kinds.items()}
        contours = {kind: _contours_of(slugs["lden"], kind) for kind, slugs in kinds.items()}
        night = {kind: slugs["lnight"] for kind, slugs in kinds.items() if "lnight" in slugs}
        night_layers = {kind: _tiles_of(value) for kind, value in night.items()}
        night_contours = {
            kind: _contours_of(value, kind, "lnight") for kind, value in night.items()
        }
        pts = homes.filter(nation_of("lsoa21cd") == nation)
        df = noise_at_homes(pts, layers, contours, night_layers, night_contours)
        noisy = df.select((pl.col("share_any_60") * pl.col("n_postcodes")).sum()).item()
        at_night = df.select((pl.col("share_any_night_50") * pl.col("n_postcodes")).sum()).item()
        logger.info(
            f"{nation}: {df.height:,} areas from {pts.height:,} postcodes over {sorted(layers)}; "
            f"{noisy / max(pts.height, 1):.1%} of postcodes at 60 dB Lden or more"
            + (
                f", {at_night / max(pts.height, 1):.1%} at 50 dB Lnight or more"
                if at_night is not None
                else ", no night-time map"
            )
        )
        frames.append(df)
    columns = ["lsoa21cd", "n_postcodes", *[f"share_{k}_60" for k in KINDS]]
    columns += [f"share_{k}_55" for k in KINDS]
    columns += ["share_road_rail_60", "share_any_60", "share_any_55", "share_any_65", "mean_db"]
    columns += ["share_any_night_50", "share_any_night_55", "mean_night_db"]
    out = pl.concat(frames, how="diagonal_relaxed").sort("lsoa21cd")
    for column in columns:
        if column not in out.columns:
            out = out.with_columns(pl.lit(None, pl.Float64).alias(column))
    return out.select(columns).lazy()
