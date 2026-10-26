"""Response models shared by the REST API, the agent's tools and the MCP server.

These are the contract with the front end: `make schemas` exports them as JSON Schema
and TypeScript types, so the UI renders exactly what the tools return.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

Level = Literal["lsoa", "msoa", "lad", "region", "nation"]
Locale = Literal["en", "cy", "gd", "ga"]
Weight = Annotated[float, Field(ge=0, le=3, allow_inf_nan=False)]
# A request's sliders and multipliers: finite, in the sliders' range, keyed by id or label
ThemeWeightMap = dict[str, Weight]
IndicatorWeightMap = dict[str, Weight]
Multiplier = Annotated[float, Field(ge=0.25, le=3, allow_inf_nan=False)]
ProfileEngine = Literal["model", "rules"]
EngineReason = Literal["free_model", "no_key", "budget", "model_error"]
PlaceKind = Literal[
    "postcode",
    "postcode_district",
    "postcode_sector",
    "place",
    "lsoa",
    "msoa",
    "lad",
    "region",
    "nation",
]


class Point(BaseModel):
    lat: float
    lon: float


class Place(BaseModel):
    """A search result: something a user might mean by a name, postcode or code."""

    kind: PlaceKind
    name: str = Field(description="The form that matched (Welsh or Gaelic when the query was)")
    name_en: str | None = Field(
        None, description="The English name when `name` is another language's"
    )
    detail: str | None = Field(None, description="Disambiguation, e.g. the local authority")
    place_type: str | None = Field(
        None,
        description="A place's settlement type as the gazetteer words it (City, Town, Village…)",
    )
    code: str | None = None
    lsoa21cd: str | None = None
    centre: Point | None = None
    bbox: tuple[float, float, float, float] | None = Field(
        None, description="west, south, east, north"
    )


class ThemeScore(BaseModel):
    theme: str
    label: str
    score: float | None = Field(None, description="0–100, higher is better")
    percentile: float | None = Field(
        None,
        description="Better than this % of the neighbourhoods the headline compares against "
        "(see percentile_within)",
    )
    percentile_nation: float | None = Field(
        None, description="Better than this % of the neighbourhoods in the same nation"
    )
    percentile_urban_rural: float | None = Field(
        None, description="Better than this % of the neighbourhoods of the same urban/rural class"
    )
    percentile_all: float | None = Field(
        None,
        description="Better than this % of every neighbourhood in the build, whatever the "
        "nation's headline rule (equals `percentile` unless the nation compares within itself)",
    )
    country_median: float | None = Field(None, description="Median score across the build")
    nation_median: float | None = Field(None, description="Median score across the nation")
    local_median: float | None = Field(None, description="Median score in the local authority")


class IndicatorValue(BaseModel):
    id: str
    label: str
    theme: str
    unit: str
    unit_code: str = Field("", description="The unit's code, for formatting and translation")
    value: float | None = Field(None, description="Raw value in `unit`")
    score: float | None = Field(None, description="0–100, higher is better")
    quality: str = "ok"
    role: str = "scored"
    benchmark: Literal["uk", "nation"] = Field(
        "uk",
        description="uk: ranked against every area in the build (the UK and Ireland) · "
        "nation: within its nation",
    )
    stands_in_for: str | None = Field(
        None,
        description="The indicator this one replaces here: a substitute shown because the "
        "nation lacks the usual one (the SIMD crime rate for police.uk crime in Scotland)",
    )


class AreaProfile(BaseModel):
    lsoa21cd: str
    lsoa_name: str
    neighbourhood: str | None = Field(
        None, description="Friendly mid-area name, in the locale asked for"
    )
    neighbourhood_en: str | None = Field(
        None, description="Its English name when `neighbourhood` is another language's"
    )
    msoa21cd: str
    local_authority: str = Field(description="In the locale asked for")
    local_authority_en: str | None = Field(None, description="Its English name when it differs")
    region: str
    nation: str = Field(description="Nation code: E, W, S, N or I (Ireland)")
    nation_name: str
    urban_rural: str
    urban_rural_label: str | None = Field(
        None, description="The urban/rural class in the requested locale (English when none)"
    )
    population: int
    overall: float | None
    overall_percentile: float | None
    overall_percentile_nation: float | None = Field(
        None, description="Percentile among the neighbourhoods of the same nation"
    )
    overall_percentile_urban_rural: float | None = Field(
        None, description="Percentile among the neighbourhoods of the same urban/rural class"
    )
    band: int | None = Field(
        None, description="1 (bottom fifth of the headline's base, see percentile_within) … 5 (top)"
    )
    percentile_within: Literal["all", "nation"] = Field(
        "all",
        description="What overall_percentile and band compare against: every neighbourhood in "
        "the build, or the area's own nation where that nation's data is thinner "
        "(headline_within in the manifest)",
    )
    overall_percentile_all: float | None = Field(
        None, description="Percentile among every neighbourhood in the build, whatever the rule"
    )
    overall_percentile_range: list[float] | None = Field(
        None,
        min_length=2,
        max_length=2,
        description="5–95% range of the build-wide percentile when each theme weight is "
        "nudged by about a quarter (for presets; None with custom weights)",
    )
    coverage: float | None
    preset: str
    themes: list[ThemeScore]
    strengths: list[IndicatorValue]
    weaknesses: list[IndicatorValue]
    key_facts: list[IndicatorValue]
    flags: list[str] = Field(default_factory=list, description="Caveats that apply to this area")
    flag_codes: list[str] = Field(
        default_factory=list,
        description="The caveats' codes (imputed, low_n, broadcast_lad, not_available, theme_gap)",
    )
    centre: Point
    bbox: tuple[float, float, float, float]


class RankedArea(BaseModel):
    rank: int
    level: Level
    code: str
    name: str = Field(description="In the locale asked for")
    name_en: str | None = Field(
        None, description="The English name when `name` is another language's"
    )
    local_authority: str
    overall: float | None
    overall_percentile: float | None
    percentile_within: Literal["all", "nation"] | None = Field(
        None, description="What overall_percentile compares against (see AreaProfile)"
    )
    themes: dict[str, float | None]
    median_price: float | None = None
    population: int
    centre: Point
    sort_value: float | None = Field(
        None, description="The ranked-by measure: the indicator's raw value or the theme score"
    )
    sort_score: float | None = Field(None, description="That measure as a 0–100 score")
    stability: float | None = Field(
        None,
        description="Share of plausible weightings (each theme weight nudged by about a "
        "quarter) under which this area stays in these top results, 0–1",
    )


class RankResult(BaseModel):
    level: Level
    within: str | None = Field(None, description="The area or place the ranking was limited to")
    preset: str
    theme_weights: dict[str, float]
    sort_by: str = Field(
        "overall",
        description='"overall", "theme:<id>" or "indicator:<id>": what the list is ranked by',
    )
    sort_label: str | None = Field(None, description="Label of that measure (English)")
    sort_unit: str | None = Field(None, description="Unit of sort_value for an indicator")
    direction: Literal["best", "worst"] = Field(
        "best", description="best: highest first · worst: lowest first"
    )
    place: str | None = Field(
        None, description="The area or place the ranking was limited to, named on its own"
    )
    radius_km: float | None = Field(
        None, description="Set when the scope is a radius around `place` rather than an area"
    )
    candidates: int = Field(description="Areas that passed the filters and have the measure")
    results: list[RankedArea]
    bbox: tuple[float, float, float, float] | None = None


class ComparedArea(BaseModel):
    code: str
    name: str
    level: Level
    overall: float | None
    overall_percentile: float | None
    percentile_within: Literal["all", "nation"] | None = Field(
        None, description="What overall_percentile compares against (see AreaProfile)"
    )
    themes: dict[str, float | None]
    indicators: dict[str, float | None] = Field(description="Raw indicator values")


class Comparison(BaseModel):
    preset: str
    areas: list[ComparedArea]
    indicator_labels: dict[str, str]
    theme_labels: dict[str, str]
    stand_ins: dict[str, str] = Field(
        default_factory=dict,
        description="Substitute indicator → the usual indicator it stands in for, where an "
        "area's nation lacks the usual one",
    )


class Poi(BaseModel):
    category: str
    name: str | None
    distance_m: float
    point: Point
    source: str
    licence: str
    detail: dict = Field(default_factory=dict)


class PoiResult(BaseModel):
    category: str
    origin: Point
    origin_label: str
    pois: list[Poi]


class Contribution(BaseModel):
    theme: str
    label: str
    weight_share: float = Field(description="Share of the overall weight, 0–1")
    score: float | None
    contribution: float | None = Field(None, description="weight_share × score")
    indicators: list[IndicatorValue]


class SourcePeriod(BaseModel):
    """The period a source's data describes (not when it was downloaded)."""

    label: str = Field(description="English wording, e.g. 'September 2023 to August 2026'")
    start: str | None = Field(None, description="First day of the period (ISO date)")
    end: str | None = Field(None, description="Last day of the period (ISO date)")
    kind: Literal["year", "month", "day", "range", "text"] = Field(
        "text", description="How to print the dates; 'text' means show the label"
    )


class SourceRef(BaseModel):
    id: str
    title: str
    licence: str
    attribution: str
    version: str | None = Field(None, description="The upstream version the build used")
    fetched_at: str | None = Field(None, description="When the build downloaded it (ISO 8601)")
    period: SourcePeriod | None = Field(None, description="What period the data is for")
    stale: bool = Field(False, description="The data may be out of date (see stale_reason)")
    stale_reason: Literal["newer_edition", "older_than_cadence", "none"] = Field(
        "none",
        description="A newer edition is published, or the download is older than the "
        "source's publishing cadence allows",
    )


class Explanation(BaseModel):
    lsoa21cd: str
    name: str
    preset: str
    overall: float | None
    overall_percentile: float | None
    percentile_within: Literal["all", "nation"] | None = Field(
        None, description="What overall_percentile compares against (see AreaProfile)"
    )
    contributions: list[Contribution]
    caveats: list[str]
    sources: list[SourceRef]


class IndicatorBrief(BaseModel):
    """An indicator's name card: enough to pick a sort_by or a theme."""

    id: str
    theme: str
    label: str
    unit: str
    role: str
    coverage: list[str] = Field(default_factory=list, description="Nation codes it is built for")


class IndicatorInfo(BaseModel):
    id: str
    theme: str
    label: str
    description: str
    unit: str
    direction: str
    role: str
    normalise: str
    weight: float
    unit_code: str = ""
    benchmark: Literal["uk", "nation"] = "uk"
    coverage: list[str] = Field(default_factory=list, description="Nation codes it is built for")
    caveats: str | None = None
    sources: list[str]


class ProfileDraft(BaseModel):
    """What the model returns for a description of the user; checked against the
    catalogue afterwards (ids, bounds, lengths), never trusted as is."""

    preset: str = Field(description="The closest base preset: one of the preset ids listed")
    theme_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Overrides 0–3 on the base preset, only for the themes the description "
        "changes (2 means twice as important as 1; 0 ignores a theme)",
    )
    indicator_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Multipliers 0.25–3 on single scored indicators the description singles "
        "out (2 means twice as important within its theme)",
    )
    name: str = Field(description="A short name for this profile, at most 40 characters")
    rationale: str = Field(
        description="One sentence, at most 200 characters, with no numbers, saying what was "
        "turned up or down and why"
    )


class WeightProfile(BaseModel):
    """A named weighting built from a description of the user: a base preset plus
    overrides, never a new preset of its own."""

    preset: str = Field(description="Base preset id")
    theme_weights: dict[str, Weight] = Field(
        default_factory=dict, description="Overrides on the preset's theme weights"
    )
    indicator_weights: dict[str, Multiplier] = Field(
        default_factory=dict, description="Multipliers on scored indicators"
    )
    name: str = Field(max_length=40)
    rationale: str = Field(max_length=200)
    locale: Locale = "en"
    engine: ProfileEngine = Field(description="model: the configured model · rules: word matching")
    engine_reason: EngineReason | None = Field(
        None, description="Why the rules engine was used instead of the model"
    )
