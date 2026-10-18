"""NSPL → staged Parquet (all nations, live and terminated postcodes).

The ONS publishes each nation's eastings and northings on its own grid: British National
Grid for Great Britain, Irish Grid for Northern Ireland. The staged table carries one
grid, the working CRS of config/nations.yaml, so every reader (postcode geocoding, the
residential origins of access measures, raster sampling) can take ``east1m``/``north1m``
as they are. Nations whose ``input_crs`` differs get those columns recomputed from
``lat``/``long`` here, and nowhere else.
"""

from pathlib import Path

import numpy as np
import polars as pl

from lix_core.config import load_nations
from lix_core.geo import bbox_to_working, lonlat_to_working
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.nspl import find_nspl_csv, read_nspl_raw

logger = setup_logging("stage.nspl")

# Recomputed points may sit this far outside the nation's declared bounding box (metres)
BBOX_MARGIN_M = 20_000


def input_grids() -> dict[int, list[str]]:
    """Input CRS → the nations whose NSPL coordinates use it, for those that differ from
    the working CRS."""
    cfg = load_nations()
    out: dict[int, list[str]] = {}
    for code, nation in cfg.nations.items():
        if nation.input_crs != cfg.build.working_crs:
            out.setdefault(nation.input_crs, []).append(code)
    return out


def reproject_inputs(
    lf: pl.LazyFrame,
    grids: dict[int, list[str]] | None = None,
    bboxes: dict[str, tuple[float, float, float, float]] | None = None,
) -> pl.LazyFrame:
    """``east1m``/``north1m`` recomputed from ``lat``/``long`` for the nations in
    ``grids`` (default: those whose ``input_crs`` is not the working CRS); other rows
    are untouched. Every recomputed point must land inside its nation's bounding box
    (``bboxes``, default from config) projected to the working CRS, so a grid mix-up
    fails the stage instead of placing a nation 100 km away."""
    grids = input_grids() if grids is None else grids
    nations = [n for codes in grids.values() for n in codes]
    if not nations:
        return lf
    if bboxes is None:
        cfg = load_nations().nations
        bboxes = {n: cfg[n].bbox for n in nations}

    df = lf.collect()
    nation = pl.col("ctry_cd").str.slice(0, 1)
    located = pl.col("lat").is_between(-90, 90) & pl.col("long").is_between(-180, 180)
    # Postcodes without a grid reference carry lat 99.999999: project a harmless (0, 0)
    # for them so no infinity reaches the integer cast, and leave their columns alone
    has_point = df.select(located).to_series().to_numpy()
    lon = np.where(has_point, df["long"].fill_null(0).to_numpy(), 0.0)
    lat = np.where(has_point, df["lat"].fill_null(0).to_numpy(), 0.0)
    x, y = lonlat_to_working(lon, lat)
    df = df.with_columns(pl.Series("_x", x), pl.Series("_y", y))
    moved = nation.is_in(nations) & located
    df = df.with_columns(
        pl.when(moved)
        .then(pl.col("_x").round().cast(pl.Int32))
        .otherwise("east1m")
        .alias("east1m"),
        pl.when(moved)
        .then(pl.col("_y").round().cast(pl.Int32))
        .otherwise("north1m")
        .alias("north1m"),
    ).drop("_x", "_y")

    for code in nations:
        rows = df.filter((nation == code) & located)
        if not rows.height:
            continue
        w, s, e, n = bbox_to_working(bboxes[code])
        outside = rows.filter(
            (pl.col("east1m") < w - BBOX_MARGIN_M)
            | (pl.col("east1m") > e + BBOX_MARGIN_M)
            | (pl.col("north1m") < s - BBOX_MARGIN_M)
            | (pl.col("north1m") > n + BBOX_MARGIN_M)
        )
        if outside.height:
            sample = outside.select("postcode", "east1m", "north1m").head(3).rows()
            raise ValueError(
                f"{code}: {outside.height:,} postcodes land outside the nation's bounding "
                f"box after reprojection, e.g. {sample}; check input_crs in nations.yaml"
            )
        logger.info(
            f"{code}: {rows.height:,} postcodes reprojected from EPSG:"
            f"{next(crs for crs, codes in grids.items() if code in codes)} to the working CRS"
        )
    return df.lazy()


def stage_nspl(raw_path: Path | None = None) -> pl.LazyFrame:
    """Stage the full NSPL (all nations, live and terminated) with stable column names
    and every coordinate in the working CRS."""
    if raw_path is None:
        raw_path = find_nspl_csv(data_dir("raw") / "nspl")
    logger.info(f"Staging NSPL from {raw_path}")
    return reproject_inputs(read_nspl_raw(raw_path))
