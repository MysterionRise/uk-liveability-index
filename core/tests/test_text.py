"""Names as people read them: mojibake repair, stray characters, the validator's check."""

import polars as pl

from lix_core.text import clean_name, repair_mojibake, stray_name_problems


def test_repair_undoes_one_or_two_rounds_of_double_encoding():
    assert repair_mojibake("NestlÃ© UK LTD-York") == "Nestlé UK LTD-York"
    assert repair_mojibake("McDonaldÃ¢Â\x80Â\x99s Loughborough") == "McDonald’s Loughborough"
    twice = "St Leonard’s Court".encode().decode("latin-1").encode().decode("latin-1")
    assert repair_mojibake(twice) == "St Leonard’s Court"
    assert repair_mojibake("Hafan Y MÃ´r Holiday Park") == "Hafan Y Môr Holiday Park"


def test_repair_leaves_real_names_and_bytes_that_do_not_round_trip():
    assert repair_mojibake("Ynys Môn") == "Ynys Môn"
    assert repair_mojibake("São Paulo Café") == "São Paulo Café"
    assert repair_mojibake("Ã la carte") == "Ã la carte"  # "Ã " is not a UTF-8 lead pair
    assert repair_mojibake(None) is None


def test_clean_name_repairs_strips_and_trims():
    df = pl.DataFrame(
        {
            "name": [
                "  Two  spaces ",
                "Â\xa0Table!",
                "Zero​width﻿",
                "Control\x1fchar",
                "   ",
                None,
                "Plain name",
            ]
        }
    )
    out = df.with_columns(clean_name(pl.col("name")))["name"].to_list()
    assert out == ["Two spaces", "Table!", "Zerowidth", "Controlchar", None, None, "Plain name"]


def test_stray_name_problems_names_the_column_and_examples():
    df = pl.DataFrame(
        {
            "poi_id": ["a", "b", "c", "d"],
            "name": ["Fine", "Bad�", " lead", "NestlÃ©"],
            "name_cy": ["Iawn", "Iawn", "Iawn", "Iawn"],
            "detail": ["x", "y", "z", "w"],
        }
    )
    problems = stray_name_problems(df, "pois", "poi_id")
    assert len(problems) == 1
    assert problems[0].startswith("pois.name: 3 names with stray characters")
    assert "('b', 'Bad\\ufffd')" in problems[0] or "Bad" in problems[0]
    assert stray_name_problems(df.filter(pl.col("poi_id") == "a"), "pois", "poi_id") == []
