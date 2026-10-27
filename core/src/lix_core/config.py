"""Loading and validation of the YAML files in ``config/``."""

from functools import lru_cache
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

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
    # The ArcGIS Hub the export is served from (the ONS geoportal unless the item belongs
    # to another portal, e.g. Tailte Éireann's data-osi.opendata.arcgis.com)
    hub: str = "https://geoportal.statistics.gov.uk"
    layer: int = 0
    # featureserver only: fields to keep, and the CRS for geometry (27700 = British National Grid)
    fields: list[str] | None = None
    geometry: Literal["none", "point", "line", "polygon"] = "none"
    out_sr: int = 27700


class ArcgisLayerAccess(_Strict):
    """A feature layer addressed by its query URL: a server outside ArcGIS Online (Historic
    Environment Scotland's INSPIRE services) or a hosted layer whose item need not be
    searched. Fetched like ``arcgis_item`` in ``featureserver`` mode; the layer's last edit
    is its version."""

    type: Literal["arcgis_layer"]
    url: str
    fields: list[str] | None = None
    geometry: Literal["none", "point", "line", "polygon"] = "none"
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
    # Every matching resource rather than the newest one (a package that splits one
    # dataset into a file per council)
    all_matches: bool = False


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


class CuratedAccess(_Strict):
    """A list kept in the repository (config/data/<slug>.csv) because no publisher offers
    it as one file: every row names its source and when it was checked."""

    type: Literal["curated"]
    path: str  # relative to the project root


class ApiKeyAccess(_Strict):
    """An open dataset behind a free registered key (Logainm's place-name API). The key is
    read from the environment variable ``env`` and sent as ``header`` or as the query
    parameter ``query``; it never reaches the lockfile, the docs or the logs. Without it
    the fetch is skipped with a warning, like a manual download, so a build completes.

    A paged JSON API (``page_param``) is read page by page into one JSON file of items.
    """

    type: Literal["api_key"]
    url: str
    env: str  # the environment variable holding the key
    header: str | None = None  # send the key in this header ("X-Api-Key") ...
    query: str | None = None  # ... or as this query parameter ("apiKey")
    prefix: str = ""  # text before the key in the header ("Bearer ")
    signup: str | None = None  # where a key is obtained (docs and warnings)
    page_param: str | None = None  # paged JSON: the page number parameter (from 1)
    per_page_param: str | None = None  # and the page size parameter
    per_page: int = 1000
    items_key: str | None = None  # the key holding a page's items (the page itself if omitted)
    timeout_s: int = 60

    @model_validator(mode="after")
    def _one_way_in(self):
        if bool(self.header) == bool(self.query):
            raise ValueError("api_key access needs exactly one of header or query")
        return self


class ManualAccess(_Strict):
    """A file the user downloads by hand (e.g. behind a free login) into data/manual/{slug}/."""

    type: Literal["manual"]
    instructions: str
    expect: list[str]


Access = Annotated[
    HttpAccess
    | ArcgisItemAccess
    | ArcgisLayerAccess
    | GovukAttachmentAccess
    | HtmlLinkAccess
    | CkanAccess
    | NomisAccess
    | OvertureAccess
    | WcsAccess
    | WfsAccess
    | ApiKeyAccess
    | ManualAccess
    | CuratedAccess,
    Field(discriminator="type"),
]

# Layout of data/serve/ (manifest, parquet columns). Readers refuse other versions.
SERVE_SCHEMA_VERSION = 5

# The nations, by the first letter of their codes: the UK's four (ONS GSS codes) and
# Ireland (codes minted here, see lix_core.codes.minted_codes). config/nations.yaml
# configures the ones a build can include; coverage declarations may name any of them.
Nation = Literal["E", "W", "S", "N", "I"]
NATION_CODES: tuple[str, ...] = ("E", "W", "S", "N", "I")
NATION_NAMES = {
    "E": "England",
    "W": "Wales",
    "S": "Scotland",
    "N": "Northern Ireland",
    "I": "Ireland",
}


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
    "climate",
]


class DatasetSpec(_Strict):
    """One entry of config/datasets.yaml."""

    title: str
    theme: Theme
    # Which nations the file covers, whatever the stager currently keeps
    coverage: list[Nation] = Field(default_factory=lambda: ["E"])
    priority: Literal["P0", "P1", "P2"] = "P0"
    access: Access
    format: Literal[
        "csv", "zip", "gpkg", "xlsx", "ods", "parquet", "json", "geojson", "pbf", "tif", "nc"
    ]
    # zip only: glob patterns of members to extract (everything if omitted)
    extract: list[str] | None = None
    landing_page: str | None = None
    description: str
    licence: str
    licence_verified: bool = True
    attribution: str
    cadence: Literal["daily", "weekly", "monthly", "quarterly", "annual", "adhoc", "static"]
    # The period the data describes, for sources whose files carry no date to read it
    # from ("2021", "Round 4 (2021 traffic)"); see lix_pipeline.stage.periods
    period: str | None = None
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
    "climate",
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
    "km": "km",
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
    "per km²": "per_km2",
    "nW/cm²/sr": "radiance",
    "hours a year": "hours_year",
    "days a year": "days_year",
    "°C": "celsius",
    "mm a year": "mm_year",
    "m/s": "m_s",
    "within 1.5km": "within_1500m",
    "patients per practice": "patients_per_practice",
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
    # Scored ids this indicator stands in for where they are not available (the profile
    # shows one row, and at the coverage gate its weight counts towards theirs, capped);
    # it never covers a nation any of them covers
    substitute_for: list[str] = Field(default_factory=list)

    @field_validator("substitute_for", mode="before")
    @classmethod
    def _one_or_many(cls, value):
        return [value] if isinstance(value, str) else value

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
    # A substitute stands in for scored indicators of its theme where they are absent
    by_id = catalogue.by_id()
    known = {i.id for i in catalogue.indicators if all(s in registry for s in i.sources)}
    for ind in catalogue.indicators:
        for target_id in ind.substitute_for:
            target = by_id.get(target_id)
            if target is None:
                problems.append(f"{ind.id}: substitute_for unknown indicator {target_id!r}")
            elif ind.role != "scored" or target.role != "scored" or target.theme != ind.theme:
                problems.append(
                    f"{ind.id}: substitute_for {target_id} needs both scored in one theme"
                )
            elif ind.id in known and target_id in known:
                both = set(indicator_coverage(ind, registry)) & set(
                    indicator_coverage(target, registry)
                )
                if both:
                    problems.append(
                        f"{ind.id}: substitutes for {target_id} but both cover {sorted(both)}"
                    )
    return problems


def coverage_problems(
    catalogue: IndicatorCatalogue,
    registry: dict[str, DatasetSpec],
    nations: tuple[str, ...],
    gaps: dict[str, list[str]] | None = None,
) -> list[str]:
    """Every theme must keep at least MIN_THEME_COVERAGE of its scored weight in each nation.

    The theme's weight is that of its ordinary scored indicators; a substitute covering a
    nation adds its own weight there, capped at the weight of what it replaces, so a thinner
    stand-in reads as thinner. A theme listed in a nation's ``theme_gaps`` (``gaps``) is a
    declared gap and passes. Run by ``lix validate config``; a nation whose sources are
    still being added fails here until enough of its indicators exist.
    """
    from lix_core.scoring import MIN_THEME_COVERAGE

    problems = []
    gaps = gaps or {}
    for theme in catalogue.themes:
        members = [i for i in catalogue.scored() if i.theme == theme]
        canonical = [i for i in members if not i.substitute_for]
        substitutes = [i for i in members if i.substitute_for]
        total = sum(i.weight for i in canonical)
        if total <= 0:
            continue
        weight_of = {i.id: i.weight for i in canonical}
        for nation in nations:
            if theme in gaps.get(nation, []):
                continue
            covered = {i.id for i in canonical if nation in indicator_coverage(i, registry)}
            here = sum(weight_of[i] for i in covered)
            replaced: set[str] = set()
            for sub_ in substitutes:
                if nation not in indicator_coverage(sub_, registry):
                    continue
                targets = [t for t in sub_.substitute_for if t in weight_of and t not in covered]
                here += min(sub_.weight, sum(weight_of[t] for t in targets))
                replaced.update(targets)
            if here / total < MIN_THEME_COVERAGE:
                missing = [i.id for i in canonical if i.id not in covered and i.id not in replaced]
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
    label: str  # what the interface calls one, in English ("data zone")
    labels: str  # and several ("data zones"); translations live in config/i18n/*.yaml


class GeoSources(_Strict):
    """The registry slugs a nation's backbone, point joins and map tiles read."""

    low_boundaries: str  # polygons of the low level (LSOA, Data Zone...)
    mid_boundaries: str  # polygons of the mid level (MSOA, Intermediate Zone...)
    # polygons of the upper level: the UK file covers its four nations, Ireland has its own
    upper_boundaries: str = "lad_boundaries"
    # population-weighted centroids of the low level; none where the builder computes
    # them (Ireland: the mean of each area's home points)
    centroids: str | None = None
    oa_lookup: str | None = None  # output area → low → mid lookup, where a nation has one
    population: str  # mid-year population estimates at the low level
    code_column: str | None = None  # the boundaries' code attribute, when not *CD
    # The publisher's ids are not usable as codes (Ireland's Small Area ids carry "/"
    # for split and merged areas): the backbone mints ``<nation><entity><6-digit rank>``
    # codes from the sorted ids and keeps the id as ``source_code``
    minted_codes: bool = False
    mid_code_column: str | None = None  # the mid boundaries' code attribute, when minted
    simplify_m: float = 0.0  # simplify full-resolution polygons for the map tiles (metres)
    extra: list[str] = Field(default_factory=list)  # other files the builder reads


class NationSpec(_Strict):
    """One nation: its codes, expected sizes and how its backbone is built."""

    name: str
    country: str  # the country it belongs to (a key of ``countries``)
    # The name in the pack's other languages (locale → name), for exact search aliases
    names: dict[str, str] = Field(default_factory=dict)
    ctry_cd: str
    input_crs: int = 27700  # CRS of the nation's coordinates in NSPL (Irish Grid for NI)
    bbox: tuple[float, float, float, float]  # lon/lat
    population: tuple[int, int]  # plausible total, checked by ``lix validate geo``
    levels: dict[LevelName, LevelSpec]
    geo_builder: str  # lix_pipeline.stage.<geo_builder> builds this nation's backbone
    places_country: str  # OS Open Names COUNTRY value (os_open_names builder)
    # Where the nation's searchable places come from: OS Open Names (Great Britain) or
    # the nation's OpenStreetMap extract (place=* nodes) where OS stops
    places_builder: Literal["os_open_names", "osm"] = "os_open_names"
    ruc_map: dict[str, RucClass]  # the nation's urban/rural codes → urban | town | rural
    pseudo_region: bool = False  # NSPL has no real regions here; the nation is its region
    demo_upper: list[str] = Field(default_factory=list)  # upper-tier areas in the demo cut
    geo: GeoSources
    # Themes the nation's open data cannot cover: shown as "limited data" and left out
    # of its overall score instead of failing the coverage gate
    theme_gaps: list[ScoredTheme] = Field(default_factory=list)
    # Other names people use for it ("Republic of Ireland", "ROI"), matched exactly by
    # search and as a scope ("best areas in the Republic of Ireland")
    aliases: list[str] = Field(default_factory=list)
    # The base of the nation's headline percentile and band: every area in the build, or
    # the nation itself where its data is thinner than the rest (#147)
    headline_within: Literal["all", "nation"] = "all"


class CountrySpec(_Strict):
    """A country of the build: what its nations share (currency, locale, postcodes)."""

    name: str
    short: str  # how copy refers to it ("the UK", "Ireland")
    names: dict[str, str] = Field(default_factory=dict)  # the short name per locale
    aliases: list[str] = Field(default_factory=list)  # "UK", "United Kingdom": a scope
    currency: str
    locale: str
    postcode_regex: str | None = None  # none where there is no open postcode file
    bbox: tuple[float, float, float, float]


class BuildSpec(_Strict):
    """What every nation of a build shares: the working grid and the key column."""

    working_crs: int
    area_key: str


class NationsConfig(_Strict):
    build: BuildSpec
    countries: dict[str, CountrySpec]
    nations: dict[str, NationSpec]


# The API's level names → the tiers of nations.yaml
LEVEL_KEYS = {"lsoa": "low", "msoa": "mid", "lad": "upper"}


def geo_slugs(nation: NationSpec) -> list[str]:
    """Every registry slug a nation's ``geo`` block names, in order."""
    geo = nation.geo
    slugs = [
        geo.low_boundaries,
        geo.mid_boundaries,
        geo.upper_boundaries,
        geo.centroids,
        geo.oa_lookup,
        geo.population,
    ]
    return list(dict.fromkeys([s for s in slugs if s] + geo.extra))


def backbone_slugs(nations: tuple[str, ...] | None = None) -> list[str]:
    """The geography sources of ``nations`` (default: every configured one), unique."""
    cfg = load_nations().nations
    out: list[str] = []
    for code in nations or tuple(cfg):
        out.extend(s for s in geo_slugs(cfg[code]) if s not in out)
    return out


def boundary_slugs(level: str = "low", nations: tuple[str, ...] | None = None) -> list[str]:
    """The polygon sources of one tier ("low" or "mid") across ``nations``, unique."""
    cfg = load_nations().nations
    attr = {"low": "low_boundaries", "mid": "mid_boundaries"}[level]
    out: list[str] = []
    for code in nations or tuple(cfg):
        slug = getattr(cfg[code].geo, attr)
        if slug not in out:
            out.append(slug)
    return out


# ---------------------------------------------------------------------------------------
# Data-label catalogues (config/i18n/<locale>.yaml)
# ---------------------------------------------------------------------------------------

CATALOGUE_SECTIONS = ("themes", "presets", "units", "flags", "indicators", "levels", "urban_rural")


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


LABEL_FIELDS = {"label", "description", "one", "other"}


def catalogue_gaps(
    catalogue: IndicatorCatalogue,
    weights: "WeightsConfig",
    labels: dict,
    nations: dict[str, NationSpec] | None = None,
) -> list[str]:
    """Ids a label catalogue lacks (reviewed catalogues must have none); with ``nations``,
    the level words of each nation too (``levels.S.low: {one, other}``)."""
    gaps = []
    for code, nation in (nations or {}).items():
        for level in ("low", "mid", "upper"):
            entry = ((labels.get("levels") or {}).get(code) or {}).get(level) or {}
            if not (entry.get("one") and entry.get("other")):
                gaps.append(f"levels.{code}.{level}")
        # The urban/rural classes of the nation's classification, by code (#187)
        classes = (labels.get("urban_rural") or {}).get(code) or {}
        for ruc_code in nation.ruc_map or {}:
            if not classes.get(str(ruc_code)):
                gaps.append(f"urban_rural.{code}.{ruc_code}")
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
    # An unquoted comma in a flow mapping splits a description into stray keys with no
    # value ("Ysgolion a meithrinfeydd da", "diogelwch …": null); such an entry is broken
    for section in ("themes", "presets", "indicators", "units", "flags"):
        for key, entry in (labels.get(section) or {}).items():
            if isinstance(entry, dict):
                stray = sorted(k for k, v in entry.items() if v is None or k not in LABEL_FIELDS)
                if stray:
                    gaps.append(f"{section}.{key}: unexpected fields {stray}")
    return gaps


@lru_cache(maxsize=1)
def load_nations() -> NationsConfig:
    """Load and validate config/nations.yaml (cached: it is static for a process)."""
    cfg = NationsConfig.model_validate(get_config("nations"))
    registry = load_registry()
    problems = []
    for code, nation in cfg.nations.items():
        if not (len(code) == 1 and code.isupper()):
            problems.append(f"nation code {code!r} is not one capital letter")
        if not nation.ctry_cd.startswith(code):
            problems.append(f"{code}: ctry_cd {nation.ctry_cd} does not start with {code}")
        if nation.country not in cfg.countries:
            problems.append(f"{code}: country {nation.country!r} is not in countries")
        # The output-area tier is optional (Ireland has no tier below the Small Area)
        missing = {"low", "mid", "upper"} - set(nation.levels)
        if missing:
            problems.append(f"{code}: levels missing {sorted(missing)}")
        for level, spec in nation.levels.items():
            if not (spec.regex.startswith(f"^{code}") and spec.regex.endswith("$")):
                problems.append(f"{code}.{level}: regex {spec.regex!r} must be ^{code}...$")
        unknown = [slug for slug in geo_slugs(nation) if slug not in registry]
        if unknown:
            problems.append(f"{code}: geo names sources missing from the registry: {unknown}")
    if problems:
        raise ValueError("Invalid config/nations.yaml: " + "; ".join(problems))
    return cfg
