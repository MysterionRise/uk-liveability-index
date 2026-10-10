"""Tests for indicator builders, on hand-made staged tables."""

from datetime import date

import polars as pl
import pytest

from lix_core.config import IndicatorSpec
from lix_pipeline.geo.access import poi_access
from lix_pipeline.indicators import build_indicator
from lix_pipeline.indicators.common import count_within, impute_by_proxy, nearest, share
from lix_pipeline.indicators.community import claimant_rate
from lix_pipeline.indicators.environment import flood_risk, optional_column, percent
from lix_pipeline.indicators.health import patients_per_gp
from lix_pipeline.indicators.housing import PRIOR_SALES, council_tax, median_price
from lix_pipeline.indicators.pubs import match_pubs, normalise_name
from lix_pipeline.indicators.safety import GREATER_MANCHESTER, crime_rate


class FakeContext:
    """Stands in for indicators.Context with in-memory staged tables."""

    def __init__(self, origins: pl.DataFrame | None = None, **tables: pl.DataFrame):
        self.tables = tables
        self.origins = origins

    @property
    def geo(self) -> pl.DataFrame:
        return self.tables["geo_lsoa"]

    def staged(self, slug: str) -> pl.DataFrame:
        return self.tables[slug]

    def access(self, pois: pl.DataFrame, radius_m: float, **kwargs) -> pl.DataFrame:
        return poi_access(self.origins, pois, radius_m=radius_m, **kwargs)


GEO = pl.DataFrame({
    "lsoa21cd": ["E01000001", "E01000002", "E01000003", "E01000004"],
    "msoa21cd": ["E02000001", "E02000001", "E02000002", "E02000002"],
    "population": [1000, 2000, 1500, 1000],
    "pfa_cd": ["E23000034", "E23000034", GREATER_MANCHESTER, "E23000034"],
})  # fmt: skip


class TestCrimeRate:
    def _ctx(self):
        police = pl.DataFrame({
            "lsoa21cd": ["E01000001", "E01000002", "E01000002", "E01000003"],
            "crime_type": ["Burglary", "Burglary", "Burglary", "Burglary"],
            "force": ["Kent Police", "Kent Police", "Essex Police", "Lancashire Constabulary"],
            "n": [36, 12, 6, 3],
            "force_months": [36, 36, 18, 36],
        })  # fmt: skip
        iod = pl.DataFrame({
            "lsoa21cd": GEO["lsoa21cd"], "crime_score": [0.5, -1.0, 0.2, -1.5],
        })  # fmt: skip
        return FakeContext(geo_lsoa=GEO, police_crime=police, iod_2025=iod)

    def test_annualised_per_force_months(self):
        df = crime_rate(self._ctx(), types=["Burglary"]).sort("lsoa21cd")
        rows = {r["lsoa21cd"]: r for r in df.to_dicts()}
        # 36 crimes over 36 months = 12 a year among 1,000 people
        assert rows["E01000001"]["value"] == pytest.approx(12.0)
        # 12/36×12 + 6/18×12 = 4 + 4 = 8 a year among 2,000
        assert rows["E01000002"]["value"] == pytest.approx(4.0)
        # No recorded crime in a covered area is a true zero
        assert rows["E01000004"]["value"] == 0.0

    def test_greater_manchester_is_imputed_not_counted(self):
        df = crime_rate(self._ctx(), types=["Burglary"])
        gm = df.filter(pl.col("lsoa21cd") == "E01000003").row(0, named=True)
        assert gm["quality"] == "imputed"
        # Stray border records (3 crimes → 2 a year per 1,000) are ignored; the value
        # comes from the proxy fit, which lies within the training range
        assert 0.0 <= gm["value"] <= 12.0
        assert gm["value"] != pytest.approx(2.0)


def test_impute_by_proxy_is_monotone():
    df = pl.DataFrame({
        "v": [1.0, 2.0, 3.0, 4.0, 5.0, None, None],
        "proxy": [1.0, 2.0, 3.0, 4.0, 5.0, 1.5, 4.5],
        "gap": [False] * 5 + [True, True],
    })  # fmt: skip
    filled = impute_by_proxy(df, "v", "proxy", pl.col("gap"), bins=5, log=False)
    low, high = filled.to_list()[5:]
    assert 1.0 <= low < high <= 5.0


class TestMedianPrice:
    def test_shrinks_small_samples_towards_msoa(self):
        pp = pl.DataFrame({
            "lsoa21cd": ["E01000001", "E01000002", "E01000003"],
            "median_price_12m": [200_000.0, 400_000.0, None],
            "transaction_count_12m": [20, 1, 0],
            "median_price_5y": [190_000.0, 380_000.0, 250_000.0],
        })  # fmt: skip
        df = median_price(FakeContext(geo_lsoa=GEO, price_paid=pp)).sort("lsoa21cd")
        rows = {r["lsoa21cd"]: r for r in df.to_dicts()}
        msoa1 = 300_000  # median of 200k and 400k
        assert rows["E01000001"]["value"] == pytest.approx((20 * 200_000 + 5 * msoa1) / 25)
        assert rows["E01000002"]["value"] == pytest.approx((1 * 400_000 + 5 * msoa1) / 6)
        assert rows["E01000002"]["quality"] == "low_n"
        # No 12-month sales and no MSOA median: fall back to the 5-year median
        assert rows["E01000003"]["value"] == 250_000
        # An LSOA missing from Price Paid entirely stays null
        assert rows["E01000004"]["value"] is None
        assert PRIOR_SALES == 5


class TestPatientsPerGp:
    def test_weights_practices_by_where_patients_live(self):
        reg = pl.DataFrame({
            "practice_code": ["P1", "P1", "P2", "P3"],
            "lsoa21cd": ["E01000001", "E01000002", "E01000001", "E01000002"],
            "patients": [1000, 1000, 3000, 500],
        })  # fmt: skip
        wf = pl.DataFrame({
            "practice_code": ["P1", "P2", "P3"],
            "qualified_gp_fte": [1.0, 2.0, 0.2],  # P3 is below the 1 FTE minimum
        })  # fmt: skip
        df = patients_per_gp(FakeContext(gp_registrations=reg, gp_workforce=wf))
        rows = {r["lsoa21cd"]: r["value"] for r in df.to_dicts()}
        # P1: 2,000 patients / 1 FTE; P2: 3,000 / 2 FTE = 1,500
        assert rows["E01000001"] == pytest.approx((1000 * 2000 + 3000 * 1500) / 4000)
        # E01000002: P1 (1,000 patients) usable, P3 (500) not → 67% coverage, use P1 only
        assert rows["E01000002"] == pytest.approx(2000)


class TestPubMatching:
    def test_normalise_name(self):
        out = pl.DataFrame({"n": ["The Fox & Hounds", "Ye Olde Cheshire Cheese Inn"]}).select(
            normalise_name(pl.col("n"))
        )
        # "&" and "and" both vanish, so "Fox & Hounds" equals "Fox and Hounds"
        assert out["n"].to_list() == ["fox hounds", "cheshire cheese"]

    def test_fhrs_id_then_spatial_name_match(self):
        osm = pl.DataFrame({
            "osm_id": [1, 2, 3, 4],
            "name": ["The Red Lion", "Fox and Hounds", "The Crown", "Kings Head"],
            "x": [0.0, 1000.0, 2000.0, 3000.0],
            "y": [0.0, 0.0, 0.0, 0.0],
            "fhrs_id": ["111", None, None, None],
        })  # fmt: skip
        fsa = pl.DataFrame({
            "fhrs_id": [111, 222, 333, 444],
            "name": ["Red Lion (registered as restaurant)", "FOX & HOUNDS PH", "The Crown",
                     "Totally Different Ltd"],
            "x": [5000.0, 1030.0, 2500.0, 3010.0],  # The Crown is 500m away: too far
            "y": [0.0, 0.0, 0.0, 0.0],
            "rating": [5, 4, 5, 5],
            "rating_date": [date(2025, 1, 1)] * 4,
        })  # fmt: skip
        out = match_pubs(osm, fsa).sort("osm_id")
        assert out["match"].to_list() == ["fhrs_id", "spatial", None, None]
        assert out["fsa_id"].to_list() == [111, 222, None, None]


def test_build_indicator_fills_every_lsoa_and_flags_missing():
    spec = IndicatorSpec(
        id="x", theme="housing", label="X", description="d", unit="u",
        direction="higher_better", sources=["price_paid"], builder="common:column",
        params={"slug": "t", "column": "c"},
    )  # fmt: skip
    table = pl.DataFrame({"lsoa21cd": ["E01000001", "E01000002"], "c": [1.0, float("nan")]})
    ctx = FakeContext(geo_lsoa=GEO, t=table)
    out = build_indicator(spec, ctx).sort("lsoa21cd")
    assert out.height == GEO.height
    assert out["value"].to_list() == [1.0, None, None, None]
    assert out["quality"].to_list() == ["ok", "missing", "missing", "missing"]


# Two homes in E01000001 and one in E01000002, 10km apart
ORIGINS = pl.DataFrame({
    "postcode": ["A", "B", "C"],
    "lsoa21cd": ["E01000001", "E01000001", "E01000002"],
    "east1m": [500_000, 500_200, 510_000],
    "north1m": [200_000, 200_000, 200_000],
})  # fmt: skip


class TestPointBuilders:
    def _ctx(self):
        collisions = pl.DataFrame({
            "severity": ["fatal", "serious", "slight", "serious"],
            "x": [500_100.0, 500_100.0, 500_100.0, 510_000.0],
            "y": [200_000.0, 200_000.0, 200_000.0, 205_000.0],
        })  # fmt: skip
        return FakeContext(origins=ORIGINS, stats19=collisions)

    def test_count_within_filters_and_annualises(self):
        df = count_within(
            self._ctx(), "stats19", radius_m=500, column="severity",
            values=["fatal", "serious"], per_years=5,
        ).sort("lsoa21cd")  # fmt: skip
        # Both homes in E01000001 have 2 serious-or-fatal collisions within 500m over 5 years
        assert df["value"].to_list() == pytest.approx([0.4, 0.0])

    def test_nearest_of_filtered_points(self):
        df = nearest(self._ctx(), "stats19", column="severity", values=["serious"]).sort("lsoa21cd")
        assert df["value"].to_list() == pytest.approx([100.0, 5000.0])


def test_share_of_staged_columns():
    stock = pl.DataFrame(
        {"lsoa21cd": ["E01000001"], "band_a": [30], "band_b": [20], "dwellings": [200]}
    )
    ctx = FakeContext(voa_ctsop=stock)
    df = share(ctx, "voa_ctsop", numerator=["band_a", "band_b"], denominator="dwellings")
    assert df["value"].to_list() == [25.0]


def test_flood_risk_share_of_homes():
    stock = pl.DataFrame(
        {"lsoa21cd": ["E01000001", "E01000002", "E01000003"], "dwellings": [100, 50, 10]}
    )
    at_risk = pl.DataFrame({
        "lsoa21cd": ["E01000001", "E01000003"],
        "res_high": [5, 20], "res_medium": [15, 0], "res_low": [30, 0],
    })  # fmt: skip
    ctx = FakeContext(voa_ctsop=stock, flood=at_risk)
    df = flood_risk(ctx, bands=["high", "medium"]).sort("lsoa21cd")
    # 20 of 100; none listed → 0; more at risk than dwellings counted → capped at 100
    assert df["value"].to_list() == [20.0, 0.0, 100.0]
    assert df["quality"].to_list() == ["ok", "ok", "ok"]


def test_flood_risk_any_source_and_estimates_are_flagged():
    stock = pl.DataFrame({"lsoa21cd": ["E01000001", "W01000001"], "dwellings": [100, 50]})
    flood = pl.DataFrame({
        "lsoa21cd": ["E01000001", "W01000001"],
        "res_high": [5, 0], "res_medium": [5, 0],
        "any_high": [10, 20], "any_medium": [20, 5],
        "estimated": [False, True],
    })  # fmt: skip
    ctx = FakeContext(voa_ctsop=stock, flood=flood)
    rivers = flood_risk(ctx, bands=["high", "medium"], prefix="res").sort("lsoa21cd")
    any_ = flood_risk(ctx, bands=["high", "medium"], prefix="any").sort("lsoa21cd")
    assert rivers["value"].to_list() == [10.0, 0.0]
    assert any_["value"].to_list() == [30.0, 50.0]
    assert any_["quality"].to_list() == ["ok", "imputed"]


def test_claimant_rate_per_working_age_resident():
    ages = pl.DataFrame({
        "lsoa21cd": ["E01000001"], "aged_15_to_19_years": [100], "aged_20_to_24_years": [400],
        "aged_60_to_64_years": [420], "aged_65_to_69_years": [500],
    })  # fmt: skip
    claims = pl.DataFrame({"lsoa21cd": ["E01000001"], "claimants": [45]})
    ctx = FakeContext(census_ts007a=ages, claimant_count=claims)
    # 16–64 ≈ 0.8 × 100 + 400 + 420 = 900
    assert claimant_rate(ctx)["value"].to_list() == pytest.approx([5.0])


def test_council_tax_broadcast_from_billing_authority():
    geo = pl.DataFrame(
        {"lsoa21cd": ["E01000001", "E01000002"], "lad_cd": ["E06000001", "E06000002"]}
    )
    tax = pl.DataFrame({"lad_cd": ["E06000001"], "band_d": [2100.0]})
    df = council_tax(FakeContext(geo_lsoa=geo, council_tax=tax)).sort("lsoa21cd")
    assert df["value"].to_list() == [2100.0, None]
    assert set(df["quality"]) == {"broadcast_lad"}


def test_overture_adds_only_pubs_osm_lacks():
    from lix_pipeline.indicators.pubs import overture_additions

    osm = pl.DataFrame({"name": ["The Red Lion"], "x": [500_000.0], "y": [200_000.0]})
    overture = pl.DataFrame({
        "id": ["a", "b", "c", "d"],
        "name": ["Red Lion", "Kings Head", "Kings Head", "Swan"],
        "x": [500_030.0, 500_010.0, 500_200.0, 500_300.0],
        "y": [200_000.0] * 4,
        "confidence": [0.95, 0.95, 0.95, 0.5],
    })  # fmt: skip
    # a: same name 30m away; b: 10m away (same building, other name); d: low confidence
    assert overture_additions(osm, overture)["id"].to_list() == ["c"]


def test_gp_quality_weights_ratings_by_where_patients_live():
    from lix_pipeline.indicators.health import gp_quality

    reg = pl.DataFrame({
        "lsoa21cd": ["E01000001", "E01000001", "E01000002", "E01000002"],
        "practice_code": ["A1", "B2", "B2", "C3"],
        "patients": [300, 100, 100, 300],
    })  # fmt: skip
    cqc = pl.DataFrame({
        "ods_code": ["A1", "B2", "C3"],
        "category": ["GP Practices"] * 3,
        "rating_score": [1.0, 0.4, None],  # C3 isn't rated yet
    })  # fmt: skip
    df = gp_quality(FakeContext(gp_registrations=reg, cqc_locations=cqc)).sort("lsoa21cd")
    first, second = df["value"].to_list()
    assert first == pytest.approx((300 * 1.0 + 100 * 0.4) / 400)
    assert second is None  # only a quarter of its patients are at a rated practice


def test_percent_scales_a_share():
    ctx = FakeContext(noise=pl.DataFrame({"lsoa21cd": ["E01000001"], "share_any_55": [0.375]}))
    assert percent(ctx, "noise", "share_any_55")["value"].to_list() == [37.5]


def test_optional_column_is_empty_without_the_staged_table(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    ctx = FakeContext(night_lights=pl.DataFrame({"lsoa21cd": ["E01000001"], "radiance": [3.5]}))
    assert optional_column(ctx, "night_lights", "radiance").height == 0
    (tmp_path / "staged").mkdir()
    ctx.tables["night_lights"].write_parquet(tmp_path / "staged" / "night_lights.parquet")
    assert optional_column(ctx, "night_lights", "radiance")["value"].to_list() == [3.5]
