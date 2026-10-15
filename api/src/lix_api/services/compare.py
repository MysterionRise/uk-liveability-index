"""Side-by-side comparison of 2–5 areas (LSOAs, or MSOAs / local authorities as averages)."""

import polars as pl

from lix_api.models import ComparedArea, Comparison
from lix_api.services.areas import stand_in
from lix_api.services.names import localised
from lix_api.services.scoring import ThemeWeights, percentile, resolve_weights, scores_for
from lix_api.services.search import resolve_lsoa, search_place
from lix_api.store import Store

COMPARE_INDICATORS = [
    "house_price", "crime_violence", "crime_burglary", "no2", "gp_distance",
    "patients_per_gp", "primary_school_quality", "well_run_pubs", "supermarket_distance",
    "connectivity_overall", "income_deprivation", "population_density",
]  # fmt: skip


def compare_areas(
    store: Store,
    refs: list[str],
    preset: str | None = None,
    theme_weights: ThemeWeights | None = None,
    locale: str | None = None,
) -> Comparison:
    """``locale`` names the areas in the interface language where the pack has it."""
    if not 2 <= len(refs) <= 5:
        raise ValueError("Compare between 2 and 5 areas")
    name, themes, multipliers = resolve_weights(store, preset, theme_weights)
    scored = pl.concat(
        [store.base_features, scores_for(store, themes, multipliers).drop("lsoa21cd")],
        how="horizontal",
    )
    requested = [i for i in COMPARE_INDICATORS if i in store.indicators]
    picked: list[
        tuple[
            str,
            str,
            str,
            float | None,
            float | None,
            dict[str, float | None],
            dict[str, tuple[str | None, float | None]],
        ]
    ] = []
    for ref in refs:
        place = (search_place(store, ref, limit=1) or [None])[0]
        if place and place.kind in ("msoa", "lad"):
            column, name_col = {"msoa": ("msoa21cd", "msoa_name"), "lad": ("lad_cd", "lad_nm")}[
                place.kind
            ]
            rows = scored.filter(pl.col(column) == place.code)
            level, code = place.kind, place.code
            label = localised(rows.row(0, named=True), name_col, locale) or place.name
        elif place and place.kind == "place" and place.lsoa21cd:
            # A named suburb or village means its neighbourhood, not one LSOA of it
            home = scored.filter(pl.col("lsoa21cd") == place.lsoa21cd).row(0, named=True)
            code, level = home["msoa21cd"], "msoa"
            rows = scored.filter(pl.col("msoa21cd") == code)
            neighbourhood = localised(home, "msoa_name", locale)
            same = neighbourhood.lower().startswith(place.name.lower())
            label = neighbourhood if same else f"{place.name} ({neighbourhood})"
        else:
            code = resolve_lsoa(store, ref)
            rows = scored.filter(pl.col("lsoa21cd") == code)
            r = rows.row(0, named=True)
            own_tier = r["msoa21cd"] == r["lsoa21cd"]
            label = (
                localised(r, "lsoa21nm", locale)
                if own_tier
                else f"{localised(r, 'lsoa21nm', locale)} ({localised(r, 'msoa_name', locale)})"
            )
            level = "lsoa"
        w = rows["population"].cast(pl.Float64)

        def wmean(col: str) -> float | None:
            vals = rows[col]
            ok = vals.is_not_null()
            if not ok.any():
                return None
            return round(float((vals.filter(ok) * w.filter(ok)).sum() / w.filter(ok).sum()), 2)

        # What each nation lacks is flagged on every one of its rows alike, so the
        # first row says which indicator (or substitute) this area can show
        first = rows.row(0, named=True)
        shown = {}
        for i in requested:
            pick = stand_in(store, first, i)
            shown[i] = (pick, wmean(f"raw__{pick}") if pick else None)
        picked.append((
            code, label, level, wmean("overall"),
            percentile(wmean("overall_pct")) if level == "lsoa" else None,
            {t: wmean(f"theme__{t}") for t in store.themes}, shown,
        ))  # fmt: skip

    # One row per indicator any area shows: the usual one, and a substitute beside it
    # when another area's nation lacks the usual one
    rows_shown: list[str] = []
    stand_ins: dict[str, str] = {}
    for i in requested:
        for *_, shown in picked:
            pick = shown[i][0]
            if pick and pick not in rows_shown:
                rows_shown.append(pick)
            if pick and pick != i:
                # The first requested indicator a substitute replaces names the row
                stand_ins.setdefault(pick, i)
    areas = []
    for code, label, level, overall, pct, themes_, shown in picked:
        values = {pick: value for pick, value in shown.values() if pick}
        areas.append(
            ComparedArea(
                code=code,
                name=label,
                level=level,
                overall=overall,
                overall_percentile=pct,
                themes=themes_,
                indicators={i: values.get(i) for i in rows_shown},
            )  # fmt: skip
        )
    return Comparison(
        preset=name,
        areas=areas,
        indicator_labels={i: store.indicators[i].label for i in rows_shown},
        theme_labels={t: s.label for t, s in store.themes.items()},
        stand_ins=stand_ins,
    )
