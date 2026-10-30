import pytest

from lix_pipeline.geo.ngr import ngr_to_bng, ngr_to_bng_arrays


@pytest.mark.parametrize(
    ("ref", "xy"),
    [
        ("SP6419046470", (464_190.5, 246_470.5)),  # Abthorpe, Northamptonshire (1 m)
        ("SP 64190 46470", (464_190.5, 246_470.5)),
        ("TQ3080", (530_500.0, 180_500.0)),  # 1 km square in London
        ("NN166712", (216_650.0, 771_250.0)),  # Ben Nevis, 100 m
        ("SV9055010487", (90_550.5, 10_487.5)),  # Scilly: the S square at the origin
        ("HU4741", (447_500.0, 1_141_500.0)),  # Lerwick
    ],
)
def test_grid_references(ref, xy):
    assert ngr_to_bng(ref) == pytest.approx(xy)


@pytest.mark.parametrize("bad", [None, "", "IA1234", "SP123", "12345", "SP12345678901"])
def test_not_a_reference(bad):
    assert ngr_to_bng(bad) is None


def test_arrays_carry_nan_for_bad_rows():
    x, y = ngr_to_bng_arrays(["TQ3080", "nope"])
    assert x[0] == 530_500.0 and x[1] != x[1]
