"""State shared between the assistant and the page over AG-UI.

The page sends it with every run (so the assistant sees slider moves, the selected
area and the shortlist); tools that change it push a snapshot back, and the map,
weight sliders and shortlist re-render from it.
"""

from typing import Literal

from pydantic import BaseModel, Field

from lix_api.models import Poi, Point


class MapView(BaseModel):
    bbox: tuple[float, float, float, float] | None = Field(
        None, description="west, south, east, north"
    )
    layer: str = Field("overall", description='"overall", "theme:<theme>" or "indicator:<id>"')
    highlighted: list[str] = Field(default_factory=list, description="LSOA/MSOA codes to outline")
    selected: str | None = Field(None, description="LSOA code of the open area profile")
    pois: list[Poi] = Field(default_factory=list, description="Markers to show")


class ShortlistItem(BaseModel):
    code: str
    name: str
    note: str | None = None
    centre: Point | None = None


class ProfileRef(BaseModel):
    """The generated weighting profile the sliders start from, as the page keeps it."""

    name: str = Field(max_length=40)
    description: str = Field(
        "", max_length=500, description="What the user typed; kept on the page, never stored"
    )
    rationale: str = Field("", max_length=200)


class LiveabilityState(BaseModel):
    preset: str = "balanced"
    locale: Literal["en", "cy", "gd", "ga"] = Field(
        "en", description="The page's language; the assistant replies in it"
    )
    theme_weights: dict[str, float] = Field(
        default_factory=dict, description="Overrides on top of the preset's theme weights"
    )
    indicator_weights: dict[str, float] = Field(
        default_factory=dict, description="Multipliers on individual indicators"
    )
    profile: ProfileRef | None = Field(
        None, description="The generated profile the weights come from, if any"
    )
    compare_within: Literal["uk", "nation", "urban_rural"] = Field(
        "uk", description="What percentiles on the map compare against"
    )
    # Kept one release for older pages; compare_within replaces it
    compare_within_urban_rural: bool = False
    mode: Literal["consumer", "analyst"] = "consumer"
    map: MapView = Field(default_factory=MapView)
    shortlist: list[ShortlistItem] = Field(default_factory=list)
