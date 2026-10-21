# ruff: noqa: E501 (fixture tables read best unwrapped)
"""A tiny synthetic data/serve/ directory for API tests (no real data needed).

Eight LSOAs in four MSOAs: four in Leeds, two in Brighton, two in Cardiff. Indicator
values follow the real catalogue in config/, so every service sees the shapes it sees
in production.
"""

import json
import os
import tempfile

# The assistant reads LIX_MODEL when lix_api.agent is imported: use the scripted model
os.environ["LIX_MODEL"] = "test"
os.environ.setdefault("LIX_SERVE_DATA", "0")
# Turn logs and anything else written under data/ go to a scratch directory
os.environ["LIX_DATA_DIR"] = tempfile.mkdtemp(prefix="lix-api-tests-")

import polars as pl  # noqa: E402
import pytest  # noqa: E402
from pyproj import Transformer  # noqa: E402

from lix_api.store import Store, set_store  # noqa: E402
from lix_core.config import (  # noqa: E402
    SERVE_SCHEMA_VERSION,
    indicator_coverage,
    load_indicators,
    load_nations,
    load_registry,
    load_weights,
)
from lix_core.quality import QUALITY_LEVELS  # noqa: E402
from lix_core.scoring import band, normalise, score_lsoas  # noqa: E402

TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)

LSOAS = [
    # code, lsoa name, msoa, msoa name, lad, lad name, region, lat, lon, urban, price
    ("E01000001", "Leeds 044A", "E02000001", "Headingley", "E08000035", "Leeds", "E12000003", 53.8196, -1.5766, True, 260_000),
    ("E01000002", "Leeds 044B", "E02000001", "Headingley", "E08000035", "Leeds", "E12000003", 53.8230, -1.5800, True, 340_000),
    ("E01000003", "Leeds 034A", "E02000002", "Chapel Allerton", "E08000035", "Leeds", "E12000003", 53.8290, -1.5390, True, 380_000),
    ("E01000004", "Leeds 034B", "E02000002", "Chapel Allerton", "E08000035", "Leeds", "E12000003", 53.8320, -1.5350, False, 200_000),
    ("E01000005", "Brighton and Hove 010A", "E02000003", "Hanover", "E06000043", "Brighton and Hove", "E12000008", 50.8290, -0.1260, True, 450_000),
    ("E01000006", "Brighton and Hove 010B", "E02000003", "Hanover", "E06000043", "Brighton and Hove", "E12000008", 50.8310, -0.1230, True, 520_000),
    ("W01001880", "Cardiff 032A", "W02000384", "Cathays", "W06000015", "Cardiff", "W92000004", 51.4850, -3.1790, True, 240_000),
    ("W01001881", "Cardiff 032B", "W02000384", "Cathays", "W06000015", "Cardiff", "W92000004", 51.4880, -3.1750, True, 300_000),
    # A council whose name holds a comma, and Ireland's Small Areas in two Dublin councils
    ("E01000007", "Bristol 001A", "E02000004", "Clifton", "E06000023", "Bristol, City of", "E12000009", 51.4650, -2.6200, True, 410_000),
    ("I01000001", "Rathmines 001", "I02000001", "Rathmines", "I06000007", "Dublin City", "I92000000", 53.3240, -6.2650, True, None),
    ("I01000002", "Rathmines 002", "I02000001", "Rathmines", "I06000007", "Dublin City", "I92000000", 53.3260, -6.2600, True, None),
    ("I01000003", "Dalkey 001", "I02000002", "Dalkey", "I06000008", "Dún Laoghaire-Rathdown", "I92000000", 53.2780, -6.1000, True, None),
]  # fmt: skip
REGION_NAMES = {
    "E12000003": "Yorkshire and The Humber",
    "E12000008": "South East",
    "E12000009": "South West",
    "W92000004": "Wales",
    "I92000000": "Ireland",
}
COUNTRY_CODES = {"E": "E92000001", "W": "W92000004", "I": "I92000000"}
# Councils that share a county (Dublin's), as the pack's areas table carries it
COUNTIES = {"I06000007": "Dublin", "I06000008": "Dublin"}


def _raw_value(ind_id: str, i: int) -> float | None:
    """Deterministic, varied raw values; E01000003 is the safest and E01000006 the least;
    the Irish rows (the last three) read worse than every UK row."""
    if i >= 9:
        if ind_id.startswith("crime_"):
            return 80.0 + i
        if ind_id in ("no2",):
            return 24.0 + i / 10
        if ind_id.endswith("_distance"):
            return 6000.0 + 100 * i
        return 0.05
    if ind_id.startswith("crime_"):
        return [40.0, 25.0, 5.0, 12.0, 30.0, 60.0, 20.0, 35.0][i % 8]
    if ind_id == "house_price":
        return None if LSOAS[i][10] is None else float(LSOAS[i][10])
    if ind_id in ("no2",):
        return [18.0, 16.0, 12.0, 9.0, 14.0, 15.0, 13.0, 17.0][i % 8]
    if ind_id.endswith("_distance"):
        return [300.0, 450.0, 800.0, 2500.0, 350.0, 400.0, 500.0, 650.0][i % 8]
    return float((i * 7 + len(ind_id)) % 10) / 10 + 0.05


def build_serve_dir(path) -> None:
    catalogue, weights = load_indicators(), load_weights()
    registry = load_registry()
    rows = []
    for i, (code, name, msoa, msoa_nm, lad, lad_nm, rgn, lat, lon, urban, _) in enumerate(LSOAS):
        x, y = TO_BNG.transform(lon, lat)
        row = {
            "lsoa21cd": code, "lsoa21nm": name, "msoa21cd": msoa, "msoa_name": msoa_nm,
            "lad_cd": lad, "lad_nm": lad_nm, "rgn_cd": rgn, "rgn_nm": REGION_NAMES[rgn],
            "lad_nm_cy": "Caerdydd" if lad == "W06000015" else None,
            "nation": code[0], "ctry_cd": COUNTRY_CODES[code[0]],
            "pfa_nm": "x", "ruc21cd": "UN1" if urban else "RSN1",
            "ruc21nm": "Urban" if urban else "Smaller rural", "urban": urban,
            "ruc_class": "urban" if urban else "rural",
            "population": 1500 + 100 * i, "area_km2": 0.5, "pwc_lon": lon, "pwc_lat": lat,
            "pwc_x": x, "pwc_y": y, "bbox_w": lon - 0.01, "bbox_s": lat - 0.005,
            "bbox_e": lon + 0.01, "bbox_n": lat + 0.005,
        }  # fmt: skip
        for ind in catalogue.indicators:
            if code[0] not in indicator_coverage(ind, registry):
                # As the pipeline writes it: nothing where the nation has no source
                row[f"raw__{ind.id}"] = None
                row[f"q__{ind.id}"] = "not_available"
                continue
            row[f"raw__{ind.id}"] = _raw_value(ind.id, i)
            imputed = ind.id.startswith("crime_") and code == "E01000004"
            row[f"q__{ind.id}"] = "imputed" if imputed else "ok"
        rows.append(row)
    df = pl.DataFrame(
        rows, schema_overrides={f"raw__{i.id}": pl.Float64 for i in catalogue.indicators}
    )
    df = df.with_columns(
        normalise(pl.col(f"raw__{i.id}"), i.direction, method=i.normalise, log1p=i.log1p,
                  good=i.good, bad=i.bad, scale_max=i.scale_max).alias(f"n__{i.id}")
        for i in catalogue.indicators
    )  # fmt: skip
    preset = weights.presets[weights.default_preset]
    scores = score_lsoas(df, [(i.id, i.theme, i.weight) for i in catalogue.scored()],
                         preset.themes, preset.indicators, group="nation")  # fmt: skip
    df = pl.concat([df, scores], how="horizontal").with_columns(
        band(pl.col("overall_pct")).alias("band")
    )
    # Percentile ranges over plausible weightings, as the pipeline writes them
    from lix_pipeline.serve.scores import uncertainty_columns

    df = df.with_columns(
        uncertainty_columns(df, [(i.id, i.theme, i.weight) for i in catalogue.scored()], weights)
    )
    df.write_parquet(path / "lsoa_features.parquet")

    nations = load_nations()
    manifest = {
        "schema_version": SERVE_SCHEMA_VERSION,
        "generated_at": "2026-10-07T00:00:00+00:00",
        "geography": {
            "build": nations.build.model_dump(),
            "countries": {k: v.model_dump() for k, v in nations.countries.items()},
            "nations": {
                code: {"name": n.name, "ctry_cd": n.ctry_cd, "bbox": list(n.bbox),
                       "levels": {lv: {"official": s.official, "count": s.count,
                                       "label": s.label, "labels": s.labels}
                                  for lv, s in n.levels.items()},
                       "theme_gaps": list(n.theme_gaps), "names": dict(n.names),
                       "aliases": list(n.aliases), "headline_within": n.headline_within}
                for code, n in nations.nations.items()
            },
            "active": ["E", "W", "I"],
            "area_counts": {"E": 7, "W": 2, "I": 3},
        },
        "quality_levels": list(QUALITY_LEVELS),
        "scored_indicators": [i.id for i in catalogue.scored()],
        "indicators": [{**i.model_dump(), "coverage": indicator_coverage(i, registry)}
                       for i in catalogue.indicators],
        "themes": {k: v.model_dump() for k, v in catalogue.themes.items()},
        "default_preset": weights.default_preset,
        "presets": {k: v.model_dump() for k, v in weights.presets.items()},
        "sources": {s: {"title": s, "licence": "OGL-3.0", "attribution": f"Source {s}"}
                    for i in catalogue.indicators for s in i.sources},
    }  # fmt: skip
    (path / "manifest.json").write_text(json.dumps(manifest, default=str))

    pl.DataFrame({
        "postcode": ["LS6 3HN", "LS7 4PL", "BN2 9QA", "CF10 1AA"],
        "postcode_norm": ["LS63HN", "LS74PL", "BN29QA", "CF101AA"],
        "lsoa21cd": ["E01000001", "E01000003", "E01000005", "W01001880"],
        "lat": [53.8196, 53.8290, 50.8290, 51.4850], "lon": [-1.5766, -1.5390, -0.1260, -3.1790],
    }).write_parquet(path / "postcodes.parquet")  # fmt: skip

    pl.DataFrame({
        "place_id": ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10"],
        "name": ["Headingley", "Leeds", "Leeds", "Brighton", "Cardiff", "Ynys Môn", "Derry",
                 # Two Dublin suburbs in different councils, and a hamlet called Ireland
                 "Ranelagh", "Dalkey", "Ireland"],
        # OpenStreetMap names the city "Derry/Londonderry": the second form is name_alt
        "name_alt": [None, None, None, None, None, None, "Londonderry", None, None, None],
        "name_cy": [None, None, None, None, "Caerdydd", None, None, None, None, None],
        "name_gd": [None, None, None, None, None, None, None, None, None, None],
        "name_ga": [None, None, None, None, None, None, None, "Raghnallach", "Deilginis", None],
        "place_type": ["Suburban Area", "City", "Village", "Other Settlement", "City", "Village", "City",
                       "Suburban Area", "Village", "Hamlet"],
        "postcode_district": ["LS6", "LS1", "ME17", "BN1", "CF10", "LL77", "BT48", None, None, "LS7"],
        "local_authority": ["Leeds", "Leeds", "Maidstone", "Brighton and Hove", "Cardiff", "Isle of Anglesey",
                            "Derry City and Strabane", "Dublin City", "Dún Laoghaire-Rathdown", "Leeds"],
        "region": ["Yorkshire", "Yorkshire", "South East", "South East", "Wales", "Wales", "Northern Ireland",
                   "Ireland", "Ireland", "Yorkshire"],
        "lon": [-1.5766, -1.5500, 0.6070, -0.1370, -3.1790, -4.3300, -7.3100, -6.2560, -6.1050, -1.5380],
        "lat": [53.8196, 53.8000, 51.2467, 50.8220, 51.4850, 53.2700, 54.9970, 53.3260, 53.2770, 53.8310],
        "lsoa21cd": ["E01000001", "E01000002", "E01000002", "E01000005", "W01001880", "W01001881", "E01000002",
                     "I01000002", "I01000003", "E01000004"],
    }).write_parquet(path / "places.parquet")  # fmt: skip

    df = df.with_columns(
        pl.col("nation")
        .replace_strict({"E": "England", "W": "Wales", "I": "Ireland"})
        .alias("nation_name")
    )
    areas = []
    for level, code_col, name_col, keep in (
        ("msoa", "msoa21cd", "msoa_name", pl.lit(True)),
        ("lad", "lad_cd", "lad_nm", pl.lit(True)),
        ("region", "rgn_cd", "rgn_nm", pl.col("rgn_cd") != pl.col("ctry_cd")),
        ("nation", "ctry_cd", "nation_name", pl.lit(True)),
    ):
        areas.append(
            df.filter(keep).group_by(code_col).agg(
                pl.col(name_col).first().alias("name"), pl.col("lad_nm").first(),
                pl.col("nation").first(),
                pl.col("population").sum(), pl.len().alias("lsoas"),
                pl.col("bbox_w").min(), pl.col("bbox_s").min(),
                pl.col("bbox_e").max(), pl.col("bbox_n").max(),
            ).rename({code_col: "code"}).with_columns(pl.lit(level).alias("level"))
        )  # fmt: skip
    counties = pl.DataFrame({"code": list(COUNTIES), "county": list(COUNTIES.values())})
    pl.concat(areas, how="vertical_relaxed").join(counties, on="code", how="left").write_parquet(
        path / "areas.parquet"
    )
    df = df.drop("nation_name")

    pois = [
        ("pub", "Well Run Arms", 53.8200, -1.5770, {"well_run": True, "rating": 5}),
        ("pub", "Grubby Tavern", 53.8197, -1.5767, {"well_run": False, "rating": 2}),
        ("pub", "Far Away Inn", 53.8600, -1.6500, {"well_run": True, "rating": 5}),
        ("gp", "Headingley Surgery", 53.8210, -1.5760, {"practice_code": "B86001"}),
        ("primary_school", "Shire Oak Primary", 53.8190, -1.5780, {"quality": 0.7}),
    ]
    p = []
    for k, (cat, name, lat, lon, detail) in enumerate(pois):
        x, y = TO_BNG.transform(lon, lat)
        p.append({"category": cat, "poi_id": f"x{k}", "name": name, "x": x, "y": y, "lon": lon,
                  "lat": lat, "source": "test", "licence": "ODbL-1.0",
                  "detail": json.dumps(detail)})  # fmt: skip
    pl.DataFrame(p).write_parquet(path / "pois.parquet")


@pytest.fixture(scope="session")
def serve_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("serve")
    build_serve_dir(path)
    return path


@pytest.fixture
def store(serve_dir) -> Store:
    s = Store(serve_dir=serve_dir)
    set_store(s)
    import lix_api.services.sql as sql

    sql._guard = None
    return s
