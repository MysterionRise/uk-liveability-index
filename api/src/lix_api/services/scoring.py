"""Scores for any weighting: preset, custom theme weights and indicator multipliers."""

import math

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
    "weather": "climate", "sunshine": "climate", "sunny": "climate", "sun": "climate",
    "rain": "climate", "rainfall": "climate", "warm": "climate", "mild": "climate",
}  # fmt: skip
# Everyday words for single scored indicators (lower case) → ids, for lenient multiplier
# keys and the rules engine behind generated profiles
INDICATOR_WORDS = {
    "broadband": "gigabit_broadband", "wifi": "gigabit_broadband",
    "internet": "gigabit_broadband",
    "gigabit": "gigabit_broadband", "fibre": "gigabit_broadband",
    "noise": "noise_exposure", "noisy": "noise_exposure", "quiet": "noise_exposure",
    "sun": "sunshine", "sunny": "sunshine", "sunshine": "sunshine",
    "rain": "wet_days", "rainy": "wet_days", "wet": "wet_days",
    "warm summer": "summer_warmth", "warm summers": "summer_warmth", "hot summers": "summer_warmth",
    "mild winter": "winter_mildness", "mild winters": "winter_mildness", "frost": "winter_mildness",
    "no2": "no2", "traffic fumes": "no2", "exhaust": "no2",
    "pm2.5": "pm25", "particulates": "pm25", "smog": "pm25", "clean air": "pm25",
    "air quality": "pm25", "air pollution": "pm25",
    "park": "green_space_access", "parks": "green_space_access",
    "green space": "green_space_access",
    "green spaces": "green_space_access", "playing fields": "green_space_access",
    "trees": "tree_canopy", "tree cover": "tree_canopy", "leafy": "tree_canopy",
    "flood": "flood_risk", "flooding": "flood_risk", "floods": "flood_risk",
    "burglary": "crime_burglary", "burglaries": "crime_burglary",
    "violence": "crime_violence", "violent crime": "crime_violence",
    "antisocial": "crime_asb", "anti social": "crime_asb", "asb": "crime_asb",
    "theft": "crime_theft", "car crime": "crime_theft",
    "road safety": "road_danger", "cycling": "road_danger", "cyclist": "road_danger",
    "a e": "hospital_ae_distance", "hospital": "hospital_ae_distance",
    "gp": "gp_distance", "doctor": "gp_distance", "doctors": "gp_distance",
    "surgery": "gp_distance",
    "pharmacy": "pharmacy_distance", "chemist": "pharmacy_distance",
    "dentist": "dentist_distance", "dentists": "dentist_distance",
    "primary school": "primary_school_quality", "primary schools": "primary_school_quality",
    "secondary school": "secondary_school_quality", "secondary schools": "secondary_school_quality",
    "gcse": "secondary_school_quality", "gcses": "secondary_school_quality",
    "nursery": "nursery_access", "nurseries": "nursery_access", "childcare": "nursery_access",
    "toddler": "nursery_access", "toddlers": "nursery_access",
    "school choice": "school_choice",
    "train": "rail_service", "trains": "rail_service", "rail": "rail_service",
    "station": "rail_service",
    "bus": "connectivity_overall", "buses": "connectivity_overall",
    "public transport": "connectivity_overall",
    "pub": "well_run_pubs", "pubs": "well_run_pubs",
    "restaurants": "food_and_drink", "cafes": "food_and_drink", "foodie": "food_and_drink",
    "eating out": "food_and_drink",
    "supermarket": "supermarket_distance", "groceries": "supermarket_distance",
    "gym": "leisure", "swimming": "leisure", "sports": "leisure", "sport": "leisure",
    "theatre": "culture", "museum": "culture", "museums": "culture", "gigs": "culture",
    "live music": "culture", "arts": "culture", "cinema": "culture",
    "house prices": "house_price", "cheap": "house_price", "affordable": "house_price",
    "budget": "house_price", "expensive": "house_price",
    "council tax": "council_tax", "damp": "housing_condition",
    "deprivation": "income_deprivation",
}  # fmt: skip
PRESET_WORDS = {
    "default": "balanced", "families": "family", "retired": "retiree", "retirees": "retiree",
    "retirement": "retiree", "commuters": "commuter", "commuting": "commuter",
    "commute": "commuter", "young professionals": "young_professional",
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


def match_theme(store: Store, key: str) -> str | None:
    """A theme id for its id, label or an everyday word ("schools" is education)."""
    labels = {t: getattr(spec, "label", t) for t, spec in store.themes.items()}
    return _match(key, store.themes, THEME_WORDS, labels)


def match_indicator(store: Store, key: str) -> str | None:
    """An indicator id for its id, label or an everyday word ("broadband")."""
    labels = {i: spec.label for i, spec in store.indicators.items()}
    return _match(key, store.indicators, INDICATOR_WORDS, labels)


def scored_ids(store: Store) -> set[str]:
    """The indicator ids a multiplier can act on (the rest are context or diagnostic)."""
    return {i.id for i in store.scored}


def percentile(v: float | None) -> float | None:
    """A percentile for "better than N% of…": whole, and the top area is 99, never 100."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return min(99.0, float(round(v)))


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
