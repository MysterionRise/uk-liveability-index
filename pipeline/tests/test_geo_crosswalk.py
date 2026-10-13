"""Data Zone 2011 → 2022 weights from postcodes, and crosswalking values with them."""

import polars as pl
import pytest

from lix_pipeline.geo.crosswalk import build_dz11_dz22, crosswalk, crosswalk_problems, summary


def _inputs():
    # Zone A22 is zone A11 entire; B22 takes one postcode (80 people) from A11 and two
    # (20 people in all) from B11; C22's postcodes sit in an unpopulated output area
    nspl = pl.DataFrame(
        {
            "postcode_norm": ["P1", "P2", "P3", "P4", "P5", "P6", "P7"],
            "oa21cd": ["OA1", "OA1", "OA2", "OA3", "OA3", "OA4", "OA4"],
            "lsoa21cd": ["A22", "A22", "B22", "B22", "B22", "C22", "C22"],
            "live": [True, True, True, True, True, True, False],
        }
    ).lazy()
    simd = pl.DataFrame(
        {
            "postcode_norm": ["P1", "P2", "P3", "P4", "P5", "P6", "P7"],
            "dz11cd": ["A11", "A11", "A11", "B11", "B11", "C11", "D11"],
        }
    )
    oa = pl.DataFrame({"oa21cd": ["OA1", "OA2", "OA3", "OA4"], "population": [100, 80, 20, 0]})
    return simd, nspl, oa


def test_weights_follow_population_and_sum_to_one():
    weights = build_dz11_dz22(*_inputs())
    w = {(r["dz22cd"], r["dz11cd"]): r["weight"] for r in weights.iter_rows(named=True)}
    assert w[("A22", "A11")] == 1.0
    assert w[("B22", "A11")] == pytest.approx(0.8) and w[("B22", "B11")] == pytest.approx(0.2)
    assert w[("C22", "C11")] == 1.0  # unpopulated: one each, and the terminated P7 is ignored
    dominant = dict(zip(weights["dz22cd"], weights["dominant"]))
    assert dominant == {"A22": True, "B22": True, "C22": True}
    assert crosswalk_problems(weights, {"A22", "B22", "C22"}) == []
    assert crosswalk_problems(weights, {"A22", "B22", "C22", "Z22"}) == [
        "1 zones without weights, e.g. ['Z22']"
    ]


def test_crosswalk_means_and_sums():
    weights = build_dz11_dz22(*_inputs())
    values = pl.DataFrame(
        {"dz11cd": ["A11", "B11", "C11"], "rate": [10.0, 20.0, None], "n": [50, 10, 5]}
    )
    mean = crosswalk(values, weights, ["rate"], how="mean")
    assert dict(zip(mean["lsoa21cd"], mean["rate"])) == {
        "A22": 10.0,
        "B22": pytest.approx(12.0),
        "C22": None,
    }
    total = crosswalk(values, weights, ["n"], how="sum")
    assert dict(zip(total["lsoa21cd"], total["n"])) == {
        "A22": 50.0,
        "B22": pytest.approx(42.0),
        "C22": 5.0,
    }
    with pytest.raises(ValueError, match="mean or sum"):
        crosswalk(values, weights, ["n"], how="max")


def test_summary_reports_mixing():
    weights = build_dz11_dz22(*_inputs())
    ranks = pl.DataFrame({"dz11cd": ["A11", "B11", "C11"], "rank": [100.0, 200.0, 300.0]})
    s = summary(weights, ranks)
    assert s["zones"] == 3 and s["one_to_one"] == 2 and s["dominant"] == 3
    assert s["mixed_zones"] == 1
