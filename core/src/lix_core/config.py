"""Loading and validation of the YAML files in ``config/``."""

from functools import lru_cache
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field

from lix_core.paths import get_project_root


def get_config(name: str) -> dict:
    """Load a YAML config file from config/{name}.yaml."""
    config_path = get_project_root() / "config" / f"{name}.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HttpAccess(_Strict):
    """A fixed URL.

    For files published under a dated name, put ``{date}`` in the URL and set
    ``date_format``; the resolver tries today and then each earlier day, up to
    ``lookback_days``, until a file exists.
    """

    type: Literal["http"]
    url: str
    date_format: str | None = None
    lookback_days: int = 7
    # Several files under one URL pattern: ``{tile}`` in the URL takes each value in turn
    # and each lands in data/raw/{slug}/{tile}.{format}
    tiles: list[str] | None = None
    # Seconds to wait for the server to start answering (a WFS export of a large layer
    # builds the whole file first)
    timeout_s: int = 60


class WfsAccess(_Strict):
    """A vector layer served over WFS 2.0.0 (GeoServer), fetched in pages.

    GeoServer stops a single GetFeature at its feature limit (a million on DataMapWales),
    so the layer is fetched ``page_size`` features at a time, sorted by ``sort_by`` for a
    stable order, into data/raw/{slug}/page_{n}.{format}. ``cql_filter`` keeps only the
    features wanted (the high and medium flood bands).
    """

    type: Literal["wfs"]
    url: str
    typename: str
    sort_by: str
    cql_filter: str | None = None
    page_size: int = 500_000
    srs: str = "EPSG:27700"
    timeout_s: int = 1800


class WcsAccess(_Strict):
    """A raster served over WCS 2.0.1 (Defra's noise maps), fetched as GeoTIFF tiles.

    DescribeCoverage gives the envelope and resolution; the envelope (clipped to ``bbox``,
    E0 N0 E1 N1 in the coverage's CRS, when set) is cut into ``tile_px``-pixel squares at
    native resolution and each is requested into data/raw/{slug}/{E}_{N}.tif.
    """

    type: Literal["wcs"]
    url: str
    coverage_id: str
    tile_px: int = 8192
    compression: str | None = "Deflate"
    bbox: list[float] | None = None


class ArcgisItemAccess(_Strict):
    """An ArcGIS Online item (ONS Open Geography Portal).

    ONS deletes an item when it publishes a new version, so the item is found by
    searching for ``query`` and taking the newest result whose title matches
    ``title_regex`` (and ``item_type``). ``pin`` is the item last known to work; it is
    used when search finds nothing.
    """

    type: Literal["arcgis_item"]
    pin: str
    query: str | None = None
    title_regex: str | None = None
    item_type: str | None = None
    # data: the item's file (CSV Collection zips) · hub_export: Hub-generated file of a
    # feature layer · featureserver: page through the layer's query API
    mode: Literal["data", "hub_export", "featureserver"] = "data"
    export_format: Literal["csv", "geoPackage", "geojson"] | None = None
    layer: int = 0
    # featureserver only: fields to keep, and the CRS for geometry (27700 = British National Grid)
    fields: list[str] | None = None
    geometry: Literal["none", "point", "polygon"] = "none"
    out_sr: int = 27700


class GovukAttachmentAccess(_Strict):
    """An attachment on a GOV.UK publication page, found via the content API.

    ``pick: one`` requires exactly one match. ``pick: latest`` is for pages that keep
    every past release (e.g. monthly Ofsted files): it takes the match with the latest
    date in its file name, such as "as_at_31_August_2026".
    """

    type: Literal["govuk_attachment"]
    path: str
    attachment_regex: str
    pick: Literal["one", "latest"] = "one"


class LinkStep(_Strict):
    """One hop of an ``html_link`` resolver: a regex whose first group is a URL."""

    pattern: str
    # page: document order · desc: highest match first (e.g. a year in the name) ·
    # date_desc: newest date in the URL first ("july-2026", "31-august-2026")
    order: Literal["page", "desc", "date_desc"] = "page"


class HtmlLinkAccess(_Strict):
    """A file found by following links from a landing page.

    Each step searches the current page for links; non-final steps are tried in order
    until one leads to a page where the next step matches (so an announced release
    without files yet is skipped).
    """

    type: Literal["html_link"]
    page: str
    steps: list[LinkStep]


class CkanAccess(_Strict):
    """A resource in a CKAN data portal package (e.g. NHSBSA open data).

    Packages that gain a file each period keep the old ones, so of the resources whose
    name or URL matches ``resource_regex`` the one whose name sorts last is taken
    (names like ``CONSOL_PHARMACY_LIST_202606Q1`` sort by date).
    """

    type: Literal["ckan"]
    api: str
    package: str
    resource_regex: str


class NomisAccess(_Strict):
    """A query against the Nomis API, paged into one CSV.

    Nomis returns at most 25,000 rows per request, and England has 33,755 LSOAs.
    ``params`` are the API's query parameters (geography, date, measures, select...).
    """

    type: Literal["nomis"]
    dataset: str
    params: dict[str, str]
    page_size: int = 25_000


class OvertureAccess(_Strict):
    """Overture Maps data queried in place with DuckDB over S3 (no full download).

    ``bbox`` (min lon, min lat, max lon, max lat) and ``where`` (SQL on the release's
    columns) pick the rows; ``select`` lists the output columns (SQL expressions with
    ``AS`` names). ``release`` pins a release; by default the latest is used.
    """

    type: Literal["overture"]
    theme: str
    kind: str
    bbox: tuple[float, float, float, float]
    select: list[str]
    where: str | None = None
    release: str | None = None


class ManualAccess(_Strict):
    """A file the user downloads by hand (e.g. behind a free login) into data/manual/{slug}/."""

    type: Literal["manual"]
    instructions: str
    expect: list[str]


Access = Annotated[
    HttpAccess
    | ArcgisItemAccess
    | GovukAttachmentAccess
    | HtmlLinkAccess
    | CkanAccess
    | NomisAccess
    | OvertureAccess
    | WcsAccess
    | WfsAccess
    | ManualAccess,
    Field(discriminator="type"),
]

# Layout of data/serve/ (manifest, parquet columns). Readers refuse other versions.
SERVE_SCHEMA_VERSION = 2

# The UK's nations, by the first letter of their ONS codes. config/nations.yaml configures
# the ones a build can include; coverage declarations may name any of them.
Nation = Literal["E", "W", "S", "N"]
NATION_CODES: tuple[str, ...] = ("E", "W", "S", "N")
NATION_NAMES = {"E": "England", "W": "Wales", "S": "Scotland", "N": "Northern Ireland"}


def _nation_list(nations: list[str], what: str) -> list[str]:
    if not nations:
        raise ValueError(f"{what}: coverage must name at least one nation")
    if len(set(nations)) != len(nations):
        raise ValueError(f"{what}: coverage repeats a nation: {nations}")
    return [n for n in NATION_CODES if n in nations]


Theme = Literal[
    "geography",
    "safety",
    "environment",
    "health",
    "education",
    "transport",
    "amenities",
    "housing",
    "community",
]


class DatasetSpec(_Strict):
    """One entry of config/datasets.yaml."""

    title: str
    theme: Theme
    # Which nations the file covers, whatever the stager currently keeps
    coverage: list[Nation] = Field(default_factory=lambda: ["E"])
    priority: Literal["P0", "P1", "P2"] = "P0"
    access: Access
    format: Literal["csv", "zip", "gpkg", "xlsx", "ods", "parquet", "json", "geojson", "pbf", "tif"]
    # zip only: glob patterns of members to extract (everything if omitted)
    extract: list[str] | None = None
    landing_page: str | None = None
    description: str
    licence: str
    licence_verified: bool = True
    attribution: str
    cadence: Literal["daily", "weekly", "monthly", "quarterly", "annual", "adhoc", "static"]
    size_hint: str | None = None
    notes: str | None = None


def load_registry() -> dict[str, DatasetSpec]:
    """Load and validate config/datasets.yaml, keyed by dataset slug.

    Top-level keys starting with ``x-`` hold YAML anchors (shared attribution text etc.)
    and are not datasets.
    """
    registry = {
        slug: DatasetSpec.model_validate(raw)
        for slug, raw in get_config("datasets").items()
        if not slug.startswith("x-")
    }
    for slug, spec in registry.items():
        _nation_list(spec.coverage, slug)
    return registry


# ---------------------------------------------------------------------------------------
# Indicators, themes and weights (config/indicators.yaml, config/weights.yaml)
# ---------------------------------------------------------------------------------------

ScoredTheme = Literal[
    "safety",
    "environment",
    "health",
    "education",
    "transport",
    "amenities",
    "housing",
    "community",
]


class ThemeSpec(_Strict):
    label: str
    description: str


# Display units → a code the front end formats by (and a catalogue translates), so the
# unit text can be in any language without the formatting guessing from English words
UNIT_CODES: dict[str, str] = {
    "£": "gbp",
    "£ a year": "gbp_year",
    "%": "pct",
    "% of homes": "pct_homes",
    "% of pupils": "pct_pupils",
    "% of 16–64s": "pct_working_age",
    "metres": "metres",
    "per 1,000 residents a year": "per_1000_year",
    "score": "score",
    "score 0–100": "score_100",
    "µg/m³": "ugm3",
    "index 0–1": "index",
    "quality 0–1": "quality",
    "rating 0–1 (Good = 0.75)": "rating",
    "rate": "rate",
    "collisions a year within 500m": "collisions_year",
    "patients per FTE GP": "patients_per_gp",
    "Attainment 8": "attainment8",
    "buses an hour": "buses_hour",
    "years of income": "years_income",
    "years": "years",
    "people per km²": "people_km2",
    "nW/cm²/sr": "radiance",
}


class IndicatorSpec(_Strict):
    """One indicator: how to build it per LSOA and how it enters the score.

    ``role``: scored (in the composite), context (shown, not scored) or diagnostic
    (kept for QA and imputation). Within one ``overlap_group`` at most one indicator
    may be scored, so the same thing isn't counted twice.
    """

    id: str
    theme: ScoredTheme
    label: str
    description: str
    unit: str
    direction: Literal["higher_better", "lower_better"]
    # uk: comparable across nations, ranked against the whole country · nation: the measure
    # or its source differs by nation (deprivation indices, school grades, crime recording),
    # so rank-normalised values are percentiles within the nation
    benchmark: Literal["uk", "nation"] = "uk"
    # Nations the indicator is built for; by default every nation all its sources cover.
    # Set it when a source is only a helper (the IoD imputation behind crime rates).
    coverage: list[Nation] | None = None
    sources: list[str]
    builder: str  # "module:function" under lix_pipeline.indicators
    params: dict = Field(default_factory=dict)
    # How raw values become 0–100 (see lix_core.scoring.normalise)
    normalise: Literal["rank", "scale", "threshold"] = "rank"
    log1p: bool = False
    good: float | None = None
    bad: float | None = None
    scale_max: float = 1.0
    weight: float = 1.0
    role: Literal["scored", "context", "diagnostic"] = "scored"
    overlap_group: str | None = None
    caveats: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unit_code(self) -> str:
        """The unit's code (UNIT_CODES); the manifest and models carry it beside ``unit``."""
        return UNIT_CODES[self.unit]


class IndicatorCatalogue(_Strict):
    themes: dict[ScoredTheme, ThemeSpec]
    indicators: list[IndicatorSpec]

    def scored(self) -> list[IndicatorSpec]:
        return [i for i in self.indicators if i.role == "scored"]

    def by_id(self) -> dict[str, IndicatorSpec]:
        return {i.id: i for i in self.indicators}


def indicator_coverage(indicator: IndicatorSpec, registry: dict[str, DatasetSpec]) -> list[str]:
    """Nations an indicator covers: its own list, else every nation all its sources cover."""
    if indicator.coverage is not None:
        return _nation_list(indicator.coverage, indicator.id)
    covered = set(NATION_CODES)
    for slug in indicator.sources:
        if slug in registry:
            covered &= set(registry[slug].coverage)
    return [n for n in NATION_CODES if n in covered]


def catalogue_problems(
    catalogue: IndicatorCatalogue, registry: dict[str, DatasetSpec]
) -> list[str]:
    """Structural checks on the indicator catalogue against the dataset registry."""
    problems = []
    ids = [i.id for i in catalogue.indicators]
    if len(ids) != len(set(ids)):
        problems.append("duplicate indicator ids")
    groups: dict[str, list[tuple[str, list[str]]]] = {}
    for ind in catalogue.indicators:
        if ind.unit not in UNIT_CODES:
            problems.append(f"{ind.id}: unit {ind.unit!r} has no code in UNIT_CODES")
        unknown = [s for s in ind.sources if s not in registry]
        if unknown:
            problems.append(f"{ind.id}: unknown sources {unknown}")
        if ind.theme not in catalogue.themes:
            problems.append(f"{ind.id}: unknown theme {ind.theme}")
        if not unknown:
            # An explicit list may exceed the intersection (a helper source such as the IoD
            # imputation need not cover every nation) but never a nation no source covers
            any_source = {n for slug in ind.sources for n in registry[slug].coverage}
            beyond = [n for n in (ind.coverage or []) if n not in any_source]
            if beyond:
                problems.append(
                    f"{ind.id}: coverage {beyond} beyond its sources' {sorted(any_source)}"
                )
            if ind.role == "scored" and ind.overlap_group:
                groups.setdefault(ind.overlap_group, []).append(
                    (ind.id, indicator_coverage(ind, registry))
                )
    # Within an overlap group, at most one scored indicator per nation
    for group, members in groups.items():
        for nation in NATION_CODES:
            scored_here = [iid for iid, cov in members if nation in cov]
            if len(scored_here) > 1:
                problems.append(f"overlap group {group} scores {scored_here} in {nation}")
    return problems


def coverage_problems(
    catalogue: IndicatorCatalogue, registry: dict[str, DatasetSpec], nations: tuple[str, ...]
) -> list[str]:
    """Every theme must keep at least MIN_THEME_COVERAGE of its scored weight in each nation.

    Run by ``lix validate config``; a nation whose sources are still being added fails
    here until enough of its indicators exist.
    """
    from lix_core.scoring import MIN_THEME_COVERAGE

    problems = []
    for theme in catalogue.themes:
        members = [i for i in catalogue.scored() if i.theme == theme]
        total = sum(i.weight for i in members)
        if total <= 0:
            continue
        for nation in nations:
            here = sum(i.weight for i in members if nation in indicator_coverage(i, registry))
            if here / total < MIN_THEME_COVERAGE:
                missing = [i.id for i in members if nation not in indicator_coverage(i, registry)]
                problems.append(
                    f"{theme} keeps {here / total:.0%} of its weight in {NATION_NAMES[nation]} "
                    f"(< {MIN_THEME_COVERAGE:.0%}); not covered: {missing}"
                )
    return problems


class Preset(_Strict):
    label: str
    description: str
    themes: dict[ScoredTheme, float]
    # Multipliers on individual indicators' weights within their theme
    indicators: dict[str, float] = Field(default_factory=dict)


class WeightsConfig(_Strict):
    default_preset: str
    presets: dict[str, Preset]


def load_indicators() -> IndicatorCatalogue:
    """Load and cross-check config/indicators.yaml against the registry."""
    catalogue = IndicatorCatalogue.model_validate(get_config("indicators"))
    problems = catalogue_problems(catalogue, load_registry())
    if problems:
        raise ValueError("Invalid config/indicators.yaml: " + "; ".join(problems))
    return catalogue


def load_weights() -> WeightsConfig:
    """Load config/weights.yaml; every preset must weight every theme."""
    weights = WeightsConfig.model_validate(get_config("weights"))
    themes = set(load_indicators().themes)
    for name, preset in weights.presets.items():
        missing = themes - set(preset.themes)
        if missing:
            raise ValueError(f"Preset {name!r} has no weight for themes {sorted(missing)}")
    if weights.default_preset not in weights.presets:
        raise ValueError(f"default_preset {weights.default_preset!r} is not a preset")
    return weights


# ---------------------------------------------------------------------------------------
# Nations (config/nations.yaml)
# ---------------------------------------------------------------------------------------

LevelName = Literal["oa", "low", "mid", "upper"]
RucClass = Literal["urban", "town", "rural"]


class LevelSpec(_Strict):
    """One tier of a nation's statistical geography."""

    regex: str  # anchored, starting with the nation letter, e.g. ^E01\d{6}$
    official: str  # what the publisher calls it (LSOA, Data Zone, Super Data Zone...)
    count: int | None = None  # expected number of areas, checked by ``lix validate geo``


class NationSpec(_Strict):
    """One nation: its codes, expected sizes and how its backbone is built."""

    name: str
    ctry_cd: str
    input_crs: int = 27700  # CRS of the nation's coordinates in NSPL (Irish Grid for NI)
    bbox: tuple[float, float, float, float]  # lon/lat
    population: tuple[int, int]  # plausible total, checked by ``lix validate geo``
    levels: dict[LevelName, LevelSpec]
    geo_builder: str  # lix_pipeline.stage.<geo_builder> builds this nation's backbone
    places_country: str  # OS Open Names COUNTRY value
    ruc_map: dict[str, RucClass]  # the nation's urban/rural codes → urban | town | rural
    pseudo_region: bool = False  # NSPL has no real regions here; the nation is its region
    demo_upper: list[str] = Field(default_factory=list)  # upper-tier areas in the demo cut


class CountrySpec(_Strict):
    code: str
    name: str
    working_crs: int
    currency: str
    locale: str
    postcode_regex: str
    bbox: tuple[float, float, float, float]
    area_key: str


class NationsConfig(_Strict):
    country: CountrySpec
    nations: dict[str, NationSpec]


# ---------------------------------------------------------------------------------------
# Data-label catalogues (config/i18n/<locale>.yaml)
# ---------------------------------------------------------------------------------------

CATALOGUE_SECTIONS = ("themes", "presets", "units", "flags", "indicators")


def load_label_catalogues() -> dict[str, dict]:
    """Translations of the data's labels, keyed by locale (English lives in the YAML)."""
    folder = get_project_root() / "config" / "i18n"
    out: dict[str, dict] = {}
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        meta = raw.get("_meta") or {}
        if meta.get("status") not in ("draft", "reviewed"):
            raise ValueError(f"{path.name}: _meta.status must be draft or reviewed")
        out[path.stem] = raw
    return out


def catalogue_gaps(
    catalogue: IndicatorCatalogue, weights: "WeightsConfig", labels: dict
) -> list[str]:
    """Ids a label catalogue lacks (reviewed catalogues must have none)."""
    gaps = []
    for theme in catalogue.themes:
        if theme not in (labels.get("themes") or {}):
            gaps.append(f"themes.{theme}")
    for name in weights.presets:
        if name not in (labels.get("presets") or {}):
            gaps.append(f"presets.{name}")
    for code in sorted(set(UNIT_CODES.values())):
        if code not in (labels.get("units") or {}):
            gaps.append(f"units.{code}")
    for ind in catalogue.indicators:
        entry = (labels.get("indicators") or {}).get(ind.id) or {}
        if not entry.get("label"):
            gaps.append(f"indicators.{ind.id}.label")
    return gaps


@lru_cache(maxsize=1)
def load_nations() -> NationsConfig:
    """Load and validate config/nations.yaml (cached: it is static for a process)."""
    cfg = NationsConfig.model_validate(get_config("nations"))
    problems = []
    for code, nation in cfg.nations.items():
        if not (len(code) == 1 and code.isupper()):
            problems.append(f"nation code {code!r} is not one capital letter")
        if not nation.ctry_cd.startswith(code):
            problems.append(f"{code}: ctry_cd {nation.ctry_cd} does not start with {code}")
        missing = {"oa", "low", "mid", "upper"} - set(nation.levels)
        if missing:
            problems.append(f"{code}: levels missing {sorted(missing)}")
        for level, spec in nation.levels.items():
            if not (spec.regex.startswith(f"^{code}") and spec.regex.endswith("$")):
                problems.append(f"{code}.{level}: regex {spec.regex!r} must be ^{code}...$")
    if problems:
        raise ValueError("Invalid config/nations.yaml: " + "; ".join(problems))
    return cfg
