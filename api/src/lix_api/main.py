"""FastAPI app: REST under /api/v1, the assistant over AG-UI at /agent, MCP at /mcp.

    uv run lix-api                      # http://localhost:8000 (LIX_MODEL picks the LLM)
    LIX_MODEL=test uv run lix-api       # scripted assistant, no API key needed

In development /data serves data/serve/ (map tiles and the browser's score file); in
docker compose the proxy (docker/Caddyfile) does that instead.
"""

import os
import time
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic_ai.ui import StateDeps

from lix_api.agent.agent import agent
from lix_api.agent.errors import FriendlyAGUIAdapter
from lix_api.agent.limits import Limits, reply_model, user_turns
from lix_api.agent.runlog import log_run, usage_limits
from lix_api.agent.state import LiveabilityState
from lix_api.mcp_server import mcp
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
from lix_api.services.sql import SqlError, get_guard
from lix_api.store import get_store
from lix_core.paths import data_dir

VERSION = version("lix-api")
mcp_app = mcp.http_app(path="/")
limits = Limits.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store().warm()
    limits.load(data_dir("logs") / "agent.jsonl")
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(
    title="UK Liveability Index API",
    version=VERSION,
    description="Scores for UK neighbourhoods from open data (the data pack says which "
    "nations are covered). See /api/v1/sources for attribution.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("LIX_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(LookupError)
async def not_found(request: Request, exc: LookupError):
    return Response(
        content=f'{{"detail": "{exc}"}}', status_code=404, media_type="application/json"
    )


@app.exception_handler(ValueError)
async def bad_request(request: Request, exc: ValueError):
    return Response(
        content=f'{{"detail": "{str(exc)}"}}', status_code=400, media_type="application/json"
    )


@app.get("/health")
def health() -> dict:
    from lix_api.agent import models

    store = get_store()
    return {
        "status": "ok",
        "version": VERSION,
        "lsoas": len(store.lsoa_index),
        "geography": {
            "active": store.geography["active"],
            "area_counts": store.geography.get("area_counts", {}),
        },
        "built": store.manifest["generated_at"],
        "data": {
            "built": store.manifest["generated_at"],
            # How recent the data gets (the latest period across the sources)
            "data_to": store.manifest.get("data_to"),
            "data_release": store.manifest.get("data_release"),
            "stale": sorted(
                s for s, v in store.manifest.get("sources", {}).items() if v.get("stale")
            ),
        },
        "assistant": {
            "model": models.model_name(),
            "problem": models.MODEL_PROBLEM,
            # Without a working model, only the recorded prompts get real answers
            "recorded_only": models.MODEL_PROBLEM is not None,
            "limits": limits.status(),
        },
    }


api = "/api/v1"


@app.get(f"{api}/search")
def search_place(q: str, limit: int = 8) -> list[Place]:
    return search.search_place(get_store(), q, limit=min(limit, 20))


@app.get(f"{api}/areas/{{ref}}")
def area_profile(ref: str, preset: str | None = None, locale: str | None = None) -> AreaProfile:
    """``locale`` (en, cy, gd) picks the language of the area names."""
    return areas.area_profile(get_store(), ref, preset=preset, locale=locale)


class RankRequest(BaseModel):
    within: str | None = None
    radius_km: float | None = None
    level: Level = "msoa"
    preset: str | None = None
    theme_weights: dict[str, float] | None = None
    indicator_weights: dict[str, float] | None = None
    max_median_price: float | None = None
    min_theme_scores: dict[str, float] | None = None
    urban: bool | None = None
    limit: int = 10
    sort_by: str = "overall"  # or "theme:<id>" / "indicator:<id>": rank by one measure
    locale: str | None = None  # en, cy or gd: the language of the area names


@app.post(f"{api}/rank")
def rank_areas(req: RankRequest) -> RankResult:
    return ranking.rank_areas(get_store(), **{**req.model_dump(), "limit": min(req.limit, 50)})


@app.get(f"{api}/compare")
def compare_areas(
    areas_: Annotated[list[str], Query(alias="areas")], preset: str | None = None
) -> Comparison:
    return compare.compare_areas(get_store(), areas_, preset=preset)


@app.get(f"{api}/pois")
def nearest_pois(category: str, near: str, max_km: float = 2.0, limit: int = 10) -> PoiResult:
    return pois.nearest_pois(get_store(), category, near, min(max_km, 20), min(limit, 50))


@app.get(f"{api}/explain/{{ref}}")
def explain_score(ref: str, theme: str | None = None, preset: str | None = None) -> Explanation:
    return explain.explain_score(get_store(), ref, theme=theme, preset=preset)


@app.get(f"{api}/indicators")
def list_indicators(theme: str | None = None) -> list[IndicatorInfo]:
    return catalogue.list_indicators(get_store(), theme)


@app.get(f"{api}/presets")
def presets() -> dict:
    return catalogue.presets(get_store())


@app.get(f"{api}/sources")
def sources() -> dict:
    return catalogue.sources(get_store())


@app.post(f"{api}/sql")
def run_sql(query: Annotated[str, Body(embed=True)]) -> dict:
    try:
        result = get_guard().run(query)
    except SqlError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"columns": result.columns, "rows": result.rows, "truncated": result.truncated}


@app.post("/agent")
async def run_agent(request: Request) -> Response:
    """AG-UI endpoint: streams the assistant's messages, tool calls and state snapshots."""
    started = time.monotonic()
    try:
        turns = user_turns(await request.json())  # the body is cached for the adapter
    except ValueError:
        turns = 0  # the adapter rejects the malformed request
    refusal = limits.admit(turns)
    if refusal:
        # Answered without the model; the run still completes normally for the client
        return await FriendlyAGUIAdapter.dispatch_request(
            request, agent=agent, deps=StateDeps(LiveabilityState()), model=reply_model(refusal)
        )

    def done(result) -> None:
        record = log_run(result, started)
        limits.spend(record and record["cost"])

    return await FriendlyAGUIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=StateDeps(LiveabilityState()),
        usage_limits=usage_limits(),
        on_complete=done,
    )


app.mount("/mcp", mcp_app)


class DataFiles(StaticFiles):
    """Static data files that browsers must revalidate (ETag) rather than reuse blindly:
    PMTiles and Parquet are read in byte ranges, and a stale range from a previous build
    corrupts the read."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


if os.environ.get("LIX_SERVE_DATA", "1") == "1":
    app.mount("/data", DataFiles(directory=get_store().serve_dir, check_dir=False), name="data")


def run() -> None:
    import uvicorn

    uvicorn.run(
        "lix_api.main:app",
        host=os.environ.get("LIX_HOST", "127.0.0.1"),
        port=int(os.environ.get("LIX_PORT", "8000")),
        reload=os.environ.get("LIX_RELOAD") == "1",
    )
