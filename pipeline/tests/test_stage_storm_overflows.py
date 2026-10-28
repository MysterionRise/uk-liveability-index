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
    ],
)
def test_durations(value, hours):
    got = duration_hours(value)
    assert got == (pytest.approx(hours) if hours is not None else None)
