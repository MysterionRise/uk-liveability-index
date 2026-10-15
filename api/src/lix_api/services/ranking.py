"""Rank areas for a weighting, within a place or radius, with optional filters."""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import polars as pl

from lix_api.models import Level, Point, RankedArea, RankResult
from lix_api.services.names import base_locale
from lix_api.services.scoring import (
    ThemeWeights,
    match_indicator,
    match_theme,
    percentile,
    resolve_weights,
    scores_for,
)
from lix_api.services.search import search_place
from lix_api.store import Store
from lix_core.uncertainty import top_share

DEFAULT_RADIUS_KM = 10.0
Direction = Literal["best", "worst"]
# Levels a ranking can be made at above the small areas: each is a population-weighted
# mean of its small areas (code column, name column). Regions and nations are too few
# and too varied to rank.
AGGREGATE_LEVELS: dict[str, tuple[str, str]] = {
    "msoa": ("msoa21cd", "msoa_name"),
    "lad": ("lad_cd", "lad_nm"),
}
RANK_LEVELS = ("lsoa", *AGGREGATE_LEVELS)


def _within(store: Store, within: str | None, radius_km: float | None, lang: str = "en"):
    """Filter expression and label for "within" (an area or a place + radius); the
    label names an area in the locale asked for where the pack has that form."""
    if not within:
        return pl.lit(True), None, None, None, None
    places = search_place(store, within, limit=1)
    if not places:
        raise LookupError(f"Couldn't find {within!r}")
    place = places[0]
    if place.kind in ("lad", "region", "nation", "msoa") and radius_km is None:
        column = {"lad": "lad_cd", "region": "rgn_cd", "nation": "ctry_cd", "msoa": "msoa21cd"}[
            place.kind
        ]
        label = place.name
        if lang != "en":
            # The features carry each council's and neighbourhood's other-language name
            name_col = {"lad": "lad_nm", "msoa": "msoa_name"}.get(place.kind)
            if name_col and f"{name_col}_{lang}" in store.features.columns:
                row = store.features.filter(pl.col(column) == place.code)
                if row.height and row[f"{name_col}_{lang}"][0]:
                    label = row[f"{name_col}_{lang}"][0]
            elif f"name_{lang}" in store.areas.columns:
                row = store.areas.filter(pl.col("code") == place.code)
                if row.height and row[f"name_{lang}"][0]:
                    label = row[f"name_{lang}"][0]
        return pl.col(column) == place.code, label, place.bbox, label, None
    centre = place.centre
    km = radius_km or DEFAULT_RADIUS_KM
    dlat = km / 111.0
    dlon = km / (111.0 * math.cos(math.radians(centre.lat)))
    dist_km = (
        ((pl.col("pwc_lat") - centre.lat) * 111.0) ** 2
        + ((pl.col("pwc_lon") - centre.lon) * 111.0 * math.cos(math.radians(centre.lat))) ** 2
    ).sqrt()
    bbox = (centre.lon - dlon, centre.lat - dlat, centre.lon + dlon, centre.lat + dlat)
    return dist_km <= km, f"within {km:g} km of {place.name}", bbox, place.name, km


@dataclass(frozen=True)
class Measure:
    """What a ranking is sorted by: the weighted overall, a theme's score or an indicator."""

    sort_by: str
    score_column: str
    feature_columns: tuple[str, ...]
    label: str | None = None
    unit: str | None = None
    raw_column: str | None = None
    raw_higher_better: bool = True

    def value(self, row: dict) -> float | None:
        """The figure to show beside the rank: the indicator's raw value, or the score."""
        if self.sort_by == "overall":
            return None
        v = row.get(self.raw_column or self.score_column)
        # Scores to one decimal like the theme scores beside them; raw values to two
        return None if v is None else round(float(v), 2 if self.raw_column else 1)


def sort_measure(store: Store, sort_by: str | None) -> Measure:
    """Resolve ``"overall"``, ``"theme:<id>"`` or ``"indicator:<id>"`` against the catalogue.

    Theme and indicator names are matched leniently (an id, its label or an everyday
    word: "theme:schools" is education); a bare name without a kind is tried as a theme
    first, then as an indicator.
    """
    sort_by = (sort_by or "overall").strip()
    if sort_by.lower() in ("overall", "score", ""):
        return Measure("overall", "overall", ())
    kind, _, ident = sort_by.partition(":")
    kind, ident = kind.strip().lower(), ident.strip()
    if not ident:
        kind, ident = "", kind
    theme = match_theme(store, ident) if kind in ("theme", "") else None
    if theme:
        return Measure(
            f"theme:{theme}", f"theme__{theme}", (), label=store.themes[theme].label, unit="score"
        )
    iid = match_indicator(store, ident) if kind in ("indicator", "") else None
    if iid:
        spec = store.indicators[iid]
        score, raw = f"n__{iid}", f"raw__{iid}"
        columns = tuple(c for c in (score, raw) if c in store.features.columns)
        if score in columns:
            return Measure(
                f"indicator:{iid}",
                score,
                columns,
                label=spec.label,
                unit=spec.unit,
                raw_column=raw if raw in columns else None,
                raw_higher_better=spec.direction == "higher_better",
            )
    themes = ", ".join(f"theme:{t}" for t in store.themes)
    raise LookupError(
        f'Unknown sort_by {sort_by!r}: use "overall", one of {themes}, or "indicator:<id>" '
        "with an id from list_indicators"
    )


def _theme_minimums(store: Store, minimums: dict[str, float] | None) -> dict[str, float]:
    """``min_theme_scores`` keyed by theme id; an unknown theme is an error the caller
    can relay, not a 500."""
    out = {}
    for key, value in (minimums or {}).items():
        theme = match_theme(store, key)
        if theme is None:
            raise LookupError(
                f"Unknown theme {key!r} in min_theme_scores; themes: {', '.join(store.themes)}"
            )
        out[theme] = float(value)
    return out


def _stand_in(store: Store, indicator_id: str, nations: set[str]) -> str | None:
    """A scored substitute for ``indicator_id`` built in every one of ``nations``."""
    for spec in store.scored:
        pair = (
            indicator_id in spec.substitute_for
            or spec.id in store.indicators[indicator_id].substitute_for
        )
        if pair and nations <= set(store.applicable.get(spec.id, [])):
            return spec.id
    return None


def _check_available(store: Store, measure: Measure, df: pl.DataFrame) -> None:
    """An indicator or theme ranking over areas that all lack it is a wrong question,
    not an empty list: say where it is available and what stands in for it."""
    if not df.height or df[measure.score_column].null_count() < df.height:
        return
    if measure.sort_by.startswith("theme:"):
        theme = measure.sort_by.split(":", 1)[1]
        here = ", ".join(
            store.nation_names.get(n, n) for n in sorted(df["nation"].unique().to_list())
        )
        raise LookupError(
            f"{measure.label} is not scored in {here}: the nation declares it has too little "
            "open data for the theme (shown as limited data)"
        )
    if measure.sort_by.startswith("indicator:"):
        iid = measure.sort_by.split(":", 1)[1]
        nations = set(df["nation"].unique().to_list())
        here = ", ".join(store.nation_names.get(n, n) for n in sorted(nations))
        covered = [store.nation_names.get(n, n) for n in store.applicable.get(iid, [])]
        where = f"it is built for {', '.join(covered)}" if covered else "it has no values"
        stand_in = _stand_in(store, iid, nations)
        hint = (
            f' Rank by "indicator:{stand_in}" ({store.indicators[stand_in].label}) instead.'
            if stand_in
            else ""
        )
        raise LookupError(f"{measure.label} is not available in {here}: {where}.{hint}")


def rank_areas(
    store: Store,
    within: str | None = None,
    radius_km: float | None = None,
    level: Level = "lsoa",
    preset: str | None = None,
    theme_weights: ThemeWeights | None = None,
    indicator_weights: dict[str, float] | None = None,
    max_median_price: float | None = None,
    min_theme_scores: dict[str, float] | None = None,
    urban: bool | None = None,
    limit: int = 10,
    sort_by: str = "overall",
    direction: Direction = "best",
    locale: str | None = None,
) -> RankResult:
    """Best areas first (or worst, with ``direction="worst"``). Above the small areas
    (``msoa``, ``lad``), scores are population-weighted means of the small areas.

    ``sort_by`` ranks by one measure instead of the weighted overall: ``theme:<id>`` by a
    theme's score, ``indicator:<id>`` by an indicator's 0–100 score (so "lower is better"
    indicators rank their lowest values first); the weights still give the overall shown
    beside it. Areas without the measure are left out rather than sorted first.
    """
    if level not in RANK_LEVELS:
        raise LookupError(
            f"Rankings are made at {', '.join(repr(lv) for lv in RANK_LEVELS)} level, not {level!r}"
        )
    limit = max(1, int(limit))
    name, themes, multipliers = resolve_weights(store, preset, theme_weights, indicator_weights)
    measure = sort_measure(store, sort_by)
    minimums = _theme_minimums(store, min_theme_scores)
    scores = scores_for(store, themes, multipliers)
    lang = base_locale(locale)
    # Every scope column _within() may filter on (ctry_cd for a nation), the names in
    # the locale asked for where the pack has them, and each feature column once: a
    # sort by house_price names raw__house_price itself
    columns = list(
        dict.fromkeys(
            [
                "lsoa21cd",
                "lsoa21nm",
                "msoa21cd",
                "msoa_name",
                "lad_cd",
                "rgn_cd",
                "ctry_cd",
                "nation",
                "lad_nm",
                "population",
                "pwc_lat",
                "pwc_lon",
                "urban",
                "raw__house_price",
                *measure.feature_columns,
                *(
                    f"{c}_{lang}"
                    for c in ("lsoa21nm", "msoa_name", "lad_nm")
                    if lang != "en" and f"{c}_{lang}" in store.features.columns
                ),
            ]
        )
    )
    df = pl.concat(
        [store.features.select(columns), scores.drop("lsoa21cd")],
        how="horizontal",
    )
    where, label, bbox, place_name, radius = _within(store, within, radius_km, lang)
    df = df.filter(where)
    if urban is not None:
        df = df.filter(pl.col("urban") == urban)

    theme_cols = [f"theme__{t}" for t in store.themes]
    numeric = list(dict.fromkeys(["overall", *theme_cols, *measure.feature_columns]))

    def in_locale(column: str) -> pl.Expr:
        """The column in the locale asked for where the row has that form."""
        other = f"{column}_{lang}"
        if lang != "en" and other in df.columns:
            return pl.coalesce(other, column)
        return pl.col(column)

    if level in AGGREGATE_LEVELS:
        code_col, name_col = AGGREGATE_LEVELS[level]
        w = pl.col("population")
        df = df.group_by(code_col).agg(
            in_locale(name_col).first().alias("name"),
            pl.col(name_col).first().alias("name_en"),
            in_locale("lad_nm").first().alias("lad_nm"),
            pl.col("nation").first(),
            w.sum().alias("population"),
            *[((pl.col(c) * w).sum() / w.filter(pl.col(c).is_not_null()).sum()).alias(c)
              for c in numeric if c != "raw__house_price"],
            pl.col("raw__house_price").median(),  # the price itself: a median, once
            ((pl.col("pwc_lat") * w).sum() / w.sum()).alias("pwc_lat"),
            ((pl.col("pwc_lon") * w).sum() / w.sum()).alias("pwc_lon"),
        ).rename({code_col: "code"})  # fmt: skip
        # Percentiles among the areas in the result set would mislead; use the small
        # areas' scale. A mean over no values (0/0) is no value, not NaN sorted first.
        df = df.with_columns(
            pl.lit(None, pl.Float64).alias("overall_pct"),
            *[pl.col(c).fill_nan(None) for c in numeric if c in df.columns],
        )
    else:
        # "Area (neighbourhood)", or the area alone where it is its own tier
        own_tier = pl.col("msoa21cd") == pl.col("code")
        df = df.rename({"lsoa21cd": "code"}).with_columns(
            pl.when(own_tier)
            .then(in_locale("lsoa21nm"))
            .otherwise(
                pl.concat_str(
                    in_locale("lsoa21nm"), pl.lit(" ("), in_locale("msoa_name"), pl.lit(")")
                )
            )
            .alias("name"),
            pl.when(own_tier)
            .then(pl.col("lsoa21nm"))
            .otherwise(
                pl.concat_str(pl.col("lsoa21nm"), pl.lit(" ("), pl.col("msoa_name"), pl.lit(")"))
            )
            .alias("name_en"),
            in_locale("lad_nm").alias("lad_nm"),
        )

    if max_median_price is not None:
        df = df.filter(pl.col("raw__house_price") <= max_median_price)
    for theme, minimum in minimums.items():
        if df.height and df[f"theme__{theme}"].null_count() == df.height:
            here = ", ".join(
                store.nation_names.get(n, n) for n in sorted(df["nation"].unique().to_list())
            )
            raise LookupError(
                f"{store.themes[theme].label} is not scored in {here}, so a minimum on it "
                "leaves nothing"
            )
        df = df.filter(pl.col(f"theme__{theme}") >= minimum)

    _check_available(store, measure, df)
    df = df.filter(pl.col(measure.score_column).is_not_null())
    candidates = df.height
    # A threshold score saturates (every area beyond 50 km reads 0, the sunniest fifth of
    # the South East reads 100), so ties are broken by the raw value in the same sense
    best = direction == "best"
    by, descending = [measure.score_column], [best]
    if measure.raw_column:
        by.append(measure.raw_column)
        descending.append(best if measure.raw_higher_better else not best)
    df = df.sort(by, descending=descending, nulls_last=True)
    # How robust the top results are to the exact weights (lix_core.uncertainty); a list
    # ranked by one measure does not depend on the weights. The worst-first list is the
    # best-first list of the scores' complements.
    present = [t for t in store.themes if f"theme__{t}" in df.columns and themes.get(t, 0) > 0]
    if measure.sort_by != "overall":
        shares = np.full(df.height, np.nan)
    elif df.height > limit and present:
        theme_scores = {
            t: (df[f"theme__{t}"].to_numpy().astype(float) if direction == "best"
                else 100.0 - df[f"theme__{t}"].to_numpy().astype(float))
            for t in present
        }  # fmt: skip
        shares = top_share(theme_scores, {t: themes[t] for t in present}, top_n=limit)
    else:
        shares = np.ones(df.height)
    top = df.head(limit).with_columns(pl.Series("stability", shares[:limit]))
    results = [
        RankedArea(
            rank=i + 1,
            level=level,
            code=r["code"],
            name=r["name"],
            name_en=r["name_en"] if r["name_en"] != r["name"] else None,
            local_authority=r["lad_nm"],
            overall=None if r["overall"] is None else round(r["overall"], 1),
            overall_percentile=percentile(r["overall_pct"]),
            themes={
                t: None if r[f"theme__{t}"] is None else round(r[f"theme__{t}"], 1)
                for t in store.themes
            },
            median_price=r["raw__house_price"],
            population=int(r["population"]),
            centre=Point(lat=r["pwc_lat"], lon=r["pwc_lon"]),
            sort_value=measure.value(r),
            sort_score=None
            if measure.sort_by == "overall" or r[measure.score_column] is None
            else round(r[measure.score_column], 1),
            stability=None if np.isnan(r["stability"]) else round(float(r["stability"]), 2),
        )  # fmt: skip
        for i, r in enumerate(top.iter_rows(named=True))
    ]
    return RankResult(
        level=level, within=label, preset=name, theme_weights=themes,
        sort_by=measure.sort_by, sort_label=measure.label, sort_unit=measure.unit,
        direction=direction, place=place_name, radius_km=radius,
        candidates=candidates, results=results, bbox=bbox,
    )  # fmt: skip
