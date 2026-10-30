import pytest

from lix_pipeline.stage.storm_overflows import duration_hours


@pytest.mark.parametrize(
    ("value", "hours"),
    [
        ("00:00:00", 0.0),
        ("12:30:00", 12.5),
        ("1234:15:36", 1234.26),
        ("7:05", 7 + 5 / 60),
        (0.5, 12.0),
        (None, None),
        ("n/a", None),
        ("", None),
        # Durations formatted as dates (most of the 2025 return): serial 0 reads as
        # 1899-12-31, and Excel's phantom 29 February 1900 shifts the epoch on 1 March
        ("1899-12-31 00:00:00", 0.0),
        ("1899-12-31 00:15:00", 0.25),
        ("1899-12-31 06:15:00", 6.25),
        ("1900-01-16 21:00:00", 16 * 24 + 21),
        ("1900-02-28 00:00:00", 59 * 24),
        ("1900-03-01 00:00:00", 61 * 24),
    ],
)
def test_durations(value, hours):
    got = duration_hours(value)
    assert got == (pytest.approx(hours) if hours is not None else None)
