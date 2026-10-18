"""Background air quality at homes: Defra's PCM 1km grids for the UK and the EEA's
interpolated 1 km maps of Europe for Ireland, as area means over the homes.

Defra's CSVs start with a few lines of metadata (pollutant, year, units) before the
``gridcode,x,y,<pollutant><year>`` header; x/y are cell centres in BNG metres. The EEA's
GeoTIFFs are on the European grid (EPSG:3035), so the homes are reprojected to read
them. Each area gets the mean over its homes of the cell they sit in, which
approximates a population-weighted average.
"""

import re

import numpy as np
import polars as pl

from lix_core.codes import active_nations, nation_of
from lix_core.geo import working_crs
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.access import residential_postcodes
from lix_pipeline.geo.grids import sample_grid_at_points

logger = setup_logging("stage.pcm")

# The EEA raster standing in for each Defra grid in Ireland, and its year
EEA_FILES = {
    "defra_pcm_no2": "eea_aq_no2",
    "defra_pcm_pm25": "eea_aq_pm25",
    "defra_pcm_pm10": "eea_aq_pm10",
}
EEA_YEAR = 2023


def read_pcm(path) -> tuple[pl.DataFrame, int]:
    """Return (cells with x, y, value) and the data year."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_at = next(i for i, line in enumerate(lines) if line.startswith("gridcode,"))
    value_col = lines[header_at].split(",")[3]
    # "no22024" or "pm252024g": the year is the last four digits, not the first ("2202")
    year = int(re.search(r"(\d{4})g?$", value_col).group(1))
    df = pl.read_csv(path, skip_rows=header_at, infer_schema_length=0).select(
        pl.col("x").cast(pl.Int64),
        pl.col("y").cast(pl.Int64),
        # Cells outside the modelled area are "MISSING"
        pl.col(value_col).cast(pl.Float64, strict=False).alias("value"),
    )
    return df.filter(pl.col("value").is_not_null()), year


def sample_raster_at_homes(path, homes: pl.DataFrame, by: str = "lsoa21cd") -> pl.DataFrame:
    """Mean per ``by`` group of a GeoTIFF's first band at each home (``east1m``,
    ``north1m`` in the working CRS, reprojected to the raster's); no-data and negative
    cells are skipped. Returns ``by``, ``value`` and ``n_points``."""
    import rasterio
    from pyproj import CRS, Transformer
    from rasterio.transform import rowcol

    with rasterio.open(path) as src:
        # The raster's CRS from its own WKT: the EEA's "ETRS89-extended / LAEA Europe"
        # does not resolve to an EPSG code with confidence
        to_raster = Transformer.from_crs(
            CRS.from_epsg(working_crs()), CRS.from_wkt(src.crs.to_wkt()), always_xy=True
        )
        x, y = to_raster.transform(homes["east1m"].to_numpy(), homes["north1m"].to_numpy())
        rows, cols = rowcol(src.transform, x, y)
        rows, cols = np.asarray(rows), np.asarray(cols)
        inside = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
        band = src.read(1).astype(float)
        if src.nodata is not None:
            band[band == src.nodata] = np.nan
    values = np.full(len(x), np.nan)
    values[inside] = band[rows[inside], cols[inside]]
    values[values < 0] = np.nan
    return (
        homes.select(by)
        # NaN is not null to Polars: make the skipped cells nulls so they drop out
        .with_columns(pl.Series("value", values).fill_nan(None))
        .filter(pl.col("value").is_not_null())
        .group_by(by)
        .agg(pl.col("value").mean(), pl.len().alias("n_points"))
        .sort(by)
    )  # fmt: skip


def stage_pcm(slug: str) -> pl.LazyFrame:
    """Area population-weighted mean of one pollutant (µg/m³) across the active nations."""
    homes = residential_postcodes()
    frames = []
    uk = [n for n in active_nations() if n != "I"]
    if uk:
        cells, year = read_pcm(data_dir("raw") / slug / f"{slug}.csv")
        out = sample_grid_at_points(
            cells, homes.filter(nation_of("lsoa21cd").is_in(uk)), "value"
        ).with_columns(pl.lit(year).alias("year"))
        logger.info(f"[{slug}] {year}: {out.height:,} areas from {cells.height:,} Defra cells")
        frames.append(out)
    if "I" in active_nations():
        eea = EEA_FILES[slug]
        out = sample_raster_at_homes(
            data_dir("raw") / eea / f"{eea}.tif", homes.filter(nation_of("lsoa21cd") == "I")
        ).with_columns(pl.lit(EEA_YEAR).alias("year"))
        logger.info(
            f"[{slug}] {EEA_YEAR}: {out.height:,} Irish areas from the EEA map ({eea}); "
            f"median {out['value'].median():.1f} µg/m³"
        )
        frames.append(out)
    return pl.concat(frames, how="diagonal_relaxed").sort("lsoa21cd").lazy()
