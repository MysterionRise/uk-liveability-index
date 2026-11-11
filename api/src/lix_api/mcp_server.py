"""MCP server: the index's read-only tools for any MCP client (Claude Desktop, IDEs, ...).

Mounted at /mcp (streamable HTTP). Same services as the web assistant, without the
page state: weights are passed explicitly instead. Arguments carry the REST API's
bounds, so a client gets a validation error rather than a traceback or an unbounded
query, and every tool is annotated read-only so clients need not ask before each call.
"""

from importlib.metadata import version
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from lix_api.models import (
    AreaProfile,
    Comparison,
    Explanation,
    IndicatorBrief,
    IndicatorInfo,
    Level,
    Place,
    PoiResult,
    RankResult,
    ThemeWeightMap,
)
from lix_api.services import areas, catalogue, compare, explain, pois, ranking, search
from lix_api.store import get_store

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
Ref = Annotated[str, Field(min_length=1, max_length=200)]
Preset = Annotated[str, Field(max_length=40)] | None
# The model on the client's side reads every character of a reply, and the catalogue's
# longest caveats run past 500: the detail cards carry the start of each (REST's
# /indicators has them whole)
MAX_CAVEAT_CHARS = 300

mcp = FastMCP(
    "UK Liveability Index",
    version=version("lix-api"),
    instructions=(
        "Scores for neighbourhoods of the UK and Ireland across nine themes, built from open data; "
        "the data pack says which nations are covered (see get_area_profile.nation_name). "
        "Scores are 0–100, higher is better, with country-wide and within-nation "
        "percentiles. Use search_place to resolve names, then profile, rank, compare or "
        "explain areas."
    ),
)


@mcp.tool(annotations=READ_ONLY)
def search_place(query: Ref) -> list[Place]:
    """Find places matching a name, postcode or area code (best first)."""
    return search.search_place(get_store(), query, limit=6)


@mcp.tool(annotations=READ_ONLY)
def get_area_profile(area: Ref, preset: Preset = None) -> AreaProfile:
    """Profile a neighbourhood: overall and theme scores, strengths, weaknesses, key facts.

    preset: balanced (default), family, young_professional, retiree or commuter.
    """
    return areas.area_profile(get_store(), area, preset=preset)


@mcp.tool(annotations=READ_ONLY)
def rank_areas(
    within: Annotated[str, Field(max_length=200)] | None = None,
    radius_km: Annotated[float, Field(gt=0, le=200)] | None = None,
    level: Level = "msoa",
    preset: Preset = None,
    theme_weights: ThemeWeightMap | None = None,
    max_median_price: Annotated[float, Field(ge=0)] | None = None,
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
    sort_by: Annotated[str, Field(max_length=80)] = "overall",
    direction: Literal["best", "worst"] = "best",
) -> RankResult:
    """Rank areas best first (or worst first), optionally within a place/area and under a
    house price; ``sort_by`` ("theme:<id>" or "indicator:<id>") ranks by one measure
    instead. ``level``: lsoa (neighbourhood), msoa (the default), lad (council), region
    or nation. ``theme_weights``: theme id → 0–3, over the preset."""
    return ranking.rank_areas(
        get_store(), within=within, radius_km=radius_km, level=level, preset=preset,
        theme_weights=theme_weights, max_median_price=max_median_price,
        limit=limit, sort_by=sort_by, direction=direction,
    )  # fmt: skip


@mcp.tool(annotations=READ_ONLY)
def compare_areas(
    areas: Annotated[list[Ref], Field(min_length=2, max_length=5)], preset: Preset = None
) -> Comparison:
    """Compare 2–5 areas side by side."""
    return compare.compare_areas(get_store(), areas, preset=preset)


@mcp.tool(annotations=READ_ONLY)
def nearest_pois(
    category: Annotated[str, Field(min_length=1, max_length=50)],
    near: Ref,
    max_km: Annotated[float, Field(gt=0, le=20)] = 2.0,
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
) -> PoiResult:
    """Nearest places of a category (well_run_pub, gp, primary_school, supermarket, ...)."""
    return pois.nearest_pois(get_store(), category, near, max_km, limit)


@mcp.tool(annotations=READ_ONLY)
def explain_score(
    area: Ref, theme: Annotated[str, Field(max_length=40)] | None = None, preset: Preset = None
) -> Explanation:
    """Why an area scores what it does, theme by theme, with sources and caveats."""
    return explain.explain_score(get_store(), area, theme=theme, preset=preset)


@mcp.tool(annotations=READ_ONLY)
def list_indicators(
    theme: Annotated[str, Field(max_length=40)] | None = None, detail: bool = False
) -> list[IndicatorBrief] | list[IndicatorInfo]:
    """What the index measures: each indicator's id, label, theme, unit, role and the
    nations it covers; ``theme`` (safety, transport, ...) limits the list to one theme.

    ``detail`` adds descriptions, sources and caveats (a long caveat is cut short and
    ends "…") and needs a ``theme``: the whole catalogue in detail is about 70,000
    characters, more than a question is worth.
    """
    store = get_store()
    if detail and theme is None:
        # Cutting the caveats saved 2,000 characters of 70,000; a theme at a time is 4,000
        # to 20,000 (#230). REST's /indicators still gives everything at once
        raise ValueError(
            "detail needs a theme: list_indicators(theme=..., detail=true) with one of "
            + ", ".join(store.themes)
        )
    return catalogue.list_indicators(store, theme, detail=detail, max_caveat=MAX_CAVEAT_CHARS)
