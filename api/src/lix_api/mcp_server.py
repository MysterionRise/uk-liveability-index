"""MCP server: the index's read-only tools for any MCP client (Claude Desktop, IDEs, ...).

Mounted at /mcp (streamable HTTP). Same services as the web assistant, without the
page state: weights are passed explicitly instead.
"""

from fastmcp import FastMCP

from lix_api.models import (
    AreaProfile,
    Comparison,
    Explanation,
    IndicatorInfo,
    Level,
    Place,
    PoiResult,
    RankResult,
)
from lix_api.services import areas, catalogue, compare, explain, pois, ranking, search
from lix_api.store import get_store

mcp = FastMCP(
    "UK Liveability Index",
    instructions=(
        "Scores for UK neighbourhoods (LSOAs) across eight themes, built from open data; "
        "the data pack says which nations are covered (see get_area_profile.nation_name). "
        "Scores are 0–100, higher is better, with country-wide and within-nation "
        "percentiles. Use search_place to resolve names, then profile, rank, compare or "
        "explain areas."
    ),
)


@mcp.tool
def search_place(query: str) -> list[Place]:
    """Find places matching a name, postcode or area code (best first)."""
    return search.search_place(get_store(), query, limit=6)


@mcp.tool
def get_area_profile(area: str, preset: str | None = None) -> AreaProfile:
    """Profile a neighbourhood: overall and theme scores, strengths, weaknesses, key facts.

    preset: balanced (default), family, young_professional, retiree or commuter.
    """
    return areas.area_profile(get_store(), area, preset=preset)


@mcp.tool
def rank_areas(
    within: str | None = None,
    radius_km: float | None = None,
    level: Level = "msoa",
    preset: str | None = None,
    theme_weights: dict[str, float] | None = None,
    max_median_price: float | None = None,
    limit: int = 10,
    sort_by: str = "overall",
) -> RankResult:
    """Rank areas best first, optionally within a place/area and under a house price;
    ``sort_by`` ("theme:<id>" or "indicator:<id>") ranks by one measure instead."""
    return ranking.rank_areas(
        get_store(), within=within, radius_km=radius_km, level=level, preset=preset,
        theme_weights=theme_weights, max_median_price=max_median_price, limit=min(limit, 25),
        sort_by=sort_by,
    )  # fmt: skip


@mcp.tool
def compare_areas(areas: list[str], preset: str | None = None) -> Comparison:
    """Compare 2–5 areas side by side."""
    return compare.compare_areas(get_store(), areas, preset=preset)


@mcp.tool
def nearest_pois(category: str, near: str, max_km: float = 2.0, limit: int = 10) -> PoiResult:
    """Nearest places of a category (well_run_pub, gp, primary_school, supermarket, ...)."""
    return pois.nearest_pois(get_store(), category, near, max_km, min(limit, 25))


@mcp.tool
def explain_score(area: str, theme: str | None = None, preset: str | None = None) -> Explanation:
    """Why an area scores what it does, theme by theme, with sources and caveats."""
    return explain.explain_score(get_store(), area, theme=theme, preset=preset)


@mcp.tool
def list_indicators(theme: str | None = None) -> list[IndicatorInfo]:
    """What the index measures, with units, sources and caveats."""
    return catalogue.list_indicators(get_store(), theme)
