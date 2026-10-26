"""One area's profile: scores, strengths, weaknesses, key facts and caveats."""

import polars as pl

from lix_api.models import AreaProfile, IndicatorValue, Point, ThemeScore
from lix_api.services.names import english_if_different, localised, ruc_label
from lix_api.services.scoring import ThemeWeights, percentile, resolve_weights, scores_for
from lix_api.services.search import resolve_lsoa
from lix_api.store import Store
from lix_core.scoring import band_of


def _covered(store: Store, values: list[IndicatorValue]) -> set[str]:
    """Indicators absent here but stood in for: a substitute with a value covers the
    indicators it replaces, and a replaced indicator with a value covers its substitute
    (the SIMD crime rate for the police.uk crimes in Scotland, and the reverse elsewhere)."""
    present = {v.id for v in values if v.quality != "not_available"}
    covered: set[str] = set()
    for spec in store.scored:
        for target in spec.substitute_for:
            if spec.id in present:
                covered.add(target)
            if target in present:
                covered.add(spec.id)
    return covered


def stand_in(store: Store, row: dict, indicator_id: str) -> str | None:
    """The indicator to show for ``indicator_id`` in this area: itself where its nation
    has it, else a scored substitute (either way round) the nation has, else None."""

    def available(i: str) -> bool:
        return row.get(f"q__{i}") != "not_available"

    if available(indicator_id):
        return indicator_id
    spec = store.indicators[indicator_id]
    for other in store.scored:
        pair = indicator_id in other.substitute_for or other.id in spec.substitute_for
        if pair and other.id != indicator_id and available(other.id):
            return other.id
    return None


def fact(store: Store, row: dict, indicator_id: str) -> IndicatorValue:
    """``indicator_id``'s value here, or its substitute's marked as standing in."""
    shown = stand_in(store, row, indicator_id) or indicator_id
    value = indicator_value(store, row, shown)
    if shown != indicator_id:
        value.stands_in_for = indicator_id
    return value


def with_substitutes(store: Store, row: dict, values: list[IndicatorValue]) -> list[IndicatorValue]:
    """A list of an area's indicator values with the substitutes resolved: a value the
    nation lacks is dropped when its substitute (either way round) is in the list, and
    a substitute says what it stands in for."""
    covered = _covered(store, values)
    out = []
    for v in values:
        if v.quality == "not_available" and v.id in covered:
            continue
        if v.quality != "not_available":
            replaced = [
                t for t in store.indicators[v.id].substitute_for
                if row.get(f"q__{t}") == "not_available"
            ]  # fmt: skip
            if replaced:
                v = v.model_copy(update={"stands_in_for": replaced[0]})
        out.append(v)
    return out


KEY_FACTS = ["house_price", "council_tax", "crime_violence", "no2", "noise_exposure",
             "flood_risk", "tree_canopy", "sunshine", "gp_distance", "station_distance",
             "everyday_services", "gigabit_broadband", "well_run_pubs",
             "households_with_children"]  # fmt: skip

# The flags an area can carry, worded in agent.messages (four languages)
FLAG_CODES = ("imputed", "low_n", "broadcast_lad", "not_available", "theme_gap")


def flag_texts(store: Store, codes: set[str], nation: str, locale: str | None) -> list[str]:
    """The flags' texts in the interface language; the theme-gap one names the themes."""
    from lix_api.agent.messages import message

    lang = (locale or "en").split("-")[0]
    labels = ((store.manifest.get("i18n") or {}).get(lang) or {}).get("themes") or {}
    gaps = [
        (labels.get(t) or {}).get("label") or store.themes[t].label
        for t in store.theme_gaps.get(nation, [])
        if t in store.themes
    ]
    out = []
    for code in FLAG_CODES:
        if code not in codes:
            continue
        themes = ", ".join(gaps) if gaps else "a theme"
        out.append(message(f"flag_{code}", lang, themes=themes))
    return out


def nation_name_in(store: Store, nation: str, locale: str | None) -> str:
    """The nation's name in the interface language where the pack carries it."""
    lang = (locale or "en").split("-")[0]
    names = (store.geography["nations"].get(nation) or {}).get("names") or {}
    return names.get(lang) or store.nation_names.get(nation, nation)


def _round(v: float | None, digits: int = 1) -> float | None:
    return None if v is None else round(v, digits)


_pct = percentile


def indicator_value(store: Store, row: dict, indicator_id: str) -> IndicatorValue:
    spec = store.indicators[indicator_id]
    return IndicatorValue(
        id=spec.id, label=spec.label, theme=spec.theme, unit=spec.unit,
        value=_round(row.get(f"raw__{spec.id}"), 3), score=_round(row.get(f"n__{spec.id}")),
        quality=str(row.get(f"q__{spec.id}") or "ok"), role=spec.role, benchmark=spec.benchmark,
        unit_code=spec.unit_code,
    )  # fmt: skip


def area_profile(
    store: Store,
    ref: str,
    preset: str | None = None,
    theme_weights: ThemeWeights | None = None,
    locale: str | None = None,
    indicator_weights: dict[str, float] | None = None,
) -> AreaProfile:
    code = resolve_lsoa(store, ref)
    row = store.features.row(store.lsoa_index[code], named=True)
    name, themes, multipliers = resolve_weights(store, preset, theme_weights, indicator_weights)
    every = scores_for(store, themes, multipliers)
    scores = every.row(store.lsoa_index[code], named=True)
    local = every.filter(store.features["lad_cd"] == row["lad_cd"])
    nation = every.filter(store.features["nation"] == row["nation"])

    def median(df: pl.DataFrame, col: str) -> float | None:
        return _round(df[col].median()) if col in df.columns else None

    theme_scores = [
        ThemeScore(
            theme=t,
            label=spec.label,
            score=_round(scores.get(f"theme__{t}")),
            percentile=_pct(scores.get(f"theme_pct__{t}")),
            percentile_nation=_pct(scores.get(f"theme_pct_nation__{t}")),
            percentile_urban_rural=_pct(scores.get(f"theme_pct_ruc_class__{t}")),
            percentile_all=_pct(scores.get(f"theme_pct_all__{t}", scores.get(f"theme_pct__{t}"))),
            country_median=median(every, f"theme__{t}"),
            nation_median=median(nation, f"theme__{t}"),
            local_median=median(local, f"theme__{t}"),
        )  # fmt: skip
        for t, spec in store.themes.items()
    ]
    scored = [indicator_value(store, row, i.id) for i in store.scored]
    ranked = sorted((v for v in scored if v.score is not None), key=lambda v: v.score)
    unavailable = [
        v for v in scored if v.quality == "not_available" and v.id not in _covered(store, scored)
    ]
    # Each caveat is tied to the indicator it is about: council tax is council-wide
    # everywhere by nature and needs no caveat, Welsh school quality does
    flag_codes = sorted(
        (
            {"imputed"}
            if any(v.quality == "imputed" and v.id.startswith("crime_") for v in scored)
            else set()
        )
        | ({"low_n"} if row.get("q__house_price") == "low_n" else set())
        | (
            {"broadcast_lad"}
            if row.get("q__secondary_school_quality") == "broadcast_lad"
            else set()
        )
        | ({"not_available"} if unavailable else set())
        | (
            {"theme_gap"}
            if any(
                scores.get(f"theme__{t}") is None for t in store.theme_gaps.get(row["nation"], [])
            )
            else set()
        )
    )
    flags = flag_texts(store, flag_codes, row["nation"], locale)
    pct = scores.get("overall_pct")
    # Precomputed per preset at build time; custom weights have no range
    preset_id = None if (theme_weights or indicator_weights) else name
    lo, hi = row.get(f"pct_lo__{preset_id}"), row.get(f"pct_hi__{preset_id}")
    return AreaProfile(
        lsoa21cd=code,
        lsoa_name=localised(row, "lsoa21nm", locale),
        # A nation whose small areas are their own mid tier (Northern Ireland) has no
        # separate neighbourhood to name
        neighbourhood=localised(row, "msoa_name", locale) if row["msoa21cd"] != code else None,
        neighbourhood_en=english_if_different(row, "msoa_name", locale)
        if row["msoa21cd"] != code
        else None,
        msoa21cd=row["msoa21cd"],
        local_authority=localised(row, "lad_nm", locale),
        local_authority_en=english_if_different(row, "lad_nm", locale),
        region=row["rgn_nm"],
        nation=row["nation"],
        nation_name=nation_name_in(store, row["nation"], locale),
        urban_rural=row["ruc21nm"],
        urban_rural_label=ruc_label(store, row, locale),
        population=row["population"],
        overall=_round(scores.get("overall")),
        overall_percentile=_pct(pct),
        overall_percentile_nation=_pct(scores.get("overall_pct_nation")),
        overall_percentile_urban_rural=_pct(scores.get("overall_pct_ruc_class")),
        overall_percentile_all=_pct(scores.get("overall_pct_all", pct)),
        percentile_within=scores.get("headline_within") or "all",
        band=band_of(pct),
        overall_percentile_range=None if lo is None or hi is None else [_pct(lo), _pct(hi)],
        coverage=_round(scores.get("coverage"), 2),
        preset=name,
        themes=theme_scores,
        strengths=list(reversed(ranked[-3:])),
        weaknesses=ranked[:3],
        key_facts=[fact(store, row, i) for i in KEY_FACTS if i in store.indicators],
        flags=flags,
        flag_codes=flag_codes,
        centre=Point(lat=row["pwc_lat"], lon=row["pwc_lon"]),
        bbox=(row["bbox_w"], row["bbox_s"], row["bbox_e"], row["bbox_n"]),
    )


def lsoas_in(store: Store, level: str, code: str) -> pl.Series:
    column = {
        "msoa": "msoa21cd", "lad": "lad_cd", "region": "rgn_cd", "nation": "ctry_cd",
        "lsoa": "lsoa21cd",
    }[level]  # fmt: skip
    return store.features.filter(pl.col(column) == code)["lsoa21cd"]
