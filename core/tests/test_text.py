"""Names as people read them: mojibake repair, stray characters, the validator's check."""

import polars as pl

from lix_core.text import clean_name, repair_mojibake, stray_name_problems


def mangle(text: str, codec: str, times: int = 1) -> str:
    for _ in range(times):
        text = text.encode("utf-8").decode(codec)
    return text


def test_repair_undoes_latin1_and_windows1252_mangling_however_many_rounds():
    assert repair_mojibake("NestlÃ© UK LTD-York") == "Nestlé UK LTD-York"
    assert repair_mojibake("Hafan Y MÃ´r Holiday Park") == "Hafan Y Môr Holiday Park"
    assert repair_mojibake("306 St Jamesâ€™s Road") == "306 St James’s Road"
    assert (
        repair_mojibake(mangle("McDonald’s Loughborough", "latin-1")) == "McDonald’s Loughborough"
    )
    for codec in ("latin-1", "cp1252"):
        for times in (1, 2, 3, 4):
            assert (
                repair_mojibake(mangle("St Leonard’s Court", codec, times)) == "St Leonard’s Court"
            )
    # Welsh circumflexes survive, as does a dash outside the mangled run
    assert repair_mojibake(mangle("Tŷ Gŵyr", "cp1252")) == "Tŷ Gŵyr"
    assert repair_mojibake("NestlÃ© – York") == "Nestlé – York"


def test_repair_leaves_real_names_and_runs_that_do_not_read_back():
    assert repair_mojibake("Ynys Môn") == "Ynys Môn"
    assert repair_mojibake("São Paulo Café") == "São Paulo Café"
    assert repair_mojibake("Ã la carte") == "Ã la carte"  # "Ã " is not a UTF-8 lead pair
    # An accented capital before a no-break space happens to be valid UTF-8 for an IPA
    # letter; that is not Latin text, so it is left alone
    assert repair_mojibake("CAFÉ\xa0BAR") == "CAFÉ\xa0BAR"
    assert repair_mojibake("Ó\xa0Briain") == "Ó\xa0Briain"
    assert repair_mojibake(None) is None


def test_clean_name_repairs_strips_and_trims():
    df = pl.DataFrame(
        {
            "name": [
                "  Two  spaces ",
                "Â\xa0Table!",
                "Zero​width﻿",
                "Control\x1fchar",
                "Play 2 Learn\nDay nursery",
                "Rack and Bone\tSmokehouse",
                "   ",
                None,
                "Plain name",
                "306 St Jamesâ€™s Road",
            ]
        }
    )
    out = df.with_columns(clean_name(pl.col("name")))["name"].to_list()
    assert out == [
        "Two spaces",
        "Table!",
        "Zerowidth",
        "Controlchar",
        "Play 2 Learn Day nursery",
        "Rack and Bone Smokehouse",
        None,
        None,
        "Plain name",
        "306 St James’s Road",
    ]


def test_an_unrepairable_name_keeps_its_c1_bytes_for_the_validator():
    # A run that is not valid UTF-8 (an overlong three-byte sequence) stays as it came,
    # C1 controls included, so the validator sees it rather than a silently cut name
    df = pl.DataFrame({"poi_id": ["a"], "name": ["à\x80\x80x"]})
    cleaned = df.with_columns(clean_name(pl.col("name")))
    assert stray_name_problems(cleaned, "pois", "poi_id")


def test_real_names_with_curly_quotes_and_dashes_are_not_mojibake():
    for name in ("Tá Sé’s", "Kitty Ó Sé’s", "Dún Laoghaire–Rathdown", "Café “Môr”"):
        assert repair_mojibake(name) == name
    df = pl.DataFrame({"place_id": ["a", "b"], "name": ["Tá Sé’s", "Dún Laoghaire–Rathdown"]})
    assert stray_name_problems(df, "places", "place_id") == []


def test_stray_name_problems_names_the_column_and_examples():
    df = pl.DataFrame(
        {
            "poi_id": ["a", "b", "c", "d", "e"],
            "name": ["Fine", "Bad�", " lead", "NestlÃ©", "Jamesâ€™s"],
            "name_cy": ["Iawn", "Iawn", "Iawn", "Iawn", "Iawn"],
            "detail": ["x", "y", "z", "w", "v"],
        }
    )
    problems = stray_name_problems(df, "pois", "poi_id")
    assert len(problems) == 1
    assert problems[0].startswith("pois.name: 4 names with stray characters")
    assert stray_name_problems(df.filter(pl.col("poi_id") == "a"), "pois", "poi_id") == []
