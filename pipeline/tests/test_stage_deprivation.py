"""The harmonised deprivation table: the Welsh rates behind WIMD's domain scores (#104)."""

import polars as pl
import pytest

from lix_pipeline.stage.deprivation import wimd_rates


def test_wimd_rates_pivot_the_indicator_table_to_proportions_per_lsoa():
    table = pl.DataFrame(
        {
            "Data values": ["36.3", "21", "50", "17.4", "14.2", "20.1", "15.5"],
            "Data description": ["Percentage"] * 7,
            "Area code": ["W01000003"] * 3 + ["W01000004"] * 2 + ["W06000001", "W92000004"],
            "Area name": ["Amlwch Port"] * 3 + ["Amlwch Rural"] * 2 + ["Isle of Anglesey", "Wales"],
            "Indicator": [
                "People in income deprivation",
                "Working age people in employment deprivation",
                "Children in income deprivation",
                "People in income deprivation",
                "Working age people in employment deprivation",
                "People in income deprivation",
                "People in income deprivation",
            ],
            "Notes": [None] * 7,
        }
    )
    out = wimd_rates(table).sort("lsoa21cd")
    assert out.columns == ["lsoa21cd", "income_score", "employment_score"]
    assert out["lsoa21cd"].to_list() == ["W01000003", "W01000004"]  # councils and Wales dropped
    assert out["income_score"].to_list() == pytest.approx([0.363, 0.174])
    assert out["employment_score"].to_list() == pytest.approx([0.21, 0.142])


def test_wimd_rates_refuse_a_table_without_the_two_indicators():
    table = pl.DataFrame(
        {
            "Data values": ["50"],
            "Area code": ["W01000003"],
            "Indicator": ["Children in income deprivation"],
        }
    )
    with pytest.raises(ValueError, match="lacks"):
        wimd_rates(table)
