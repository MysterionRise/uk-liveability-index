"""Checks on staged outputs, run with ``lix validate <target>`` on a full build.

Each validator returns a list of human-readable problems (empty means OK).
"""

import hashlib
import json
from pathlib import Path

import polars as pl

from lix_core.codes import active_nations, area_code_regex, in_scope
from lix_core.config import (
    NATION_NAMES,
    SERVE_SCHEMA_VERSION,
    indicator_coverage,
    load_indicators,
    load_nations,
    load_registry,
)
from lix_core.paths import data_dir
from lix_core.quality import QUALITY_LEVELS


def validate_geo() -> list[str]:
    """The backbone has every area of every active nation, once, with its columns filled."""
    problems = []
    staged = data_dir("staged")
    geo = pl.read_parquet(staged / "geo_lsoa.parquet")
    nations = active_nations()
    cfg = load_nations().nations

    expected = sum(cfg[n].levels["low"].count or 0 for n in nations)
    if geo.height != expected:
        problems.append(f"geo_lsoa has {geo.height:,} rows, expected {expected:,}")
    if geo["lsoa21cd"].n_unique() != geo.height:
        problems.append("geo_lsoa has duplicate lsoa21cd")
    if not geo["lsoa21cd"].str.contains(area_code_regex()).all():
        problems.append(f"geo_lsoa has codes outside the active nations {list(nations)}")

    required = [
        "lsoa21nm",
        "msoa21cd",
        "msoa21nm",
        "msoa_name",
        "lad_cd",
        "lad_nm",
        "rgn_cd",
        "rgn_nm",
        "nation",
        "ctry_cd",
        "area_type",
        "ruc21cd",
        "ruc_class",
        "urban",
        "pwc_x",
        "pwc_y",
        "pwc_lat",
        "pwc_lon",
        "population",
        "area_km2",
        "bbox_w",
        "bbox_s",
        "bbox_e",
        "bbox_n",
    ]
    missing_columns = [c for c in required if c not in geo.columns]
    problems += [f"geo_lsoa is missing column {c}" for c in missing_columns]
    for col in required:
        if col in geo.columns and (n := geo[col].null_count()) > 0:
            problems.append(f"geo_lsoa.{col} has {n:,} nulls")
    if missing_columns:
        return problems  # the per-nation checks below assume the columns exist

    for code in nations:
        spec = cfg[code]
        rows = geo.filter(in_scope("lsoa21cd", nations=(code,)))
        if (count := spec.levels["low"].count) and rows.height != count:
            problems.append(f"{spec.name}: {rows.height:,} LSOAs, expected {count:,}")
        if (count := spec.levels["mid"].count) and rows["msoa21cd"].n_unique() != count:
            found = rows["msoa21cd"].n_unique()
            problems.append(f"{spec.name}: {found:,} MSOAs, expected {count:,}")
        if not (rows["nation"] == code).all() or not (rows["ctry_cd"] == spec.ctry_cd).all():
            problems.append(f"{spec.name}: nation/ctry_cd columns disagree with the codes")
        low, high = spec.population
        pop = rows["population"].sum()
        if not low < pop < high:
            problems.append(f"{spec.name} population {pop:,} is outside {low:,}–{high:,}")
        if spec.geo.minted_codes:
            # Minted codes: the publisher's id rides along, once per area
            if "source_code" not in rows.columns or rows["source_code"].null_count():
                problems.append(f"{spec.name}: minted codes without a source_code for every area")
            elif rows["source_code"].n_unique() != rows.height:
                problems.append(f"{spec.name}: source_code repeats")

    # Centroids must sit inside their own LSOA's bbox
    outside = geo.filter(
        (pl.col("pwc_lon") < pl.col("bbox_w"))
        | (pl.col("pwc_lon") > pl.col("bbox_e"))
        | (pl.col("pwc_lat") < pl.col("bbox_s"))
        | (pl.col("pwc_lat") > pl.col("bbox_n"))
    )
    if outside.height:
        problems.append(f"{outside.height} centroids fall outside their LSOA's bounding box")

    nspl = pl.scan_parquet(staged / "nspl.parquet")
    nspl_lsoas = set(
        nspl.filter(pl.col("live") & in_scope("lsoa21cd"))
        .select("lsoa21cd")
        .unique()
        .collect()["lsoa21cd"]
    )
    missing = nspl_lsoas - set(geo["lsoa21cd"])
    if missing:
        problems.append(
            f"{len(missing)} NSPL LSOAs missing from geo_lsoa, e.g. {sorted(missing)[:3]}"
        )
    return problems


# Scored indicators must have a value for at least this share of LSOAs
MIN_SCORED_COVERAGE = 0.99
# ...except where the gaps are understood: GP ratings need at least half an LSOA's
# patients at a rated practice (new and unrated practices leave about 1% short)
COVERAGE_EXCEPTIONS = {"gp_quality": 0.98}
MISSING_U16 = 65535
SERVE_FILES = [
    "lsoa_features.parquet",
    "scores.parquet",
    "postcodes.parquet",
    "places.parquet",
    "areas.parquet",
    "pois.parquet",
    "manifest.json",
    "tiles/lsoa.pmtiles",
    "tiles/msoa.pmtiles",
    "tiles/lad.pmtiles",
]


def indicator_coverage_problems(
    features: pl.DataFrame, scored: list[str], coverage: dict[str, list[str]], nations: list[str]
) -> list[str]:
    """Each scored indicator covers (almost) every area of the nations it is built for and
    is ``not_available`` everywhere else."""
    problems = []
    for nation in nations:
        rows = features.filter(pl.col("nation") == nation)
        name = NATION_NAMES.get(nation, nation)
        if rows.is_empty():
            problems.append(f"no areas in {name}")
            continue
        for iid in scored:
            col, q = f"n__{iid}", f"q__{iid}"
            if col not in features.columns:
                continue
            if nation in coverage.get(iid, []):
                share = rows[col].is_not_null().mean()
                minimum = COVERAGE_EXCEPTIONS.get(iid, MIN_SCORED_COVERAGE)
                if share < minimum:
                    problems.append(f"{iid} covers {share:.1%} of {name} (< {minimum:.0%})")
            else:
                stray = rows.filter(pl.col(col).is_not_null()).height
                not_flagged = (
                    rows.filter(pl.col(q) != "not_available").height if q in rows.columns else 0
                )
                if stray or not_flagged:
                    problems.append(
                        f"{iid} is not built for {name} but has {stray} values and "
                        f"{not_flagged} rows not flagged not_available"
                    )
    return problems


def validate_serve(serve_dir: Path | None = None) -> list[str]:
    """The files the API and browser read: complete, consistent and as the manifest says.

    Works on a full build (every area of the active nations) and on a demo cut
    (``demo: true`` in the manifest, with its own ``lsoa_count`` and ``area_counts``).
    """
    serve = serve_dir or data_dir("serve")
    missing = [f for f in SERVE_FILES if not (serve / f).is_file()]
    if missing:
        return [f"missing {', '.join(missing)} in {serve}"]
    manifest = json.loads((serve / "manifest.json").read_text())
    version = manifest.get("schema_version")
    if version != SERVE_SCHEMA_VERSION:
        return [
            f"manifest schema_version {version!r}; this code reads v{SERVE_SCHEMA_VERSION}: "
            "rebuild with lix score (or lix demo-data)"
        ]
    problems = []
    catalogue = load_indicators()
    scored = [i.id for i in catalogue.scored()]
    # The nations each scored indicator is built for, from the catalogue (a pack cut
    # before an indicator existed does not list it)
    registry = load_registry()
    built_for = {i.id: indicator_coverage(i, registry) for i in catalogue.scored()}
    cfg = load_nations().nations
    geo = manifest.get("geography", {})
    nations = [n for n in geo.get("active", []) if n in cfg]
    area_counts = {k: int(v) for k, v in geo.get("area_counts", {}).items()}
    if not nations:
        problems.append("manifest geography names no configured nation")
    if manifest.get("demo"):
        expected = manifest.get("lsoa_count")
    else:
        expected = sum(cfg[n].levels["low"].count or 0 for n in nations)
        for n in nations:
            if (c := cfg[n].levels["low"].count) and area_counts.get(n) != c:
                problems.append(
                    f"manifest counts {area_counts.get(n)} areas in {n}, expected {c:,}"
                )

    features = pl.read_parquet(serve / "lsoa_features.parquet")
    codes = features["lsoa21cd"]
    if features.height != expected:
        problems.append(f"lsoa_features has {features.height:,} rows, expected {expected:,}")
    if sum(area_counts.values()) != features.height:
        problems.append(
            f"manifest area_counts sum to {sum(area_counts.values()):,}, not {features.height:,}"
        )
    if codes.n_unique() != features.height:
        problems.append("lsoa_features has duplicate lsoa21cd")
    if nations and not codes.str.contains(area_code_regex(nations=tuple(nations))).all():
        problems.append(f"lsoa_features has codes outside the nations {nations}")
    for col in ("nation", "ctry_cd", "ruc_class"):
        if col not in features.columns:
            problems.append(f"lsoa_features is missing column {col}")
    if "nation" in features.columns:
        coverage = {i["id"]: i.get("coverage") or [] for i in manifest.get("indicators", [])}
        problems += indicator_coverage_problems(features, scored, coverage, nations)
    for iid in scored:
        # An indicator built for none of the pack's nations (Ireland's substitutes in a
        # UK-only cut) is not expected in its features
        if nations and built_for.get(iid) and not set(built_for[iid]) & set(nations):
            continue
        if f"n__{iid}" not in features.columns:
            problems.append(f"scored indicator {iid} is missing from lsoa_features")
    overall = features["overall"]
    if overall.null_count() or not overall.is_between(0, 100).all():
        problems.append("overall score has nulls or values outside 0–100")

    scores = pl.read_parquet(serve / "scores.parquet")
    if set(scores["lsoa21cd"]) != set(codes):
        problems.append("scores.parquet and lsoa_features have different LSOAs")
    # A cut of an older build may predate an indicator built for none of its nations
    # (Ireland's substitutes in a UK-only cut): those are not expected in its files
    expected = [
        i for i in scored if not nations or not built_for.get(i) or set(built_for[i]) & set(nations)
    ]
    listed = manifest.get("scored_indicators") or []
    if listed != scored and listed != expected:
        problems.append("manifest scored_indicators differ from config/indicators.yaml")
    if set(manifest.get("themes", {})) != set(catalogue.themes):
        problems.append("manifest themes differ from config/indicators.yaml")
    if manifest.get("quality_levels") != list(QUALITY_LEVELS):
        problems.append("manifest quality_levels differ from lix_core.quality")
    absent = [i for i in expected if i not in scores.columns]
    if absent:
        problems.append(f"scores.parquet lacks scored indicators {absent}")
    no_quality = [i for i in expected if f"q__{i}" not in scores.columns]
    if no_quality:
        problems.append(f"scores.parquet lacks quality codes for {no_quality}")
    context_absent = [
        i for i in manifest.get("context_indicators") or [] if i not in scores.columns
    ]
    if context_absent:
        problems.append(f"scores.parquet lacks context indicators {context_absent}")
    for iid in scored:
        if iid in scores.columns:
            bad = scores.filter((pl.col(iid) > 10_000) & (pl.col(iid) != MISSING_U16)).height
            if bad:
                problems.append(f"scores.parquet {iid} has {bad} values above 100.00")

    for name, meta in manifest.get("files", {}).items():
        path = serve / name
        if not path.is_file():
            problems.append(f"manifest lists {name}, which doesn't exist")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("sha256"):
            problems.append(f"{name} doesn't match its manifest checksum")

    for tiles in ("lsoa", "msoa", "lad"):
        path = serve / "tiles" / f"{tiles}.pmtiles"
        if path.read_bytes()[:7] != b"PMTiles":
            problems.append(f"tiles/{tiles}.pmtiles isn't a PMTiles file")
    return problems


def validate_places() -> list[str]:
    from lix_pipeline.qa.places import anchor_problems

    return anchor_problems()


def validate_config() -> list[str]:
    """Every theme keeps enough scored weight in each active nation (coverage_problems), and
    every reviewed label catalogue is complete (a draft may have gaps: English fills them)."""
    from lix_core.config import (
        catalogue_gaps,
        coverage_problems,
        load_label_catalogues,
        load_nations,
        load_registry,
        load_weights,
    )

    catalogue, weights = load_indicators(), load_weights()
    nations = {n: load_nations().nations[n] for n in active_nations()}
    gaps_declared = {n: list(spec.theme_gaps) for n, spec in nations.items()}
    problems = coverage_problems(catalogue, load_registry(), active_nations(), gaps_declared)
    for locale, labels in load_label_catalogues().items():
        gaps = catalogue_gaps(catalogue, weights, labels, nations)
        status = (labels.get("_meta") or {}).get("status")
        if gaps and status == "reviewed":
            problems.append(f"config/i18n/{locale}.yaml is reviewed but lacks {gaps[:5]}…")
        elif gaps:
            print(f"config/i18n/{locale}.yaml (draft): {len(gaps)} labels fall back to English")
    return problems


VALIDATORS = {
    "config": validate_config,
    "geo": validate_geo,
    "serve": validate_serve,
    "places": validate_places,
}
