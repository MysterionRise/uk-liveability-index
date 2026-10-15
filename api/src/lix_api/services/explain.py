"""Why an area scores what it does: each theme's share of the score and its indicators."""

from lix_api.models import Contribution, Explanation, SourceRef
from lix_api.services.areas import indicator_value, with_substitutes
from lix_api.services.names import localised
from lix_api.services.scoring import (
    ThemeWeights,
    match_theme,
    percentile,
    resolve_weights,
    scores_for,
)
from lix_api.services.search import resolve_lsoa
from lix_api.store import Store


def explain_score(
    store: Store,
    ref: str,
    theme: str | None = None,
    preset: str | None = None,
    theme_weights: ThemeWeights | None = None,
    locale: str | None = None,
) -> Explanation:
    """``locale`` names the area in the interface language where the pack has it."""
    code = resolve_lsoa(store, ref)
    i = store.lsoa_index[code]
    row = store.features.row(i, named=True)
    name, themes, multipliers = resolve_weights(store, preset, theme_weights)
    scores = scores_for(store, themes, multipliers).row(i, named=True)

    if theme is not None:
        matched = match_theme(store, theme)
        if matched is None:
            raise LookupError(f"Unknown theme {theme!r}; themes: {', '.join(store.themes)}")
        theme = matched
    available = {t: w for t, w in themes.items() if scores.get(f"theme__{t}") is not None}
    total = sum(w for w in available.values() if w > 0) or 1.0
    contributions = []
    for t, spec in store.themes.items():
        if theme and t != theme:
            continue
        share = max(available.get(t, 0.0), 0.0) / total
        score = scores.get(f"theme__{t}")
        indicators = with_substitutes(
            store,
            row,
            [
                indicator_value(store, row, ind.id)
                for ind in store.indicators.values()
                if ind.theme == t and ind.role in ("scored", "context")
            ],
        )
        contributions.append(
            Contribution(
                theme=t,
                label=spec.label,
                weight_share=round(share, 3),
                score=None if score is None else round(score, 1),
                contribution=None if score is None else round(share * score, 1),
                indicators=indicators,
            )  # fmt: skip
        )
    contributions.sort(key=lambda c: c.contribution or 0, reverse=True)

    # The sources behind this area's nation: an indicator lists every nation's files
    known = store.manifest["sources"]
    nation = row.get("nation")

    def covers(slug: str) -> bool:
        coverage = known.get(slug, {}).get("coverage")
        return not coverage or nation is None or nation in coverage

    used = {
        s
        for c in contributions
        for v in c.indicators
        for s in store.indicators[v.id].sources
        if covers(s)
    }
    caveats = sorted(
        {store.indicators[v.id].caveats for c in contributions for v in c.indicators
         if store.indicators[v.id].caveats and v.role == "scored"}
    )  # fmt: skip
    sources = [
        SourceRef(
            id=s,
            title=known[s]["title"],
            licence=known[s]["licence"],
            attribution=known[s]["attribution"],
            version=None if known[s].get("version") is None else str(known[s]["version"]),
            fetched_at=known[s].get("fetched_at"),
            period=known[s].get("period"),
            stale=bool(known[s].get("stale")),
            stale_reason=known[s].get("stale_reason")
            or ("older_than_cadence" if known[s].get("stale") else "none"),
        )
        for s in sorted(used)
        if s in known
    ]
    return Explanation(
        lsoa21cd=code,
        name=localised(row, "lsoa21nm", locale)
        if row["msoa21cd"] == row["lsoa21cd"]
        else f"{localised(row, 'lsoa21nm', locale)} ({localised(row, 'msoa_name', locale)})",
        preset=name,
        overall=None if scores.get("overall") is None else round(scores["overall"], 1),
        overall_percentile=percentile(scores.get("overall_pct")),
        contributions=contributions, caveats=caveats, sources=sources,
    )  # fmt: skip
