"""Turn the indicator table into the files the API and browser use.

    data/serve/lsoa_features.parquet  every LSOA: geography, raw__/n__/q__ per indicator,
                                      theme__ percentiles, overall and band (default preset)
    data/serve/scores.parquet         compact copy for the browser: n__ percentiles as
                                      uint16 (×100; 65535 = missing) + q__ quality codes
                                      (uint8, see lix_core.quality)
    data/serve/manifest.json          schema version, geography (country, nations, area
                                      counts), indicators, themes, presets, source
                                      versions, attribution and file checksums

The browser recomputes themes and the overall score from ``scores.parquet`` with
the same maths as ``lix_core.scoring`` whenever weights change.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from lix_core.codes import active_nations
from lix_core.config import (
    SERVE_SCHEMA_VERSION,
    indicator_coverage,
    load_indicators,
    load_label_catalogues,
    load_nations,
    load_registry,
    load_weights,
)
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_core.quality import QUALITY_CODE, QUALITY_LEVELS
from lix_core.scoring import band, normalise, score_lsoas
from lix_core.uncertainty import percentile_interval
from lix_pipeline.fetch.lock import read_lock

logger = setup_logging("serve.scores")

MISSING_U16 = 65535
GEO_COLUMNS = [
    "lsoa21cd", "lsoa21nm", "msoa21cd", "msoa_name", "lad_cd", "lad_nm", "rgn_cd", "rgn_nm",
    "nation", "ctry_cd", "pfa_nm", "ruc21cd", "ruc21nm", "ruc_class", "urban", "population",
    "area_km2", "pwc_lon", "pwc_lat", "pwc_x", "pwc_y", "bbox_w", "bbox_s", "bbox_e", "bbox_n",
]  # fmt: skip


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_features() -> pl.DataFrame:
    """Wide per-LSOA table with raw values, percentiles, quality flags and default scores."""
    catalogue = load_indicators()
    weights = load_weights()
    preset = weights.presets[weights.default_preset]
    long = pl.read_parquet(data_dir("indicators") / "long.parquet")
    geo = pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet").select(GEO_COLUMNS)

    raw = long.pivot(on="indicator_id", index="lsoa21cd", values="value")
    quality = long.pivot(on="indicator_id", index="lsoa21cd", values="quality")
    df = geo.join(raw.rename(lambda c: c if c == "lsoa21cd" else f"raw__{c}"), on="lsoa21cd")
    df = df.join(quality.rename(lambda c: c if c == "lsoa21cd" else f"q__{c}"), on="lsoa21cd")

    # Rank-normalised indicators benchmarked within the nation rank there; the theme and
    # overall percentiles come both UK-wide and within the nation (compare-against)
    df = df.with_columns(
        normalise(
            pl.col(f"raw__{i.id}"),
            i.direction,
            method=i.normalise,
            log1p=i.log1p,
            good=i.good,
            bad=i.bad,
            scale_max=i.scale_max,
            group=pl.col("nation") if i.benchmark == "nation" else None,
        ).alias(f"n__{i.id}")
        for i in catalogue.indicators
    )
    scored = [(i.id, i.theme, i.weight) for i in catalogue.scored()]
    scores = score_lsoas(df, scored, preset.themes, preset.indicators, group="nation")
    df = pl.concat([df, scores], how="horizontal").with_columns(
        band(pl.col("overall_pct")).alias("band")
    )
    df = df.with_columns(uncertainty_columns(df, scored, weights))
    return df.sort("lsoa21cd")


def uncertainty_columns(df: pl.DataFrame, scored, weights) -> list[pl.Series]:
    """Per preset, the 5–95% range of each LSOA's England percentile over plausible
    weightings (each theme weight nudged by about a quarter): ``pct_lo__``/``pct_hi__``."""
    out = []
    for name, preset in weights.presets.items():
        themes = score_lsoas(df, scored, preset.themes, preset.indicators)
        theme_scores = {
            c.removeprefix("theme__"): themes[c].to_numpy().astype(float)
            for c in themes.columns
            if c.startswith("theme__")
        }
        lo, hi = percentile_interval(theme_scores, preset.themes)
        out += [
            pl.Series(f"pct_lo__{name}", lo).round(1).fill_nan(None),
            pl.Series(f"pct_hi__{name}", hi).round(1).fill_nan(None),
        ]
    return out


def compact_scores(features: pl.DataFrame) -> pl.DataFrame:
    """Browser copy: uint16 percentiles and a uint8 quality code per scored indicator."""
    catalogue = load_indicators()
    scored = [i.id for i in catalogue.scored()]
    missing = QUALITY_CODE["missing"]
    return features.select(
        "lsoa21cd",
        # For colouring the zoomed-out MSOA and local authority layers, and the
        # compare-against options (nation, like-for-like urban/rural class)
        "msoa21cd",
        "lad_cd",
        "nation",
        "ruc_class",
        "urban",
        pl.col("population").cast(pl.UInt32),
        *[
            (pl.col(f"n__{iid}") * 100).round().fill_null(MISSING_U16).cast(pl.UInt16).alias(iid)
            for iid in scored
        ],
        *[
            pl.col(f"q__{iid}")
            .cast(pl.Utf8)
            .replace_strict(QUALITY_CODE, default=missing, return_dtype=pl.UInt8)
            .alias(f"q__{iid}")
            for iid in scored
        ],
    )


# A download older than this (days) has probably been superseded upstream. Daily and
# weekly sources (food hygiene, OSM, schools) change a little at a time, so a month is fine
STALE_AFTER_DAYS = {"daily": 30, "weekly": 30, "monthly": 62, "quarterly": 190, "annual": 400}


def is_stale(fetched_at: str | None, cadence: str, now: datetime | None = None) -> bool:
    """Whether a source fetched at ``fetched_at`` is older than its cadence allows."""
    limit = STALE_AFTER_DAYS.get(cadence)
    if limit is None or not fetched_at:
        return False
    fetched = datetime.fromisoformat(fetched_at)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - fetched).days > limit


def correlation_matrix(features: pl.DataFrame) -> dict:
    """Spearman correlations between scored indicators, ordered by theme (analyst view)."""
    catalogue = load_indicators()
    scored = sorted(catalogue.scored(), key=lambda i: (i.theme, i.id))
    ids = [i.id for i in scored if f"n__{i.id}" in features.columns]
    rho = features.select(f"n__{i}" for i in ids).to_pandas().corr(method="spearman")
    return {
        "ids": ids,
        "themes": [next(i.theme for i in scored if i.id == iid) for iid in ids],
        "rho": [[round(float(v), 2) for v in row] for row in rho.to_numpy()],
    }


def geography(area_counts: dict[str, int]) -> dict:
    """The manifest's geography block: the country, the nations in this build, their sizes."""
    cfg = load_nations()
    nations = {}
    for code in active_nations():
        spec = cfg.nations[code]
        nations[code] = {
            "name": spec.name,
            "ctry_cd": spec.ctry_cd,
            "bbox": list(spec.bbox),
            "levels": {
                level: {"official": lv.official, "count": lv.count}
                for level, lv in spec.levels.items()
            },
        }
    return {
        "country": cfg.country.model_dump(),
        "nations": nations,
        "active": list(active_nations()),
        "area_counts": area_counts,
    }


def languages() -> list[dict]:
    """English plus every data-label catalogue, with its review status."""
    out = [{"code": "en", "status": "reviewed"}]
    for locale, labels in load_label_catalogues().items():
        out.append({"code": locale, "status": (labels.get("_meta") or {}).get("status", "draft")})
    return out


def manifest(
    files: dict[str, Path],
    lsoa_count: int | None = None,
    correlations: dict | None = None,
    area_counts: dict[str, int] | None = None,
) -> dict:
    catalogue = load_indicators()
    weights = load_weights()
    registry = load_registry()
    lock = read_lock()
    from lix_pipeline.stage import BACKBONE_INPUTS

    used = sorted({s for i in catalogue.indicators for s in i.sources} | set(BACKBONE_INPUTS))
    return {
        "schema_version": SERVE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "geography": geography(area_counts or {}),
        "lsoa_count": lsoa_count,
        "demo": False,
        "quality_levels": list(QUALITY_LEVELS),
        "languages": languages(),
        "i18n": {
            locale: {k: v for k, v in labels.items() if k != "_meta"}
            for locale, labels in load_label_catalogues().items()
        },
        "scored_indicators": [i.id for i in catalogue.scored()],
        "indicators": [
            {**i.model_dump(), "coverage": indicator_coverage(i, registry)}
            for i in catalogue.indicators
        ],
        "themes": {k: v.model_dump() for k, v in catalogue.themes.items()},
        "default_preset": weights.default_preset,
        "presets": {k: v.model_dump() for k, v in weights.presets.items()},
        "sources": {
            slug: {
                "title": registry[slug].title,
                "licence": registry[slug].licence,
                "attribution": registry[slug].attribution,
                "version": lock.get(slug, {}).get("resolved", {}).get("version"),
                "fetched_at": lock.get(slug, {}).get("fetched", {}).get("fetched_at"),
                "cadence": registry[slug].cadence,
                "stale": is_stale(
                    lock.get(slug, {}).get("fetched", {}).get("fetched_at"), registry[slug].cadence
                ),
            }
            for slug in used
        },
        "files": {
            name: {"sha256": _sha256(p), "bytes": p.stat().st_size} for name, p in files.items()
        },
        "correlations": correlations,
        "encoding": {
            "scores.parquet": "percentiles × 100 as uint16; 65535 = missing; q__<indicator> is "
            "the uint8 index into quality_levels"
        },
    }


def build_serve() -> dict[str, Path]:
    out = data_dir("serve")
    out.mkdir(parents=True, exist_ok=True)
    features = build_features()
    files = {
        "lsoa_features.parquet": out / "lsoa_features.parquet",
        "scores.parquet": out / "scores.parquet",
    }
    features.write_parquet(files["lsoa_features.parquet"])
    # Snappy: the browser's parquet reader (hyparquet) decodes it without plugins
    compact_scores(features).write_parquet(files["scores.parquet"], compression="snappy")
    from lix_pipeline.serve.lookups import build_lookups

    files.update(build_lookups(features))
    area_counts = dict(features.group_by("nation").len().sort("nation").iter_rows())
    (out / "manifest.json").write_text(
        json.dumps(
            manifest(
                files,
                lsoa_count=features.height,
                correlations=correlation_matrix(features),
                area_counts=area_counts,
            ),
            indent=2,
            default=str,
        )
    )
    files["manifest.json"] = out / "manifest.json"
    for name, path in files.items():
        logger.info(f"{name}: {path.stat().st_size / 1e6:.1f} MB")
    return files
