"""FastAPI app: REST under /api/v1, the assistant over AG-UI at /agent, MCP at /mcp.

    uv run lix-api                      # http://localhost:8000 (LIX_MODEL picks the LLM)
    LIX_MODEL=test uv run lix-api       # scripted assistant, no API key needed

In development /data serves data/serve/ (map tiles and the browser's score file); in
docker compose the proxy (docker/Caddyfile) does that instead.
"""

import json
import os
import time
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Annotated, Literal

from fastapi import Body, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic_ai.ui import StateDeps

from lix_api.agent.agent import agent
from lix_api.agent.errors import FriendlyAGUIAdapter
from lix_api.agent.limits import limits, reply_model, user_turns
from lix_api.agent.runlog import log_run, usage_limits
from lix_api.agent.state import LiveabilityState
from lix_api.mcp_server import mcp
from lix_api.models import (
    AreaProfile,
    Comparison,
    Explanation,
    IndicatorInfo,
    IndicatorWeightMap,
    Level,
    Locale,
    Place,
    PoiResult,
    RankResult,
    ThemeWeightMap,
    WeightProfile,
)
from lix_api.services import areas, catalogue, compare, explain, pois, profiles, ranking, search
from lix_api.services.sql import SqlError, get_guard
from lix_api.store import get_store
from lix_core.paths import data_dir

VERSION = version("lix-api")
mcp_app = mcp.http_app(path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store().warm()
    limits.load(data_dir("logs") / "agent.jsonl")
    async with mcp_app.lifespan(app):
        yield


# The longest reference a request may carry (a postcode, a code, "Place, Scope") and the
# largest body a REST call may send: anything bigger is a mistake or an attack, and search
# time grows with it. The assistant's own endpoint carries the whole conversation each
# turn (every answer and tool result so far), so it has room for a long one
MAX_REF = 200
MAX_BODY_BYTES = 16_384
MAX_AGENT_BODY_BYTES = 8 * 1024 * 1024
# A weights map in a query string: a few ids and numbers, never a thousand brackets
MAX_WEIGHTS_CHARS = 2_000

app = FastAPI(
    title="UK Liveability Index API",
    version=VERSION,
    description="Scores for neighbourhoods of the UK and Ireland from open data (the data "
    "pack says which nations are covered). See /api/v1/sources for attribution.",
    lifespan=lifespan,
    # Behind the proxy only /api/v1/* reaches the API, so the schema and docs live there
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("LIX_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


class BodyLimit:
    """A request body larger than any legitimate one is refused: a REST call carries a
    few hundred bytes, the assistant's a conversation. A declared length over the limit
    is refused before the body is read; the bytes are counted as they arrive too, since a
    chunked request declares no length (the v0.6.3 release pass sent 4 MB that way)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = MAX_BODY_BYTES if scope["path"].startswith("/api/v1/") else MAX_AGENT_BODY_BYTES
        detail = f"Request body over {limit:,} bytes"
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), b"")
        if declared.isdigit() and int(declared) > limit:
            await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)
            return
        received = 0

        async def counted():
            nonlocal received
            event = await receive()
            if event["type"] == "http.request":
                received += len(event.get("body", b""))
                if received > limit:
                    # Raised where the body is read, inside the app, so the usual
                    # handler answers it
                    raise HTTPException(status_code=413, detail=detail)
            return event

        await self.app(scope, counted, send)


app.add_middleware(BodyLimit)


@app.exception_handler(LookupError)
async def not_found(request: Request, exc: LookupError):
    # Serialised, not formatted: a message quoting an id ("indicator:crime_violence") or
    # echoing what the user typed must still be valid JSON
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(ValueError)
async def bad_request(request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


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


Ref = Annotated[str, Query(max_length=MAX_REF)]
ThemeScore = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


@app.get(f"{api}/search")
def search_place(
    q: Ref, limit: Annotated[int, Query(ge=1, le=20)] = 8, locale: str | None = None
) -> list[Place]:
    return search.search_place(get_store(), q, limit=limit, locale=locale)


_WEIGHTS = {
    "theme_weights": TypeAdapter(ThemeWeightMap),
    "indicator_weights": TypeAdapter(IndicatorWeightMap),
}


def _weights_json(name: str, raw: str | None) -> dict[str, float] | None:
    """A weights map given as a JSON object in a query string: the same checks as the
    body form (finite numbers from 0 to 3, keyed by id or label)."""
    if not raw:
        return None
    if len(raw) > MAX_WEIGHTS_CHARS:
        raise ValueError(f"{name} is at most {MAX_WEIGHTS_CHARS:,} characters")
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError) as e:  # a thousand brackets end in RecursionError
        raise ValueError(f"{name} must be a JSON object of numbers: {e}") from e
    try:
        return _WEIGHTS[name].validate_python(parsed)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or name}: {err['msg']}" for err in e.errors()
        )
        raise ValueError(f"{name} must be a JSON object of numbers from 0 to 3: {problems}") from e


WEIGHTS_DOC = 'The sliders\' weights as a JSON object, e.g. {"safety": 3}'
MULTIPLIERS_DOC = 'Indicator multipliers as a JSON object, e.g. {"sunshine": 2}'


@app.get(f"{api}/areas/{{ref}}")
def area_profile(
    ref: Annotated[str, Path(max_length=MAX_REF)],
    preset: str | None = None,
    locale: str | None = None,
    theme_weights: str | None = Query(None, description=WEIGHTS_DOC),
    indicator_weights: str | None = Query(None, description=MULTIPLIERS_DOC),
) -> AreaProfile:
    """``locale`` (en, cy, gd, ga) picks the language of the area names; ``theme_weights``
    and ``indicator_weights`` re-score the area with the user's sliders and profile (the
    preset's name reads "(adjusted)")."""
    return areas.area_profile(
        get_store(),
        ref,
        preset=preset,
        theme_weights=_weights_json("theme_weights", theme_weights),
        indicator_weights=_weights_json("indicator_weights", indicator_weights),
        locale=locale,
    )


class RankRequest(BaseModel):
    within: str | None = Field(None, max_length=MAX_REF)
    radius_km: float | None = Field(None, gt=0, le=200)
    level: Level = "msoa"
    preset: str | None = Field(None, max_length=MAX_REF)
    theme_weights: ThemeWeightMap | None = None
    indicator_weights: IndicatorWeightMap | None = None
    max_median_price: float | None = Field(None, ge=0, allow_inf_nan=False)
    min_theme_scores: dict[str, ThemeScore] | None = None
    urban: bool | None = None
    limit: int = Field(10, ge=1, le=50)
    # or "theme:<id>" / "indicator:<id>": rank by one measure
    sort_by: str = Field("overall", max_length=MAX_REF)
    direction: Literal["best", "worst"] = "best"
    locale: str | None = Field(None, max_length=10)  # en, cy, gd or ga: the area names' language


@app.post(f"{api}/rank")
def rank_areas(req: RankRequest) -> RankResult:
    return ranking.rank_areas(get_store(), **req.model_dump())


@app.get(f"{api}/compare")
def compare_areas(
    areas_: Annotated[list[Ref], Query(alias="areas")],
    preset: str | None = None,
    locale: str | None = None,
    theme_weights: str | None = Query(None, description=WEIGHTS_DOC),
    indicator_weights: str | None = Query(None, description=MULTIPLIERS_DOC),
) -> Comparison:
    return compare.compare_areas(
        get_store(),
        areas_,
        preset=preset,
        theme_weights=_weights_json("theme_weights", theme_weights),
        indicator_weights=_weights_json("indicator_weights", indicator_weights),
        locale=locale,
    )


class ProfileRequest(BaseModel):
    description: str = Field(min_length=3, max_length=500)
    locale: Locale = "en"


@app.post(f"{api}/profile")
async def generate_profile(req: ProfileRequest) -> WeightProfile:
    """A named weighting from a description of the user: the configured model, or word
    matching when there is no key, the budget is spent or the model is the scripted one.
    The description is not stored; the run log keeps the kind, tokens and cost."""
    engine, _ = profiles.engine_choice()
    if engine == "model" and (refusal := limits.admit(1, req.locale)):
        raise HTTPException(status_code=429, detail=refusal)
    return await profiles.profile_from_description(get_store(), req.description.strip(), req.locale)


@app.get(f"{api}/pois")
def nearest_pois(
    category: Annotated[str, Query(max_length=60)],
    near: Ref,
    max_km: Annotated[float, Query(gt=0, le=20)] = 2.0,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> PoiResult:
    return pois.nearest_pois(get_store(), category, near, max_km, limit)


@app.get(f"{api}/explain/{{ref}}")
def explain_score(
    ref: Annotated[str, Path(max_length=MAX_REF)],
    theme: str | None = None,
    preset: str | None = None,
    locale: str | None = None,
) -> Explanation:
    return explain.explain_score(get_store(), ref, theme=theme, preset=preset, locale=locale)


@app.get(f"{api}/indicators")
def list_indicators(theme: str | None = None) -> list[IndicatorInfo]:
    return catalogue.list_indicators(get_store(), theme, detail=True)


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
        body = await request.json()  # the body is cached for the adapter
        turns = user_turns(body)
        locale = str((body.get("state") or {}).get("locale") or "en")[:2]
    except (ValueError, AttributeError):
        turns, locale = 0, "en"  # the adapter rejects the malformed request
    refusal = limits.admit(turns, locale)
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


# Every GET route answers HEAD as well (an uptime probe, a load balancer, a parquet reader
# sizing a file): FastAPI does not add it the way Starlette's own routes do, and the server
# drops the body itself (#178)
for _route in app.routes:
    if isinstance(_route, APIRoute) and "GET" in _route.methods:
        _route.methods.add("HEAD")
