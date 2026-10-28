"""Protected land within about 2 km of homes (#84)."""

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.stage.protected_areas import protected_hectares


def test_hectares_count_overlaps_once_and_only_on_land():
    land = gpd.GeoSeries([box(0, 0, 20_000, 20_000)], crs=27700)
    sites = gpd.GeoSeries(
        [
            box(9_500, 9_500, 10_500, 10_500),  # 1 km² of SSSI…
            box(9_500, 9_500, 10_500, 10_500),  # …that is also an SAC: counted once
            box(20_000, 9_000, 25_000, 11_000),  # a marine SAC beyond the coast
        ],
        crs=27700,
    )
    homes = pl.DataFrame({
        "lsoa21cd": ["A", "B", "C"],
        "east1m": [10_000.0, 5_000.0, 19_900.0],
        "north1m": [10_000.0, 5_000.0, 10_000.0],
    })  # fmt: skip
    out = dict(
        protected_hectares(sites, land, homes, block_m=10_000)
        .select("lsoa21cd", "protected_ha")
        .iter_rows()
    )
    assert out["A"] == pytest.approx(100.0, rel=0.02)  # the whole square km is in reach
    assert out["B"] == pytest.approx(0.0)  # 7 km away
    assert out["C"] == pytest.approx(0.0)  # on the coast, next to the marine site only
