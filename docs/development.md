# Development

How the project is put together and how to work on it. For running it, see the
[README](../README.md); for contributing, [CONTRIBUTING.md](../CONTRIBUTING.md).

## The data pipeline

The pipeline downloads, stages and joins the open datasets below:

- `lix resolve` finds each source's current file and pins it in `config/datasets.lock.json`.
  ONS deletes Open Geography items whenever it publishes a new version, so those are found by
  search; GOV.UK files through the content API.
- `lix fetch` downloads what the lockfile pins (resumable, checksummed, rejects error pages served
  as data, waits out ArcGIS exports still being generated, extracts only the files it needs)
- `lix stage` turns each raw file into tidy Parquet in `data/staged/`
- `geo_lsoa` is the backbone every indicator joins onto: each LSOA of the active nations
  (`config/nations.yaml`; `LIX_NATIONS=E,W` narrows a build) with its nation, MSOA,
  local authority, region, friendly MSOA name, population-weighted centroid, urban/rural class,
  population, area and map bounding box; `lix validate geo` checks it
- helpers bring other geographies onto LSOAs: points, output areas, MSOA/local-authority values,
  1km grids (sampled at postcodes, so population-weighted) and distance-based access to places
- `lix indicators` builds 68 indicators (33 scored across 8 themes) from
  [`config/indicators.yaml`](../config/indicators.yaml); `lix score` turns them into theme and
  overall scores per LSOA for five persona presets ([`config/weights.yaml`](../config/weights.yaml)),
  plus a QA report. The method, including how Greater Manchester's missing crime data and
  Ofsted's framework changes are handled, is in [docs/methodology.md](methodology.md)
- CI runs ruff and pytest on every push and PR to `master` (tests never touch the network);
  a nightly job checks every source is still reachable (a 403 is reported as "not
  verified" rather than failed: some publishers block cloud addresses such as the runners')

Datasets ingested so far (full list with licences: [docs/data-sources.md](data-sources.md)):

| Theme | Sources |
|-------|---------|
| Geography | NSPL postcode lookup, LSOA/MSOA/local authority boundaries, population-weighted centroids, OA and 2011→2021 lookups, rural–urban class, House of Commons Library MSOA names, OS Open Names |
| Community | English Indices of Deprivation 2025; Census 2021 (population, density, age, households, health, accommodation, cars, tenure, commuting, qualifications); Nomis claimant count (monthly); OHID life expectancy by MSOA |
| Housing | HM Land Registry Price Paid (LSOA medians); ONS small-area income; council tax by billing authority; VOA housing stock by council tax band and build period |
| Safety | police.uk street crime, 36 months (Greater Manchester Police publishes none; flagged); DfT road collisions (STATS19, 5 years) |
| Environment | Defra modelled NO₂ and PM2.5 (1km, population-weighted to LSOAs); OS Open Greenspace; Environment Agency flood risk by postcode (rivers, sea and surface water) and NRW's risk areas in Wales; Defra and Welsh Government strategic noise maps; ESA WorldCover tree cover; VIIRS night-time lights (manual download) |
| Health | NHS GP practices, patients registered by LSOA (real catchments), GP workforce; CQC ratings of GP practices and care homes; NHS dental practices; NHSBSA community pharmacies |
| Education | Get Information About Schools; Ofsted inspections blended across the 2024 and 2025 framework changes; Ofsted nurseries and pre-schools; DfE key stage 2 and 4 results |
| Transport | DfT Transport Connectivity Metric; NaPTAN stations; Bus Open Data Service timetables (bus frequency); Ofcom broadband coverage |
| Amenities | OpenStreetMap points of interest; Food Standards Agency hygiene ratings; Overture Maps Places (pubs OSM lacks); Sport England Active Places |

## API and assistant

`make api` (or `uv run lix-api`) serves, on http://localhost:8000:

- a REST API under `/api/v1`: place search, area profiles, rankings with any weights and
  filters, comparisons, nearest places (GPs, schools, well-run pubs, ...), score explanations,
  the indicator catalogue and read-only SQL (docs at `/docs`)
- an AI assistant at `/agent` speaking [AG-UI](https://docs.ag-ui.com), built with
  [Pydantic AI](https://ai.pydantic.dev): its tools return typed results the front end renders as
  maps, cards and tables, and it moves the map and weight sliders through shared state
- an [MCP](https://modelcontextprotocol.io) server at `/mcp` with the same tools, for Claude
  Desktop and other MCP clients

The model is set with `LIX_MODEL`: `openrouter:...` (one `OPENROUTER_API_KEY` for many
providers; the default), `anthropic:...`, `openai:...`, `google:...` or `ollama:...`.
`LIX_MODEL=test` runs a scripted assistant that needs no API key, and without a key the
recorded demo answers stand in. See [.env.example](../.env.example). Each question is capped
(model requests, tool calls, tokens), and so are conversations, questions per minute and
spend per day (`api/src/lix_api/agent/limits.py`); a refusal, a failure or a missing key is
explained in the chat while the map keeps working, and every turn is logged to
`data/logs/agent.jsonl` (tools called, latency, tokens, what the provider billed).

The assistant has an eval suite ([api/evals/cases.yaml](../api/evals/cases.yaml): 32 cases
covering ranking with constraints, look-ups, follow-ups, ambiguous places, out-of-scope and
discriminatory requests, numbers grounded in tool results, analyst SQL, prompt injection and
replies in Welsh):

```bash
make eval MODEL=openrouter:anthropic/claude-haiku-5.5 CASES=family_leeds_budget,compare_two
make eval MODEL=openrouter:anthropic/claude-haiku-5.5    # all cases, about $0.05
```

Claude Opus 5.5, Claude Haiku 5.5 and Qwen 3.8 27B (open weights) all pass the 32 cases;
results, costs and what the evals caught are in [docs/evals.md](evals.md).

## Stakeholder demo

A 7–10 minute stakeholder walkthrough, with talking points and a rehearsal checklist, is in
[docs/demo.md](demo.md):

```bash
make demo           # Docker, real model from .env, opens http://localhost:3000
make demo-offline   # recorded conversations (config/demo_cassettes.json): no network needed
make demo-record    # re-record them after changing data or prompts
```

`LIX_DEMO=1 LIX_E2E_STACK=docker npx playwright test demo-storyline` (in `web/`) rehearses
the storyline against a running demo and saves a screenshot per step. `make gif` records the
README's GIFs and the release's walkthrough video the same way (needs ffmpeg), and
`make tiktok` the vertical, TikTok-style clip of the phone layout
([web/scripts/tiktok.mjs](../web/scripts/tiktok.mjs)).

## Docker

```bash
make up          # everything at http://localhost:3000 (LIX_PORT), data from ./data/serve
make up BUILD=1  # build the images from this checkout instead of pulling the release's
make up-demo     # the same on the committed demo dataset (fixtures/demo: Leeds + Brighton)
make down
```

One Caddy proxy ([docker/Caddyfile](../docker/Caddyfile)) is the only published port: it
serves the data files under `/data`, sends `/api/v1`, `/health` and `/mcp` to the API and
everything else to the web app, so the same images work on any host.

The data pack: `make data-pack` on a machine with a full build writes
`dist/lix-serve.tar.gz` (about 120 MB, with the licence notices); `make data-download`
fetches a release's pack and `make data-unpack` restores one you have. `make demo-data`
re-cuts the demo dataset from a full build, and `lix validate serve` checks any serve
directory (CI runs the browser tests on the demo data).

## Front end

`web/` is a Next.js app (Node 24, see `.nvmrc`):

- a MapLibre map of every neighbourhood in England and Wales from static PMTiles, coloured
  by UK percentile; weights are recomputed in the browser, so moving a slider recolours all
  35,672 areas in about 100 ms
- an assistant chat ([CopilotKit](https://copilotkit.ai) over AG-UI) whose tool calls render as
  components: ranked lists, area profiles, side-by-side comparisons, nearby places, score
  explanations and SQL results. The assistant and the page share state, so it can move the map,
  outline areas, set the weight sliders and save a shortlist, and it sees what you change
- presets (family, young professional, retired, commuter), a "compare like with like" switch for
  urban/rural fairness, a shortlist, shareable URLs, light and dark mode, and an analyst mode with
  a distribution chart, read-only SQL and CSV export

How it looks (tokens, the map ramp, theme colours, dark mode) is in [design.md](design.md);
`web/lib/palette.ts` is the source and `npm run tokens` regenerates the CSS.

```bash
make web-install   # npm ci (Node 24)
make dev           # API (model from .env, else the scripted assistant) + front end on :3000
make web-test      # typecheck, lint, unit tests (incl. scoring parity with Python)
make e2e           # browser tests (Playwright) against the local build
```

With a model key in `.env`, `make dev` uses the real model from `.env`.

## Planned

- More open datasets: rail station usage, mobile coverage, EV charging, private rents, jobs
  density, energy performance certificates, A&E distance and listed buildings, then the
  CDRC's Access to Healthy Assets & Hazards (behind a free login) as a cross-check
- Walking-network travel times instead of straight-line distances

## Building the data

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
make install    # uv sync
make fetch      # download every dataset pinned in the lockfile (about 20 GB; Price Paid is 5.5 GB)
make stage      # stage every downloaded dataset to data/staged/*.parquet
make indicators # build the indicator table
make score      # theme/overall scores, browser files and QA report in data/serve/
make validate   # check the geography backbone, the serve files and the anchor places
make test       # pytest (no network access; HTTP is tested against a local server)
make lint       # ruff check + format check
```

Or step by step:

```bash
uv run lix resolve --all              # pick up new upstream versions (updates the lockfile)
uv run lix fetch --theme geography    # or --slug nspl, --priority P0, --all
uv run lix stage --all
uv run lix resolve --check --all      # is every source still reachable?
```

Set `LIX_DATA_DIR` to keep data somewhere other than `./data`.

## Layout

```
config/datasets.yaml    dataset URLs, formats, licences
core/                   lix_core: paths, config, logging, shared code patterns
api/                    lix_api: FastAPI, the AI assistant (AG-UI) and the MCP server
pipeline/               lix_pipeline and the `lix` CLI
web/                    Next.js front end: map, chat and generative UI components
contracts/              JSON Schema of the API models and the scoring golden cases
  fetch/http.py         download engine with caching and resume
  geo/                  NSPL postcode lookup, LSOA boundaries
  stage/                one stager per dataset (nspl, price_paid, iod)
data/                   raw/, staged/, ... (gitignored; rebuilt by the pipeline)
```

The Python side is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
(`core`, `pipeline`, `api`).

Languages: how the interface and data-label catalogues work, and how to review or add
one, is in [i18n.md](i18n.md).
