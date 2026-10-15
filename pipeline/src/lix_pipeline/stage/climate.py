"""Climate around homes, from the Met Office's HadUK-Grid 1km thirty-year averages.

Each variable is a NetCDF file on the Ordnance Survey grid (cell centres at 500m
offsets, like Defra's air-quality grids), one value per cell for the whole period or
one per season. Every residential postcode takes its cell's value and the area takes
the mean over its postcodes, so the figures are population-weighted the way the
air-quality ones are. The files sit behind a free CEDA login, so the source is a
manual download and the stager raises a clear error when none is present; a
variable's file may be absent (its column is then null).
"""

from pathlib import Path

import netCDF4
import numpy as np
import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes
from lix_pipeline.geo.grids import sample_grid_at_points

logger = setup_logging("stage.climate")

SLUG = "haduk_grid"
KNOT_MS = 0.514444

# Output column: (HadUK variable, period, season); periods are the file's ann-30y or
# seas-30y averages, the season a lower-case Met Office label (djf, mam, jja, son)
VARIABLES: dict[str, tuple[str, str, str | None]] = {
    "sunshine_hours": ("sun", "ann", None),
    "rainfall_mm": ("rainfall", "ann", None),
    "rain_days": ("raindays1mm", "ann", None),
    "temp_mean": ("tas", "ann", None),
    "summer_tmax": ("tasmax", "seas", "jja"),
    "winter_tmin": ("tasmin", "seas", "djf"),
    "air_frost_days": ("airfrost", "ann", None),
    "summer_days": ("summerdays", "ann", None),
    "wind_ms": ("sfcWind", "ann", None),
    "snow_days": ("snowLying", "ann", None),
}
# The scored indicators' columns: the stage fails without their files
REQUIRED = ("sunshine_hours", "rain_days", "summer_tmax", "winter_tmin")

# The month a season's bounds start in (CF time_bnds), or its midpoint falls in
SEASON_BY_START_MONTH = {12: "djf", 3: "mam", 6: "jja", 9: "son"}
SEASON_BY_MID_MONTH = {1: "djf", 4: "mam", 7: "jja", 10: "son"}


def haduk_file(variable: str, period: str) -> Path | None:
    """The newest ``<variable>_hadukgrid_uk_1km_<period>-30y_*.nc`` under data/manual."""
    folder = data_dir("manual") / SLUG
    files = sorted(folder.glob(f"{variable}_hadukgrid_uk_1km_{period}-30y_*.nc"))
    return files[-1] if files else None


def _dimension(ds: netCDF4.Dataset, axis: str) -> str:
    """The name of the projection coordinate for ``axis`` (``x`` or ``y``)."""
    wanted = f"projection_{axis}_coordinate"
    for name in ds.variables:
        if name == wanted or name.lower().startswith(wanted):
            return name
    raise ValueError(f"{ds.filepath()}: no {wanted} variable; has {list(ds.variables)}")


def season_labels(ds: netCDF4.Dataset) -> list[str]:
    """The season of each time step of a seasonal file (``djf``, ``mam``, ``jja``, ``son``),
    from the start month of the time bounds, else the month the time step falls in."""
    time = ds.variables["time"]
    calendar = getattr(time, "calendar", "standard")
    if "time_bnds" in ds.variables:
        starts = ds.variables["time_bnds"][:, 0]
        months = [d.month for d in netCDF4.num2date(starts, time.units, calendar)]
        table = SEASON_BY_START_MONTH
    else:
        months = [d.month for d in netCDF4.num2date(time[:], time.units, calendar)]
        table = SEASON_BY_MID_MONTH
    try:
        return [table[m] for m in months]
    except KeyError as exc:
        raise ValueError(f"{ds.filepath()}: cannot tell seasons from months {months}") from exc


def read_haduk(path: Path, variable: str, season: str | None = None) -> tuple[pl.DataFrame, str]:
    """One HadUK-Grid file → cells (x, y, value) with the value's unit; ``season`` picks
    the step of a seasonal file."""
    with netCDF4.Dataset(path) as ds:
        if variable not in ds.variables:
            raise ValueError(f"{path.name}: no variable {variable!r}; has {list(ds.variables)}")
        var = ds.variables[variable]
        xs = np.asarray(ds.variables[_dimension(ds, "x")][:], dtype=float)
        ys = np.asarray(ds.variables[_dimension(ds, "y")][:], dtype=float)
        units = str(getattr(var, "units", ""))
        dims = list(var.dimensions)
        if "time" in dims:
            if season is None:
                index = 0
            else:
                labels = season_labels(ds)
                if season not in labels:
                    raise ValueError(f"{path.name}: no {season} step among {labels}")
                index = labels.index(season)
            data = var[index]
        else:
            data = var[:]
        if dims[-2:] != [_dimension(ds, "y"), _dimension(ds, "x")]:
            data = np.ma.transpose(data)
        values = np.ma.filled(np.ma.asarray(data, dtype=float), np.nan)
    xx, yy = np.meshgrid(xs, ys)
    keep = np.isfinite(values)
    cells = pl.DataFrame(
        {"x": xx[keep].astype(np.int64), "y": yy[keep].astype(np.int64), "value": values[keep]}
    )
    return cells, units


def _to_display_units(cells: pl.DataFrame, column: str, units: str) -> pl.DataFrame:
    """Wind in knots → m/s; everything else is published in the unit the catalogue shows
    (hours, mm, days, °C)."""
    if column == "wind_ms" and units.lower() in ("knots", "knot", "kt", "kts"):
        return cells.with_columns(pl.col("value") * KNOT_MS)
    if column == "wind_ms" and units.lower() not in ("m s-1", "m/s", "m.s-1"):
        logger.warning(f"wind speed published in {units!r}; left as is")
    return cells


# A postcode on reclaimed land or a pier can sit in a cell the grid leaves blank (sea);
# its area takes the nearest land cell within this distance
NEAREST_CELL_M = 3000


def _fill_from_nearest_cells(
    cells: pl.DataFrame, homes: pl.DataFrame, sampled: pl.DataFrame, column: str
) -> pl.DataFrame:
    """Areas none of whose postcodes found a cell (docks, harbours: Leith, Southampton's
    waterfront) take the nearest land cell within NEAREST_CELL_M of each postcode."""
    from scipy.spatial import cKDTree

    missing = homes.filter(~pl.col("lsoa21cd").is_in(sampled["lsoa21cd"].implode()))
    if missing.is_empty():
        return sampled
    tree = cKDTree(cells.select("x", "y").to_numpy().astype(float))
    dist, idx = tree.query(
        missing.select("east1m", "north1m").to_numpy().astype(float),
        distance_upper_bound=NEAREST_CELL_M,
    )
    found = np.isfinite(dist)
    if not found.any():
        return sampled
    values = cells["value"].to_numpy()[idx[found]]
    filled = (
        missing.filter(pl.Series(found))
        .with_columns(pl.Series(column, values))
        .group_by("lsoa21cd")
        .agg(pl.col(column).mean(), pl.len().alias(f"_n_{column}"))
    )
    logger.info(f"{column}: {filled.height} areas took the nearest land cell")
    return pl.concat([sampled, filled], how="vertical_relaxed")


def climate_at_homes(homes: pl.DataFrame, files: dict[str, Path]) -> pl.DataFrame:
    """Per area: the mean over its postcodes of each variable's cell value."""
    out: pl.DataFrame | None = None
    for column, (variable, _period, season) in VARIABLES.items():
        path = files.get(column)
        if path is None:
            continue
        cells, units = read_haduk(path, variable, season)
        cells = _to_display_units(cells, column, units)
        sampled = sample_grid_at_points(cells, homes, "value").rename(
            {"value": column, "n_points": f"_n_{column}"}
        )
        sampled = _fill_from_nearest_cells(cells, homes, sampled, column)
        logger.info(f"{column}: {sampled.height:,} areas from {cells.height:,} cells ({units})")
        out = (
            sampled if out is None else out.join(sampled, on="lsoa21cd", how="full", coalesce=True)
        )
    if out is None:
        raise FileNotFoundError(f"No HadUK-Grid files under data/manual/{SLUG}/")
    counts = [c for c in out.columns if c.startswith("_n_")]
    out = out.with_columns(pl.max_horizontal(counts).cast(pl.Int32).alias("n_postcodes")).drop(
        counts
    )
    for column in VARIABLES:
        if column not in out.columns:
            out = out.with_columns(pl.lit(None, pl.Float64).alias(column))
    return out.select("lsoa21cd", *VARIABLES, "n_postcodes").sort("lsoa21cd")


def stage_climate() -> pl.LazyFrame:
    files = {
        column: path
        for column, (variable, period, _season) in VARIABLES.items()
        if (path := haduk_file(variable, period)) is not None
    }
    missing = [c for c in REQUIRED if c not in files]
    if missing:
        raise FileNotFoundError(
            f"HadUK-Grid files for {missing} are missing from data/manual/{SLUG}/ "
            "(see the registry's instructions)"
        )
    homes = residential_postcodes()
    df = climate_at_homes(homes, files)
    logger.info(
        f"{df.height:,} areas; median sunshine {df['sunshine_hours'].median():.0f} h, "
        f"wet days {df['rain_days'].median():.0f}"
    )
    return df.lazy()


HOT_DAY_COLUMNS = {
    "HSD_0120_median": "hot_days_baseline",
    "HSD_2_median": "hot_days_2c",
    "HSD_4_median": "hot_days_4c",
}


def stage_ukcp_hot_days() -> pl.LazyFrame:
    """UKCP18's local projections of hot summer days per MSOA, Intermediate Zone or (in
    Northern Ireland) district electoral area, copied down to every small area inside
    (``broadcast_msoa``): the 2001–2020 baseline and the counts at 2 and 4 °C of global
    warming (median of the ensemble)."""
    raw = pl.read_parquet(data_dir("raw") / "ukcp_hot_days" / "ukcp_hot_days.parquet")
    code = next(c for c in raw.columns if c.upper() == "CODE")
    values = raw.select(
        pl.col(code).cast(pl.Utf8).str.strip_chars().alias("code"),
        *[
            pl.col(src).cast(pl.Float64, strict=False).alias(dst)
            for src, dst in HOT_DAY_COLUMNS.items()
            if src in raw.columns
        ],
    ).unique("code")
    geo = pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet")
    key = (
        pl.when(pl.col("nation") == "N")
        .then(pl.col("dea_cd") if "dea_cd" in geo.columns else pl.lit(None, pl.Utf8))
        .otherwise(pl.col("msoa21cd"))
    )
    out = (
        geo.select("lsoa21cd", key.alias("code"))
        .join(values, on="code", how="left")
        .with_columns(pl.lit("broadcast_msoa").alias("quality"))
        .drop("code")
        .sort("lsoa21cd")
    )
    logger.info(
        f"{out.height:,} areas; hot summer days at 2 °C: median "
        f"{out['hot_days_2c'].median():.1f}, {out['hot_days_2c'].null_count():,} without a value"
    )
    return out.lazy()
