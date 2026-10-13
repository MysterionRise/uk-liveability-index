"""Rank areas for a weighting, within a place or radius, with optional filters."""

import math
from dataclasses import dataclass

import numpy as np
import polars as pl

from lix_api.models import Level, Point, RankedArea, RankResult
from lix_api.services.names import base_locale
from lix_api.services.scoring import ThemeWeights, resolve_weights, scores_for
from lix_api.services.search import search_place
from lix_api.store import Store
from lix_core.uncertainty import top_share

DEFAULT_RADIUS_KM = 10.0


def _within(store: Store, within: str | None, radius_km: float | None):
    """Filter expression and label for "within" (an area or a place + radius)."""
    if not within:
        return pl.lit(True), None, None
    places = search_place(store, within, limit=1)
    if not places:
        raise LookupError(f"Couldn't find {within!r}")
    place = places[0]
    if place.kind in ("lad", "region", "nation", "msoa") and radius_km is None:
        column = {"lad": "lad_cd", "region": "rgn_cd", "nation": "ctry_cd", "msoa": "msoa21cd"}[
            place.kind
        ]
        return pl.col(column) == place.code, place.name, place.bbox
    centre = place.centre
    km = radius_km or DEFAULT_RADIUS_KM
    dlat = km / 111.0
    dlon = km / (111.0 * math.cos(math.radians(centre.lat)))
    dist_km = (
        ((pl.col("pwc_lat") - centre.lat) * 111.0) ** 2
        + ((pl.col("pwc_lon") - centre.lon) * 111.0 * math.cos(math.radians(centre.lat))) ** 2
    ).sqrt()
    bbox = (centre.lon - dlon, centre.lat - dlat, centre.lon + dlon, centre.lat + dlat)
    return dist_km <= km, f"within {km:g} km of {place.name}", bbox


@dataclass(frozen=True)
class Measure:
    """What a ranking is sorted by: the weighted overall, a theme's score or an indicator."""

    sort_by: str
    score_column: str
    feature_columns: tuple[str, ...]
    label: str | None = None
    unit: str | None = None
    raw_column: str | None = None

    def value(self, row: dict) -> float | None:
        """The figure to show beside the rank: the indicator's raw value, or the score."""
        if self.sort_by == "overall":
            return None
        v = row.get(self.raw_column or self.score_column)
        # Scores to one decimal like the theme scores beside them; raw values to two
        return None if v is None else round(float(v), 2 if self.raw_column else 1)


def sort_measure(store: Store, sort_by: str | None) -> Measure:
    """Resolve ``"overall"``, ``"theme:<id>"`` or ``"indicator:<id>"`` against the catalogue."""
    sort_by = (sort_by or "overall").strip()
    if sort_by == "overall":
        return Measure("overall", "overall", ())
    kind, _, ident = sort_by.partition(":")
    if kind == "theme" and ident in store.themes:
        return Measure(
            sort_by, f"theme__{ident}", (), label=store.themes[ident].label, unit="score"
        )
    if kind == "indicator" and ident in store.indicators:
        spec = store.indicators[ident]
        score, raw = f"n__{ident}", f"raw__{ident}"
        columns = tuple(c for c in (score, raw) if c in store.features.columns)
        if score in columns:
            return Measure(
                sort_by,
                score,
                columns,
                label=spec.label,
                unit=spec.unit,
                raw_column=raw if raw in columns else None,
            )
    themes = ", ".join(f"theme:{t}" for t in store.themes)
    raise LookupError(
        f'Unknown sort_by {sort_by!r}: use "overall", one of {themes}, or "indicator:<id>" '
        "with an id from list_indicators"
    )


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
    locale: str | None = None,
) -> RankResult:
    """Best areas first. At MSOA level, scores are population-weighted LSOA means.

    ``sort_by`` ranks by one measure instead of the weighted overall: ``theme:<id>`` by a
    theme's score, ``indicator:<id>`` by an indicator's 0–100 score (so "lower is better"
    indicators rank their lowest values first); the weights still give the overall shown
    beside it.
    """
    name, themes, multipliers = resolve_weights(store, preset, theme_weights, indicator_weights)
    measure = sort_measure(store, sort_by)
    scores = scores_for(store, themes, multipliers)
    df = pl.concat(
        [
            store.features.select(
                "lsoa21cd",
                "lsoa21nm",
                "msoa21cd",
                "msoa_name",
                "lad_cd",
                "rgn_cd",
                "lad_nm",
                "population",
                "pwc_lat",
                "pwc_lon",
                "urban",
                "raw__house_price",
                *measure.feature_columns,
            ),  # fmt: skip
            scores.drop("lsoa21cd"),
        ],
        how="horizontal",
    )
    where, label, bbox = _within(store, within, radius_km)
    df = df.filter(where)
    if urban is not None:
        df = df.filter(pl.col("urban") == urban)

    theme_cols = [f"theme__{t}" for t in store.themes]

    def in_locale(column: str) -> pl.Expr:
        """The column in the locale asked for where the row has that form."""
        other = f"{column}_{base_locale(locale)}"
        if base_locale(locale) != "en" and other in df.columns:
            return pl.coalesce(other, column)
        return pl.col(column)

    if level == "msoa":
        w = pl.col("population")
        df = df.group_by("msoa21cd").agg(
            in_locale("msoa_name").first().alias("name"),
            pl.col("msoa_name").first().alias("name_en"),
            in_locale("lad_nm").first().alias("lad_nm"),
            w.sum().alias("population"),
            *[((pl.col(c) * w).sum() / w.filter(pl.col(c).is_not_null()).sum()).alias(c)
              for c in ["overall", *theme_cols, *measure.feature_columns]],
            pl.col("raw__house_price").median(),
            ((pl.col("pwc_lat") * w).sum() / w.sum()).alias("pwc_lat"),
            ((pl.col("pwc_lon") * w).sum() / w.sum()).alias("pwc_lon"),
        ).rename({"msoa21cd": "code"})  # fmt: skip
        # MSOA percentiles among MSOAs in the result set would mislead; use LSOA's scale
        df = df.with_columns(pl.lit(None, pl.Float64).alias("overall_pct"))
    else:
        df = df.rename({"lsoa21cd": "code"}).with_columns(
            pl.concat_str(
                in_locale("lsoa21nm"), pl.lit(" ("), in_locale("msoa_name"), pl.lit(")")
            ).alias("name"),
            pl.concat_str(pl.col("lsoa21nm"), pl.lit(" ("), pl.col("msoa_name"), pl.lit(")")).alias(
                "name_en"
            ),
            in_locale("lad_nm").alias("lad_nm"),
        )

    if max_median_price is not None:
        df = df.filter(pl.col("raw__house_price") <= max_median_price)
    for theme, minimum in (min_theme_scores or {}).items():
        df = df.filter(pl.col(f"theme__{theme}") >= minimum)

    candidates = df.height
    df = df.sort(measure.score_column, descending=True, nulls_last=True)
    # How robust the top results are to the exact weights (lix_core.uncertainty); a list
    # ranked by one measure does not depend on the weights
    present = [t for t in store.themes if f"theme__{t}" in df.columns and themes.get(t, 0) > 0]
    if measure.sort_by != "overall":
        shares = np.full(df.height, np.nan)
    elif df.height > limit and present:
        theme_scores = {t: df[f"theme__{t}"].to_numpy().astype(float) for t in present}
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
            overall_percentile=None if r["overall_pct"] is None else round(r["overall_pct"]),
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
        candidates=candidates, results=results, bbox=bbox,
    )  # fmt: skip
