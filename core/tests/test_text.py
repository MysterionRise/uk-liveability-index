"""Names as people read them: mojibake repair, stray characters, the validator's check."""

import polars as pl
import pytest

from lix_core.text import (
    capitals_problems,
    clean_name,
    repair_mojibake,
    stray_name_problems,
    title_name,
)


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


def test_placeholders_and_a_lone_a_circumflex():
    # A no-break space mangled to "Â " (its trailer turned into a space upstream) goes; a
    # real Â before a letter stays; "N/A" and names without a letter or digit become null
    df = pl.DataFrame(
        {
            "poi_id": list("abcdefgh"),
            "name": ["Â Table!", "Ân Phú", "Name Â", "N/A", "n/a", "≈", "#", "No. 1"],
        }
    )
    out = df.with_columns(clean_name(pl.col("name")))
    assert out["name"].to_list() == ["Table!", "Ân Phú", "Name", None, None, None, None, "No. 1"]
    assert stray_name_problems(out, "pois", "poi_id") == []
    assert len(stray_name_problems(df, "pois", "poi_id")) == 1


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


class TestTitleName:
    """A name that arrives in capitals, as people write it (#258)."""

    @pytest.mark.parametrize(
        ("raw", "shown"),
        [
            ("HEBDEN BRIDGE GROUP PRACTICE", "Hebden Bridge Group Practice"),
            # Acronyms stay; small words drop unless they open the name or a part of it
            ("THE NHS CENTRE", "The NHS Centre"),
            ("COVENTRY GP GROUP OF PRACTICES", "Coventry GP Group of Practices"),
            ("NEWCASTLE UNDER LYME PMS", "Newcastle under Lyme PMS"),
            ("INSPIRE HEALTH - THE AVENUE SURGERY", "Inspire Health - The Avenue Surgery"),
            ("THE SURGERY (THE GROVE)", "The Surgery (The Grove)"),
            # A word with no vowel is an acronym or initials, unless it is an abbreviation
            ("STRATFORD-UPON-AVON HC", "Stratford-upon-Avon HC"),
            ("ST. MARY'S HEALTH CTR", "St. Mary's Health Ctr"),
            ("HUMSHAUGH & WARK MED GRP", "Humshaugh & Wark Med Grp"),
            ("HOLES LANE MEDICAL LTD.", "Holes Lane Medical Ltd."),
            # Initials after "Dr", but not a two-letter surname
            ("DR PS BATH'S PRACTICE", "Dr PS Bath's Practice"),
            ("DR AA ANSARI'S PRACTICE", "Dr AA Ansari's Practice"),
            ("P'SHIP OF DR A B SHAH", "P'ship of Dr A B Shah"),
            ("DR NG & PARTNERS", "Dr Ng & Partners"),
            ("DR AL MOUSAWI", "Dr Al Mousawi"),
            ("DR LEE & PARTNERS", "Dr Lee & Partners"),
            # Apostrophes, hyphens, Mc
            ("O'RIORDAN SURGERY", "O'Riordan Surgery"),
            ("ST JAMES' SURGERY", "St James' Surgery"),
            ("THE RIGG-MILNER MEDICAL CENTRE", "The Rigg-Milner Medical Centre"),
            ("CHAPEL-EN-LE-FRITH", "Chapel-en-le-Frith"),
            ("MCKENZIE HOUSE", "McKenzie House"),
            ("MACKLIN STREET SURGERY", "Macklin Street Surgery"),
            # What the release's data tester found wrong in the first version
            ("SLAG LANE MC", "Slag Lane MC"),
            ("DRS LAMBERT & NG", "Drs Lambert & Ng"),
            ("DR ST PIERRE-LIBBERTON", "Dr St Pierre-Libberton"),
            ("LITTLE WALTHAM & GT NOTLEY SURGERY", "Little Waltham & Gt Notley Surgery"),
            ("THE SURGERY (SA BAILEY)", "The Surgery (SA Bailey)"),
            ("DR A NAEEM & PARTNERS,THE NEW SURGERY", "Dr A Naeem & Partners,The New Surgery"),
            ("ST MARY`S R.C. (A) SCHOOL", "St Mary`s R.C. (A) School"),
            ("WALLSEND UPCC", "Wallsend UPCC"),
            ("SPECIAL ALLOCATION SCHEME (NEL)", "Special Allocation Scheme (NEL)"),
            ("THE SURGERY (ONE)", "The Surgery (One)"),
            ("CARN TO COAST HEALTH CENTRES", "Carn to Coast Health Centres"),
            # Digits: a postcode district and a code stay, an ordinal drops its capitals
            ("FIRST 4 HEALTH GROUP - E7 HEALTH", "First 4 Health Group - E7 Health"),
            ("4TH FLOOR SURGERY", "4th Floor Surgery"),
            ("YORKLEIGH SURGERY(CT)", "Yorkleigh Surgery(CT)"),
        ],
    )
    def test_capitals_become_title_case(self, raw, shown):
        assert title_name(raw) == shown

    @pytest.mark.parametrize(
        ("raw", "shown"),
        [
            ("YSGOL Y GARREG", "Ysgol y Garreg"),
            ("MAES YR HAF", "Maes yr Haf"),
            ("Y TYMBL", "Y Tymbl"),  # the word that opens a name keeps its capital
            # w is a vowel: these are words, not acronyms
            ("CWRT HENRI", "Cwrt Henri"),
            ("PWLL", "Pwll"),
            ("YSGOL CWM BANWY", "Ysgol Cwm Banwy"),
            ("YGG TANYLAN", "YGG Tanylan"),
            # The I of an initialism is not the Welsh word for "to"
            ("GRIFFITHS C.I.W. PRIMARY SCHOOL", "Griffiths C.I.W. Primary School"),
            ("LLANELWEDD CHURCH IN WALES PRIMARY", "Llanelwedd Church in Wales Primary"),
            ("HAY-ON-WYE C.P. SCHOOL", "Hay-on-Wye C.P. School"),
            ("COED-Y-BRAIN PRIMARY", "Coed-y-Brain Primary"),
            ("PEN-Y-BONT SURGERY", "Pen-y-Bont Surgery"),
            ("LLANRHAEADR YM MOCHNANT C.P. SCHOOL", "Llanrhaeadr ym Mochnant C.P. School"),
            ("MEDDYGFA CWM RHYMNI PRACTICE", "Meddygfa Cwm Rhymni Practice"),
            ("GLAN YR AFON SURGERY", "Glan yr Afon Surgery"),
            ("ST BERNADETTES RC PRIMARY", "St Bernadettes RC Primary"),
        ],
    )  # fmt: skip
    def test_welsh_names(self, raw, shown):
        assert title_name(raw, welsh=True) == shown

    def test_w_is_a_vowel_in_welsh_names_only(self):
        assert title_name("DR GWD JONES") == "Dr GWD Jones"
        assert title_name("PWLL PRIMARY") == "PWLL Primary"

    def test_a_name_in_mixed_case_keeps_what_its_author_wrote(self):
        assert title_name("Meddygfa Teifi Surgery") == "Meddygfa Teifi Surgery"
        assert title_name("the McKenzie practice") == "the McKenzie practice"
        assert title_name("Tesco Superstore-Basildon") == "Tesco Superstore-Basildon"
        assert title_name(None) is None

    @pytest.mark.parametrize(
        ("raw", "shown", "welsh"),
        [
            # Northern Ireland's GP list: 297 of 304 names have the surname in capitals
            ("Dr. DEERING & PARTNERS", "Dr. Deering & Partners", False),
            ("Dr. MCGLADE & PARTNERS", "Dr. McGlade & Partners", False),
            ("DUNVANT Primary SCHOOL", "Dunvant Primary School", True),
            ("St. MARY'S CHURCH SCHOOL", "St. Mary's Church School", True),
            ("YSGOL Godre'r Berwyn", "Ysgol Godre'r Berwyn", True),
            # An article left capital between two Welsh words; one that opens the name stays
            ("Ysgol Y Gelli", "Ysgol y Gelli", True),
            ("Ysgol Yr Hendre", "Ysgol yr Hendre", True),
            ("Y Pant Comprehensive", "Y Pant Comprehensive", True),
            # Short words in capitals are left: an acronym is likelier than a shout
            ("Ysgol Gynradd YGG Bro", "Ysgol Gynradd YGG Bro", True),
            ("Riverside NHS Practice", "Riverside NHS Practice", False),
            ("Health Hub PCN Team", "Health Hub PCN Team", False),
        ],
    )
    def test_capitals_inside_a_mixed_case_name(self, raw, shown, welsh):
        assert title_name(raw, welsh=welsh) == shown


def test_a_source_whose_names_are_mostly_capitals_is_a_problem():
    shouting = [f"SURGERY NUMBER {i}" for i in range(30)]
    mixed = [f"Ysgol {i}" for i in range(25)] + ["YSGOL GYNRADD"] * 5 + ["ALDI"]
    pois = pl.DataFrame(
        {
            "name": shouting + mixed + ["RHND"],
            "source": ["NHS England ODS"] * 30 + ["Welsh Government"] * 31 + ["Tiny list"],
        }
    )
    assert capitals_problems(pois, "pois") == [
        "pois: 30 of 30 names from NHS England ODS are all in capitals"
    ]
    titled = pois.with_columns(pl.col("name").map_elements(title_name, return_dtype=pl.Utf8))
    assert capitals_problems(titled, "pois") == []
