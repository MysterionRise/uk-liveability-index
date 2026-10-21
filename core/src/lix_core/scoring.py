"""Scoring maths shared by the pipeline, the API and (mirrored in TypeScript) the browser.

Per indicator, raw values become a 0–100 score where higher is always better, by one
of three methods (chosen per indicator in config/indicators.yaml):

- ``rank``: percentile over the benchmark group: the whole country, or within
  ``group`` (e.g. the nation) for measures that aren't comparable across nations. For
  relative measures with no natural "good enough" level (crime rates, prices,
  deprivation scores).
- ``scale``: the value's own scale (an access index 0–1, a DfT score 0–100) mapped to
  0–100. Access indices already saturate, so a dense suburb and a city centre that both
  have plenty of cafés both score near 100.
- ``threshold``: 100 at or better than ``good``, 0 at or worse than ``bad``, linear
  between (on a log scale when ``log1p``). For distances ("a GP within 1km is fine")
  and pollution against health guidelines.

Ranking everything would turn trivial differences (a GP 300m vs 900m away) into big
score gaps and push every rural area to the bottom; saturating scales avoid that.

Per theme: weighted mean of its indicators' scores over those the LSOA has; null when
less than ``MIN_THEME_COVERAGE`` of the theme's weight is present. Overall: weighted
mean of theme scores. Both are 0–100 scores; percentiles ("better than N% of
England's neighbourhoods") are reported alongside, not instead.
"""

import math
from collections.abc import Collection, Mapping, Sequence

import polars as pl

MIN_THEME_COVERAGE = 0.5


def percentile_rank(values: pl.Expr, group: pl.Expr | None = None) -> pl.Expr:
    """0–100 percentile with ties averaged; nulls stay null.

    The lowest value gets 0 and the highest 100 (for n > 1; a lone value gets 50).
    With ``group``, ranks are computed within each group (nation, urban/rural class).
    """
    rank = values.rank(method="average")
    n = values.count()
    if group is not None:
        # A row without a group is in no group: its within-group percentile is null
        rank, n = rank.over(group), n.over(group)
        values = pl.when(group.is_null()).then(None).otherwise(values)
    pct = (rank - 1) / (n - 1) * 100
    return pl.when(n > 1).then(pct).when(values.is_not_null()).then(50.0)


def normalise(
    values: pl.Expr,
    direction: str,
    method: str = "rank",
    log1p: bool = False,
    good: float | None = None,
    bad: float | None = None,
    scale_max: float = 1.0,
    group: pl.Expr | None = None,
) -> pl.Expr:
    """Raw indicator values → 0–100 where higher is always better.

    ``group`` only affects ``rank``: the percentile is then taken within each group.
    """
    if direction not in ("higher_better", "lower_better"):
        raise ValueError(f"direction must be higher_better or lower_better, not {direction!r}")
    if method == "rank":
        v = values.log1p() if log1p else values
        return percentile_rank(-v if direction == "lower_better" else v, group)
    if method == "scale":
        score = (values / scale_max * 100).clip(0, 100)
        # neg().add() rather than 100 - score, which would rename the column "literal"
        return score.neg().add(100) if direction == "lower_better" else score
    if method == "threshold":
        if good is None or bad is None or good == bad:
            raise ValueError("threshold needs distinct good and bad levels")
        if (direction == "lower_better") != (good < bad):
            raise ValueError(f"good/bad ({good}, {bad}) contradict direction {direction}")
        if log1p:
            v, g, b = values.log1p(), math.log1p(good), math.log1p(bad)
        else:
            v, g, b = values, good, bad
        return ((v - b) / (g - b) * 100).clip(0, 100)
    raise ValueError(f"Unknown normalisation method {method!r}")


def weighted_score(
    df: pl.DataFrame,
    weights: Mapping[str, float],
    min_coverage: float = MIN_THEME_COVERAGE,
    applicable: Mapping[str, pl.Series] | None = None,
) -> tuple[pl.Series, pl.Series]:
    """Weighted mean of 0–100 columns, ignoring nulls; returns (score, coverage).

    ``score`` is null where the available weight share is below ``min_coverage``.
    Zero-weight columns are ignored entirely. ``applicable`` gives, per column, which
    rows it applies to (a nation's sources): the row's total weight counts only those
    columns, so an area is not marked thin for indicators its nation cannot have. A row
    with no applicable weight scores null with coverage 0.
    """
    active = {c: w for c, w in weights.items() if w > 0}
    if not active:
        raise ValueError("At least one weight must be positive")
    masks = applicable or {}
    frame = df.with_columns(
        [
            pl.Series(f"__applies_{c}", masks[c])
            if c in masks
            else pl.lit(True).alias(f"__applies_{c}")
            for c in active
        ]
    )

    def applies(c: str) -> pl.Expr:
        return pl.col(f"__applies_{c}").cast(pl.Float64)

    total = pl.sum_horizontal(applies(c) * w for c, w in active.items())
    num = pl.sum_horizontal(pl.col(c).fill_null(0) * applies(c) * w for c, w in active.items())
    avail = pl.sum_horizontal(
        pl.col(c).is_not_null().cast(pl.Float64) * applies(c) * w for c, w in active.items()
    )
    share = pl.when(total > 0).then(avail / total).otherwise(0.0)
    out = frame.select(
        pl.when((total > 0) & (share >= min_coverage)).then(num / avail).alias("score"),
        share.alias("coverage"),
    )
    return out["score"], out["coverage"]


def percentile_of(scores: pl.Series, group: pl.Series | None = None) -> pl.Series:
    """Percentile (0–100) of a score column, keeping nulls; within ``group`` if given."""
    if group is None:
        return pl.DataFrame({"s": scores}).select(percentile_rank(pl.col("s")))["s"]
    df = pl.DataFrame({"s": scores, "g": group})
    return df.select(percentile_rank(pl.col("s"), pl.col("g")))["s"]


def shown_percentile(percentile: pl.Expr) -> pl.Expr:
    """The percentile as people read it: a whole number (halves round up, as the browser's
    ``Math.round`` does), and the top area is 99, never 100."""
    return (percentile + 0.5).floor().clip(0, 99)


def band(percentile: pl.Expr) -> pl.Expr:
    """Consumer-facing band from a percentile: 1 (bottom fifth of the headline's base) … 5
    (top fifth), derived from the percentile as shown so "better than 80%" never sits
    beside "fourth fifth"."""
    return (shown_percentile(percentile) / 20).floor().clip(0, 4).cast(pl.Int8) + 1


def shown_percentile_of(percentile: float | None) -> float | None:
    """``shown_percentile`` for one value (the API's cards)."""
    if percentile is None or percentile != percentile:
        return None
    return float(min(99, math.floor(percentile + 0.5)))


def band_of(percentile: float | None) -> int | None:
    """``band`` for one value: the fifth the shown percentile falls in."""
    shown = shown_percentile_of(percentile)
    return None if shown is None else min(int(shown // 20), 4) + 1


def _theme_applies(
    masks: Mapping[str, pl.Series] | None, active: Sequence[str], height: int
) -> pl.Series:
    """Per row, whether a theme applies: any of its weighted indicators does (an indicator
    without a mask applies everywhere)."""
    if masks is None or any(c not in masks for c in active):
        return pl.Series([True] * height, dtype=pl.Boolean)
    if not active:
        return pl.Series([False] * height, dtype=pl.Boolean)
    return pl.DataFrame({c: masks[c] for c in active}).select(pl.any_horizontal(pl.all()))[:, 0]


def score_lsoas(
    norms: pl.DataFrame,
    indicators: list[tuple[str, str, float]],
    theme_weights: Mapping[str, float],
    indicator_multipliers: Mapping[str, float] | None = None,
    min_coverage: float = MIN_THEME_COVERAGE,
    group: str | Sequence[str] | None = None,
    applicable: Mapping[str, Collection[str]] | None = None,
    theme_gaps: Mapping[str, Collection[str]] | None = None,
    nation: str = "nation",
) -> pl.DataFrame:
    """Theme and overall scores from per-indicator 0–100 scores.

    ``norms``: one row per LSOA with a ``n__{indicator_id}`` column for each scored
    indicator. ``indicators``: (id, theme, base weight) for the scored ones.
    Returns per theme ``theme__{t}`` (score), ``theme_pct__{t}`` (percentile) and
    ``theme_coverage__{t}``; then ``overall``, ``overall_pct`` and ``coverage``.
    With ``group`` (a column of ``norms``, e.g. ``nation``, or several such columns), each
    percentile is also taken within the group: ``theme_pct_{group}__{t}`` and
    ``overall_pct_{group}``.

    ``applicable`` (indicator id → the nations it is built for) makes each row's theme
    total the weight its nation can have, read from the ``nation`` column, so a Scottish
    area scores its safety from the indicators Scotland has rather than reading as thin;
    a theme with no applicable weight is null and left out of the row's overall, and so is
    a theme listed in ``theme_gaps`` (nation → themes the nation declares it cannot cover).
    A theme the nation can have but the row has no data for stays in the row's overall
    denominator with coverage 0, so an area missing most of its themes fails the gate
    instead of taking a band from the one or two it has.
    """
    mult = indicator_multipliers or {}
    groups = [g for g in ([group] if isinstance(group, str) else list(group or [])) if g]
    within = {g: norms[g] for g in groups}
    nations = norms[nation] if (applicable or theme_gaps) else None
    if (applicable or theme_gaps) and nations is None:
        raise ValueError(f"applicable/theme_gaps need the {nation!r} column")
    columns: dict[str, pl.Series] = {}
    theme_masks: dict[str, pl.Series] = {}
    themes = sorted({theme for _, theme, _ in indicators})
    for theme in themes:
        weights = {f"n__{iid}": w * mult.get(iid, 1.0) for iid, t, w in indicators if t == theme}
        if not any(w > 0 for w in weights.values()):
            continue
        masks = None
        if applicable:
            masks = {
                f"n__{iid}": nations.is_in(list(applicable[iid]))
                for iid, t, _ in indicators
                if t == theme and iid in applicable
            }
        score, coverage = weighted_score(norms, weights, min_coverage, masks)
        gap = None
        if theme_gaps:
            gapped = [n for n, ts in theme_gaps.items() if theme in ts]
            if gapped:
                gap = nations.is_in(gapped)
                score = pl.DataFrame({"s": score, "g": gap}).select(
                    pl.when(pl.col("g")).then(None).otherwise(pl.col("s")).alias("s")
                )["s"]
                coverage = pl.DataFrame({"c": coverage, "g": gap}).select(
                    pl.when(pl.col("g")).then(0.0).otherwise(pl.col("c")).alias("c")
                )["c"]
        # The theme counts towards a row's overall wherever the nation can have it, whether
        # or not this row has data for it: a theme that applies but is empty pulls the
        # row's coverage down (and past the gate, its overall out), while a theme the
        # nation cannot have (no applicable indicator, or a declared gap) is left out
        applies = _theme_applies(masks, [c for c, w in weights.items() if w > 0], norms.height)
        theme_masks[f"theme__{theme}"] = applies if gap is None else (applies & ~gap)
        columns[f"theme__{theme}"] = score
        columns[f"theme_pct__{theme}"] = percentile_of(score)
        for g, members in within.items():
            columns[f"theme_pct_{g}__{theme}"] = percentile_of(score, members)
        columns[f"theme_coverage__{theme}"] = coverage
    themes_df = pl.DataFrame(columns)
    overall, coverage = weighted_score(
        themes_df,
        {f"theme__{t}": theme_weights.get(t, 0.0) for t in themes if f"theme__{t}" in columns},
        min_coverage,
        theme_masks if (applicable or theme_gaps) else None,
    )
    out = themes_df.with_columns(overall=overall, overall_pct=percentile_of(overall))
    for g, members in within.items():
        out = out.with_columns(pl.Series(f"overall_pct_{g}", percentile_of(overall, members)))
    return out.with_columns(coverage=coverage)


def headline_within(
    scores: pl.DataFrame, nations: pl.Series, rule: Mapping[str, str]
) -> pl.DataFrame:
    """The headline percentiles after each nation's rule: where ``rule[nation]`` is
    ``"nation"``, ``overall_pct`` and every ``theme_pct__*`` become the within-nation
    percentile (``overall_pct_nation``, ``theme_pct_nation__*``), elsewhere they keep the
    build-wide value; the build-wide values stay as ``overall_pct_all`` and
    ``theme_pct_all__*``, and ``headline_within`` ("all" | "nation") says per row which
    base the headline uses. A nation with too little comparable data opts in through
    ``headline_within: nation`` in config/nations.yaml (#147).
    """
    within = [n for n, r in rule.items() if r == "nation"]
    own = pl.Series("__own", nations.is_in(within)) if within else None
    out = scores.with_columns(
        pl.Series("headline_within", ["nation" if o else "all" for o in own])
        if own is not None
        else pl.lit("all").alias("headline_within")
    )
    pairs = [("overall_pct", "overall_pct_nation", "overall_pct_all")] + [
        (c, f"theme_pct_nation__{c.removeprefix('theme_pct__')}",
         f"theme_pct_all__{c.removeprefix('theme_pct__')}")
        for c in scores.columns
        if c.startswith("theme_pct__")
    ]  # fmt: skip
    for headline, nation_col, all_col in pairs:
        if nation_col not in scores.columns:
            raise ValueError(f"headline_within needs {nation_col!r} (score with group='nation')")
        out = out.with_columns(pl.col(headline).alias(all_col))
        if own is not None:
            out = out.with_columns(
                pl.when(own).then(pl.col(nation_col)).otherwise(pl.col(headline)).alias(headline)
            )
    return out
