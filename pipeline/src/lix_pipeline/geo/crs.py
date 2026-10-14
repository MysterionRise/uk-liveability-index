"""Polars wrappers over :mod:`lix_core.geo`: the working CRS (British National Grid for
the UK) to and from WGS84. The names keep the grid they were written for; the maths
follows ``country.working_crs`` in config/nations.yaml."""

import polars as pl

from lix_core.geo import lonlat_to_working, working_to_lonlat


def bng_to_lonlat(x: pl.Series, y: pl.Series) -> tuple[pl.Series, pl.Series]:
    lon, lat = working_to_lonlat(x.to_numpy(), y.to_numpy())
    return pl.Series("lon", lon), pl.Series("lat", lat)


def lonlat_to_bng(lon: pl.Series, lat: pl.Series) -> tuple[pl.Series, pl.Series]:
    x, y = lonlat_to_working(lon.to_numpy(), lat.to_numpy())
    return pl.Series("x", x), pl.Series("y", y)
