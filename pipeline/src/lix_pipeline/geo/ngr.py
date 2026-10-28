"""Ordnance Survey National Grid references ("SP 64190 46470") → eastings and northings.

The two letters name a 100 km square: the first a 500 km square of a 5 × 5 grid that
starts at S (0, 0) with V south-west of the false origin, the second the 100 km square
within it; I is not used. The digits split in half, easting first; a reference with
fewer digits names a larger square, and its centre is returned.
"""

from __future__ import annotations

import re

import numpy as np

_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # no I
_REF = re.compile(r"^([A-HJ-Z]{2})(\d*)$")


def ngr_to_bng(ref: str | None) -> tuple[float, float] | None:
    """Easting and northing in metres of a grid reference's square (its centre), or
    None when it is not one."""
    if ref is None:
        return None
    m = _REF.match(re.sub(r"\s+", "", str(ref)).upper())
    if not m or len(m.group(2)) % 2 or len(m.group(2)) > 10:
        return None
    l1, l2 = (_LETTERS.index(c) for c in m.group(1))
    e100 = ((l1 - 2) % 5) * 5 + (l2 % 5)
    n100 = (19 - (l1 // 5) * 5) - (l2 // 5)
    digits = m.group(2)
    k = len(digits) // 2
    unit = 10 ** (5 - k)  # metres per last digit
    e = int(digits[:k]) * unit if k else 0
    n = int(digits[k:]) * unit if k else 0
    return e100 * 100_000 + e + unit / 2, n100 * 100_000 + n + unit / 2


def ngr_to_bng_arrays(refs) -> tuple[np.ndarray, np.ndarray]:
    """Vector form: NaN for references that do not parse."""
    xy = [ngr_to_bng(r) or (np.nan, np.nan) for r in refs]
    a = np.asarray(xy, dtype=float).reshape(-1, 2)
    return a[:, 0], a[:, 1]
