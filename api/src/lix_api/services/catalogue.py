"""What the index measures: indicators, themes, presets and sources."""

from lix_api.models import IndicatorBrief, IndicatorInfo
from lix_api.store import Store


def _in_locale(store: Store, locale: str | None):
    """The catalogue texts of ``locale`` (config/i18n through the manifest): label,
    description and caveats per indicator, and the unit by its code; English where a
    draft has none."""
    from lix_api.services.names import base_locale

    lang = base_locale(locale)
    cat = ((store.manifest.get("i18n") or {}).get(lang) or {}) if lang != "en" else {}
    entries, units = cat.get("indicators") or {}, cat.get("units") or {}

    def text(i, field: str):
        return (entries.get(i.id) or {}).get(field) or getattr(i, field)

    def unit(i):
        return units.get(i.unit_code) or i.unit

    return text, unit


def list_indicators(
    store: Store, theme: str | None = None, detail: bool = True, locale: str | None = None
) -> list[IndicatorInfo] | list[IndicatorBrief]:
    """Every indicator (or one theme's): the full card, or without ``detail`` the name
    card alone (id, label, theme, unit, role, coverage), a tenth of the size; labels,
    descriptions, caveats and units in ``locale`` where its catalogue has them, so the
    assistant quotes the drafted wording rather than translating the English (v0.7.0)."""
    text, unit = _in_locale(store, locale)
    if theme is not None:
        from lix_api.services.scoring import match_theme

        matched = match_theme(store, theme)
        if matched is None:
            raise LookupError(f"Unknown theme {theme!r}; themes are {sorted(store.themes)}")
        theme = matched
    if not detail:
        return [
            IndicatorBrief(
                id=i.id,
                theme=i.theme,
                label=text(i, "label"),
                unit=unit(i),
                role=i.role,
                coverage=i.coverage or [],
            )  # fmt: skip
            for i in store.indicators.values()
            if theme is None or i.theme == theme
        ]
    return [
        IndicatorInfo(
            id=i.id,
            theme=i.theme,
            label=text(i, "label"),
            description=text(i, "description"),
            unit=unit(i),
            direction=i.direction,
            role=i.role,
            normalise=i.normalise,
            weight=i.weight,
            unit_code=i.unit_code,
            benchmark=i.benchmark,
            coverage=i.coverage or [],
            caveats=text(i, "caveats") if i.caveats else None,
            sources=i.sources,
        )  # fmt: skip
        for i in store.indicators.values()
        if theme is None or i.theme == theme
    ]


def presets(store: Store) -> dict:
    return {
        "default": store.default_preset,
        "presets": {k: v.model_dump() for k, v in store.presets.items()},
        "themes": {k: v.model_dump() for k, v in store.themes.items()},
    }


def sources(store: Store) -> dict:
    return store.manifest["sources"]
