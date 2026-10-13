"""Golden cases shared with the browser's TypeScript scorer (web/lib/scoring.ts).

Both implementations must reproduce contracts/fixtures/scoring_golden.json. To change
the maths on purpose, regenerate with LIX_UPDATE_GOLDEN=1 and update the TypeScript.
"""

import json
import os

import polars as pl
import pytest

from lix_core.paths import get_project_root
from lix_core.scoring import normalise, score_lsoas

GOLDEN = get_project_root() / "contracts" / "fixtures" / "scoring_golden.json"

# ``coverage``: the nations an indicator is built for; a row's theme total counts only the
# indicators its nation has (parks and gp_m are England-only here)
INDICATORS = [
    {"id": "crime", "theme": "safety", "weight": 1.5, "direction": "lower_better",
     "normalise": "rank", "log1p": True, "coverage": ["E", "W"]},
    {"id": "theft", "theme": "safety", "weight": 1.0, "direction": "lower_better",
     "normalise": "rank", "log1p": True, "benchmark": "nation", "coverage": ["E", "W"]},
    {"id": "no2", "theme": "environment", "weight": 1.0, "direction": "lower_better",
     "normalise": "threshold", "good": 10, "bad": 30, "coverage": ["E", "W"]},
    {"id": "parks", "theme": "environment", "weight": 0.5, "direction": "higher_better",
     "normalise": "scale", "scale_max": 1.0, "coverage": ["E"]},
    {"id": "gp_m", "theme": "health", "weight": 1.0, "direction": "lower_better",
     "normalise": "threshold", "good": 1000, "bad": 8000, "log1p": True, "coverage": ["E"]},
]  # fmt: skip
# Themes a nation declares it cannot cover: null there and left out of its overall
THEME_GAPS = {"W": ["safety"]}
RAW = {
    "crime": [12.0, 3.0, None, 40.0, 7.5, 0.0],
    "theft": [5.0, 5.0, 2.0, 30.0, None, 1.0],
    "no2": [8.0, 15.5, 22.0, 31.0, 12.0, 9.9],
    "parks": [0.9, 0.2, None, 1.0, 0.0, 0.55],
    "gp_m": [400.0, 1500.0, 9000.0, 650.0, None, 3000.0],
}
# One label per row; rank-normalised indicators with benchmark "nation" and the
# *_pct_nation columns are computed within these groups
GROUPS = {"nation": ["E", "E", "W", "E", "W", "W"]}
CASES = {
    "balanced": {"themes": {"safety": 1, "environment": 1, "health": 1}, "indicators": {}},
    "family": {"themes": {"safety": 2, "environment": 1.5, "health": 0.5},
               "indicators": {"parks": 2}},
    "safety_only": {"themes": {"safety": 1, "environment": 0, "health": 0}, "indicators": {}},
}  # fmt: skip


def compute() -> dict:
    df = pl.DataFrame({**RAW, **GROUPS})
    norms = df.select(
        normalise(
            pl.col(i["id"]), i["direction"], method=i["normalise"], log1p=i.get("log1p", False),
            good=i.get("good"), bad=i.get("bad"), scale_max=i.get("scale_max", 1.0),
            group=pl.col("nation") if i.get("benchmark") == "nation" else None,
        ).alias(f"n__{i['id']}")
        for i in INDICATORS
    )  # fmt: skip
    ind = [(i["id"], i["theme"], i["weight"]) for i in INDICATORS]
    expected = {}
    for name, case in CASES.items():
        out = score_lsoas(
            norms.with_columns(df["nation"]),
            ind,
            case["themes"],
            case["indicators"],
            group="nation",
            applicable={i["id"]: i["coverage"] for i in INDICATORS},
            theme_gaps=THEME_GAPS,
        )
        expected[name] = {c: out[c].to_list() for c in out.columns}
    return {
        "indicators": INDICATORS,
        "raw": RAW,
        "groups": GROUPS,
        "theme_gaps": THEME_GAPS,
        "normalised": {c: norms[c].to_list() for c in norms.columns},
        "cases": CASES,
        "expected": expected,
    }


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a == pytest.approx(b, abs=1e-9)


def test_python_matches_golden():
    current = compute()
    if os.environ.get("LIX_UPDATE_GOLDEN") or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=1) + "\n")
    golden = json.loads(GOLDEN.read_text())
    for col, values in golden["normalised"].items():
        assert all(_close(a, b) for a, b in zip(current["normalised"][col], values)), col
    for case, cols in golden["expected"].items():
        for col, values in cols.items():
            got = current["expected"][case][col]
            assert all(_close(a, b) for a, b in zip(got, values)), f"{case}.{col}"
