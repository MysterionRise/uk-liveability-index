"""Which OpenStreetMap doctors and clinics read as GP practices (Ireland, #144)."""

import polars as pl

from lix_pipeline.stage.health import is_gp_practice


def test_is_gp_practice_keeps_surgeries_and_named_gp_clinics_only():
    df = pl.DataFrame(
        {
            "name": [
                "Ranelagh Medical Centre", "The Chiropody Surgery", "Cook Street Chiropody",
                "The Well Woman Centre", "OxyGeneration", "Affidea Letterkenny",
                "Dr Murphy", None, "Glasnevin Family Practice", "Skin Laser Clinic",
            ],
            "value": [
                "clinic", "doctors", "doctors", "clinic", "clinic", "clinic",
                "doctors", "doctors", "clinic", "doctors",
            ],
            "healthcare:speciality": [
                None, None, "podiatry", None, None, None, "general", None, None, None,
            ],
        }
    )  # fmt: skip
    kept = df.filter(is_gp_practice(df))["name"].to_list()
    assert kept == ["Ranelagh Medical Centre", "Dr Murphy", None, "Glasnevin Family Practice"]
