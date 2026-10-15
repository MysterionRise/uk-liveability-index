"""What the index measures: indicators, themes, presets and sources."""

from lix_api.models import IndicatorBrief, IndicatorInfo
from lix_api.store import Store


def list_indicators(
    store: Store, theme: str | None = None, detail: bool = True
) -> list[IndicatorInfo] | list[IndicatorBrief]:
    """Every indicator (or one theme's): the full card, or without ``detail`` the name
    card alone (id, label, theme, unit, role, coverage), a tenth of the size."""
    if not detail:
        return [
            IndicatorBrief(
                id=i.id,
                theme=i.theme,
                label=i.label,
                unit=i.unit,
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
            label=i.label,
            description=i.description,
            unit=i.unit,
            direction=i.direction,
            role=i.role,
            normalise=i.normalise,
            weight=i.weight,
            unit_code=i.unit_code,
            benchmark=i.benchmark,
            coverage=i.coverage or [],
            caveats=i.caveats,
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
