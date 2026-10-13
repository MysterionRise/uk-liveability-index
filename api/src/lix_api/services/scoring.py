"""Scores for any weighting: preset, custom theme weights and indicator multipliers."""

import polars as pl

from lix_api.store import Store
from lix_core.scoring import score_lsoas

ThemeWeights = dict[str, float]
MAX_CACHED = 64


# Everyday words for themes and presets (lower case) → ids
THEME_WORDS = {
    "crime": "safety", "safe": "safety", "security": "safety",
    "air": "environment", "air quality": "environment", "green space": "environment",
    "parks": "environment", "nature": "environment", "flooding": "environment",
    "health": "health", "nhs": "health", "gp": "health", "gps": "health", "doctors": "health",
    "schools": "education", "school": "education", "childcare": "education",
    "nurseries": "education", "transport": "transport", "connectivity": "transport",
    "commute": "transport", "commuting": "transport", "broadband": "transport",
    "amenities": "amenities", "nightlife": "amenities", "pubs": "amenities",
    "shops": "amenities", "affordability": "housing", "prices": "housing",
    "house prices": "housing", "cost": "housing", "wellbeing": "community",
}  # fmt: skip
PRESET_WORDS = {
    "default": "balanced", "families": "family", "retired": "retiree", "retirees": "retiree",
    "retirement": "retiree", "commuters": "commuter", "young professionals": "young_professional",
}  # fmt: skip


def _match(
    key: str, ids, words: dict[str, str], labels: dict[str, str] | None = None
) -> str | None:
    """An id for a name given as the id, its label or an everyday word (any case)."""
    k = key.strip().lower()
    by_label = {v.lower(): i for i, v in (labels or {}).items()}
    for candidate in (k, k.replace(" ", "_"), by_label.get(k), words.get(k)):
        if candidate in ids:
            return candidate
    return None


def match_preset(store: Store, preset: str) -> str:
    name = _match(preset, store.presets, PRESET_WORDS)
    if name is None:
        raise ValueError(f"Unknown preset {preset!r}; choose from {sorted(store.presets)}")
    return name


def match_themes(store: Store, theme_weights: ThemeWeights | None) -> ThemeWeights:
    """Theme weights keyed by theme id (unknown names are kept, for the caller to report)."""
    labels = {t: getattr(spec, "label", t) for t, spec in store.themes.items()}
    return {
        _match(k, store.themes, THEME_WORDS, labels) or k: v
        for k, v in (theme_weights or {}).items()
    }


def resolve_weights(
    store: Store,
    preset: str | None = None,
    theme_weights: ThemeWeights | None = None,
    indicator_weights: dict[str, float] | None = None,
) -> tuple[str, ThemeWeights, dict[str, float]]:
    """Start from a preset, then apply any custom theme weights and indicator multipliers.

    Preset and theme names are matched leniently (case, labels like "Schools & childcare",
    common words like "crime" or "nightlife"), since models don't always use the ids.
    """
    name = match_preset(store, preset) if preset else store.default_preset
    base = store.presets[name]
    themes = {**base.themes, **match_themes(store, theme_weights)}
    unknown = set(themes) - set(store.themes)
    if unknown:
        raise ValueError(f"Unknown themes {sorted(unknown)}; themes are {sorted(store.themes)}")
    multipliers = {**base.indicators, **(indicator_weights or {})}
    if theme_weights or indicator_weights:
        name = f"{name} (adjusted)"
    return name, themes, multipliers


def scores_for(store: Store, themes: ThemeWeights, multipliers: dict[str, float]) -> pl.DataFrame:
    """Per-LSOA theme scores, overall and percentiles (country-wide and within the
    nation) for these weights.

    Computing every LSOA takes a few milliseconds; results are cached on the store per
    weighting, since a conversation reuses the same few.
    """
    key = (tuple(sorted(themes.items())), tuple(sorted(multipliers.items())))
    cache = store.score_cache
    if key not in cache:
        if len(cache) >= MAX_CACHED:
            cache.pop(next(iter(cache)))
        scored = [(i.id, i.theme, i.weight) for i in store.scored]
        out = score_lsoas(
            store.features,
            scored,
            themes,
            multipliers,
            group="nation",
            applicable=store.applicable,
            theme_gaps=store.theme_gaps,
        )
        cache[key] = pl.concat([store.features.select("lsoa21cd"), out], how="horizontal")
    return cache[key]
