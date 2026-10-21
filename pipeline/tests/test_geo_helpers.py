"""Tests for the join, grid-sampling and accessibility helpers, on synthetic geography."""

import math

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.geo.access import poi_access
from lix_pipeline.geo.grids import sample_grid_at_points
from lix_pipeline.geo.joins import broadcast, oa_to_lsoa, points_to_lsoa


@pytest.fixture
def two_lsoas() -> gpd.GeoDataFrame:
    """Two 1km squares side by side in BNG: A spans x 0–1000, B spans x 1000–2000."""
    return gpd.GeoDataFrame(
        {"lsoa21cd": ["E01000001", "E01000002"]},
        geometry=[box(0, 0, 1000, 1000), box(1000, 0, 2000, 1000)],
        crs=27700,
    )


class TestPointsToLsoa:
    def test_assigns_points(self, two_lsoas):
        df = pl.DataFrame({"id": [1, 2, 3], "x": [500.0, 1500.0, 5000.0], "y": [500.0] * 3})
        out = points_to_lsoa(df, polygons=two_lsoas)
        assert out["lsoa21cd"].to_list() == ["E01000001", "E01000002", None]
        assert out["id"].to_list() == [1, 2, 3]

    def test_border_point_counted_once(self, two_lsoas):
        df = pl.DataFrame({"x": [1000.0], "y": [500.0]})
        out = points_to_lsoa(df, polygons=two_lsoas)
        assert out.height == 1
        assert out["lsoa21cd"][0] in {"E01000001", "E01000002"}

    def test_null_coordinates(self, two_lsoas):
        df = pl.DataFrame({"x": [None, 500.0], "y": [None, 500.0]})
        out = points_to_lsoa(df, polygons=two_lsoas)
        assert out["lsoa21cd"].to_list() == [None, "E01000001"]

    def test_lonlat_input(self, two_lsoas):
        # Reproject the centre of square A to WGS84 and back through the helper
        centre = gpd.GeoSeries.from_xy([500.0], [500.0], crs=27700).to_crs(4326)
        df = pl.DataFrame({"lon": [centre.x[0]], "lat": [centre.y[0]]})
        out = points_to_lsoa(df, x="lon", y="lat", crs=4326, polygons=two_lsoas)
        assert out["lsoa21cd"][0] == "E01000001"


LOOKUP = pl.DataFrame({
    "oa21cd": ["O1", "O2", "O3"],
    "lsoa21cd": ["E01000001", "E01000001", "E01000002"],
})  # fmt: skip


class TestOaToLsoa:
    def test_sum(self):
        df = pl.DataFrame({"oa21cd": ["O1", "O2", "O3"], "premises": [10, 30, 5]})
        out = oa_to_lsoa(df, ["premises"], lookup=LOOKUP)
        assert out.to_dicts() == [
            {"lsoa21cd": "E01000001", "premises": 40},
            {"lsoa21cd": "E01000002", "premises": 5},
        ]

    def test_weighted_mean_ignores_missing_values(self):
        df = pl.DataFrame({
            "oa21cd": ["O1", "O2", "O3"],
            "gigabit_pct": [100.0, 0.0, None],
            "premises": [30, 10, 5],
        })  # fmt: skip
        out = oa_to_lsoa(df, ["gigabit_pct"], how="weighted_mean", weight="premises", lookup=LOOKUP)
        rows = {r["lsoa21cd"]: r["gigabit_pct"] for r in out.to_dicts()}
        assert rows["E01000001"] == pytest.approx(75.0)
        assert rows["E01000002"] is None

    def test_weighted_mean_needs_weight(self):
        with pytest.raises(ValueError, match="weight"):
            oa_to_lsoa(pl.DataFrame({"oa21cd": ["O1"], "v": [1]}), ["v"], how="weighted_mean",
                       lookup=LOOKUP)  # fmt: skip


GEO = pl.DataFrame({
    "lsoa21cd": ["E01000001", "E01000002", "E01000003"],
    "msoa21cd": ["E02000001", "E02000001", "E02000002"],
    "lad_cd": ["E08000039", "E08000039", "E06000001"],
    "lad24cd": ["E08000019", "E08000019", "E06000001"],
    "lad22cd": ["E08000019", "E08000019", "E06000001"],
})  # fmt: skip


class TestBroadcast:
    def test_msoa(self):
        df = pl.DataFrame({"msoa": ["E02000001", "E02000002"], "income": [30_000, 40_000]})
        out = broadcast(df, ["income"], level="msoa", code_col="msoa", geo=GEO)
        assert out["income"].to_list() == [30_000, 30_000, 40_000]
        assert out["quality"].unique().to_list() == ["broadcast_msoa"]

    def test_lad_matches_older_code_vintage(self):
        # Sheffield's pre-2026 code: the data predates the boundary change
        df = pl.DataFrame({"code": ["E08000019", "E06000001"], "band_d": [2200.0, 2100.0]})
        out = broadcast(df, ["band_d"], level="lad", code_col="code", geo=GEO)
        assert out["band_d"].to_list() == [2200.0, 2200.0, 2100.0]
        assert out["quality"][0] == "broadcast_lad"

    def test_lad_current_codes(self):
        df = pl.DataFrame({"code": ["E08000039"], "v": [1]})
        out = broadcast(df, ["v"], level="lad", code_col="code", geo=GEO)
        assert out["v"].to_list() == [1, 1, None]


class TestSampleGrid:
    def test_population_weighted_mean(self):
        # Cells are located by their centres (500, 1500, ...)
        grid = pl.DataFrame({"x": [500, 1500], "y": [500, 500], "no2": [10.0, 30.0]})
        points = pl.DataFrame({
            "lsoa21cd": ["A", "A", "A", "B"],
            "east1m": [100, 900, 1200, 5000],
            "north1m": [100, 100, 100, 100],
        })  # fmt: skip
        out = sample_grid_at_points(grid, points, "no2")
        # A: two postcodes in the 10 cell, one in the 30 cell; B has no cell
        assert out.to_dicts() == [{"lsoa21cd": "A", "no2": pytest.approx(50 / 3), "n_points": 3}]

    def test_cell_edges(self):
        grid = pl.DataFrame({"x": [500, 1500], "y": [500, 500], "v": [1.0, 2.0]})
        points = pl.DataFrame({"lsoa21cd": ["A", "B"], "east1m": [999, 1000], "north1m": [0, 0]})
        out = sample_grid_at_points(grid, points, "v")
        assert dict(zip(out["lsoa21cd"], out["v"])) == {"A": 1.0, "B": 2.0}


class TestPoiAccess:
    ORIGINS = pl.DataFrame({
        "lsoa21cd": ["A", "A", "B"],
        "east1m": [0, 100, 10_000],
        "north1m": [0, 0, 0],
    })  # fmt: skip

    def test_nearest_count_and_share(self):
        pois = pl.DataFrame({"x": [300.0, 600.0], "y": [0.0, 0.0]})
        out = poi_access(self.ORIGINS, pois, radius_m=550)
        a, b = out.to_dicts()
        # Origin 0: nearest 300, one POI within 550 · origin 100: nearest 200, two within
        assert a["nearest_m"] == pytest.approx(250)
        assert a["count"] == pytest.approx(1.5)
        assert a["share_with_any"] == 1.0
        assert a["n_origins"] == 2
        assert b["nearest_m"] == pytest.approx(9400)
        assert b["count"] == 0 and b["score"] == 0 and b["share_with_any"] == 0

    def test_decay_and_cap(self):
        origins = pl.DataFrame({"lsoa21cd": ["A"], "east1m": [0], "north1m": [0]})
        pois = pl.DataFrame({"x": [0.0, 500.0], "y": [0.0, 0.0]})
        out = poi_access(origins, pois, radius_m=1000, sigma_m=500, cap=10)
        raw = 1 + math.exp(-0.5)  # weight 1 at 0m, exp(-d²/2σ²) at 500m
        assert out["score"][0] == pytest.approx(math.log1p(raw) / math.log1p(10))

    def test_score_capped_at_one(self):
        origins = pl.DataFrame({"lsoa21cd": ["A"], "east1m": [0], "north1m": [0]})
        pois = pl.DataFrame({"x": [0.0] * 50, "y": [0.0] * 50})
        assert poi_access(origins, pois, radius_m=100, cap=10)["score"][0] == 1.0

    def test_weights_scale_contribution(self):
        origins = pl.DataFrame({"lsoa21cd": ["A"], "east1m": [0], "north1m": [0]})
        pois = pl.DataFrame({"x": [0.0], "y": [0.0], "quality": [3.0]})
        out = poi_access(origins, pois, radius_m=100, weight="quality", cap=10)
        assert out["score"][0] == pytest.approx(math.log1p(3) / math.log1p(10))

    def test_no_pois(self):
        pois = pl.DataFrame({"x": [], "y": []}, schema={"x": pl.Float64, "y": pl.Float64})
        out = poi_access(self.ORIGINS, pois, radius_m=500)
        assert out["nearest_m"].to_list() == [None, None]
        assert out["count"].to_list() == [0.0, 0.0]

    def test_nearest_beyond_max_counts_as_the_cap(self):
        # A home with nothing within the search cap is at least the cap away (#148)
        pois = pl.DataFrame({"x": [100_000.0], "y": [0.0]})
        out = poi_access(self.ORIGINS, pois, radius_m=500, max_nearest_m=50_000)
        assert out["nearest_m"].to_list() == [50_000.0, 50_000.0]

    def test_no_pois_at_all_leaves_nearest_unknown(self):
        out = poi_access(self.ORIGINS, pl.DataFrame({"x": [], "y": []}), radius_m=500)
        assert out["nearest_m"].to_list() == [None, None]
