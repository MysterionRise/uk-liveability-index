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
- `lix indicators` builds 112 indicators (44 scored across 9 themes) from
  [`config/indicators.yaml`](../config/indicators.yaml); `lix score` turns them into theme and
  overall scores per LSOA for five persona presets ([`config/weights.yaml`](../config/weights.yaml)),
  plus a QA report. The method, including how Greater Manchester's missing crime data and
  Ofsted's framework changes are handled, is in [docs/methodology.md](methodology.md)
- CI runs ruff and pytest on every push and PR to `master` (tests never touch the network);
  a nightly job checks every source is still reachable and whether a newer edition is
  out (the edition is the server's `Last-Modified`, else its `ETag`, else the file name
  it states; a 403 is reported as "not verified" rather than failed: some publishers
  block cloud addresses such as the runners')

Datasets ingested so far (full list with licences: [docs/data-sources.md](data-sources.md)):

| Theme | Sources |
|-------|---------|
| Geography | NSPL postcode lookup, LSOA/MSOA/local authority boundaries, population-weighted centroids, OA and 2011→2021 lookups, rural–urban class, House of Commons Library MSOA names, OS Open Names; Scotland: Data Zone and Intermediate Zone 2022 boundaries and centroids, the Data Zone lookup, NRS output-area and small-area population estimates, the SIMD postcode lookup (the 2011→2022 crosswalk), Gaelic council names (Wikidata); Northern Ireland: NISRA's Super Data Zones, Data Zones and population estimates, OSNI boundaries, Logainm place names (keyed); Ireland: CSO Small Areas and Electoral Divisions, Tailte Éireann councils and building footprints (home points), the Census 2022 SAPS, OpenStreetMap places; the coastline: OS Boundary-Line's high water mark, Tailte Éireann's high water mark, the OSNI outline |
| Climate | Met Office HadUK-Grid 1991–2020 averages (sunshine, rain days, summer and winter temperatures, frost, wind, snow) and UKCP18 hot days; Met Éireann's 1991–2020 grids in Ireland |
| Northern Ireland and Ireland | NIMDM 2017, PSNI crime, LPS prices and rates, BSO lists, DE schools, Translink timetables, DAERA noise; Pobal HP 2022, CSO Garda crime, EEA air quality, EPA noise and radon, Transport for Ireland GTFS, ComReg gigabit coverage, ESB and Open Charge Map chargers (keyed), NIAH listed buildings |
| Community | English Indices of Deprivation 2025, WIMD 2025, SIMD 2020v2; Census 2021 and Scotland's Census 2022 (population, density, age, households, health, accommodation, cars, tenure, commuting, qualifications); Nomis claimant count (monthly); OHID life expectancy by MSOA |
| Housing | HM Land Registry Price Paid (LSOA medians) and Registers of Scotland small-area prices; ONS small-area income; council tax by billing authority (MHCLG, the Welsh and Scottish Governments); VOA housing stock by council tax band and build period, NRS dwellings by band |
| Safety | police.uk street crime, 36 months (Greater Manchester Police publishes none; flagged); the SIMD crime domain in Scotland; DfT road collisions (STATS19, 5 years) |
| Environment | Defra modelled NO₂ and PM2.5 (1km, population-weighted to LSOAs); OS Open Greenspace; Environment Agency flood risk by postcode (rivers, sea and surface water), NRW's risk areas in Wales and SEPA's flood maps in Scotland; Defra, Welsh Government and Scottish (SEPA-hosted) strategic noise maps (Lden and Lnight), DAERA's and the EPA's contours; ESA WorldCover tree cover; VIIRS night-time lights (manual download) |
| Health | NHS GP practices, patients registered by LSOA (real catchments), GP workforce; CQC ratings of GP practices and care homes; NHS dental practices; NHSBSA community pharmacies; Public Health Scotland's GP, dispenser and dental lists; a curated list of emergency departments (PHS and OpenStreetMap) |
| Education | Get Information About Schools and the Welsh and Scottish school lists; Ofsted inspections blended across the 2024 and 2025 framework changes; Ofsted nurseries and pre-schools, the Care Inspectorate's day care of children; DfE key stage 2 and 4 results, Welsh authority results, Scottish leaver tariffs |
| Transport | DfT Transport Connectivity Metric (the SIMD access domain in Scotland); NaPTAN stations and ORR station usage; Bus Open Data Service timetables (bus frequency); Ofcom broadband coverage |
| Amenities | OpenStreetMap points of interest; Food Standards Agency hygiene ratings; Overture Maps Places (pubs OSM lacks); Sport England Active Places |

## API and assistant

`make api` (or `uv run lix-api`) serves, on http://localhost:8000:

- a REST API under `/api/v1`: place search, area profiles, rankings with any weights and
  filters or by one measure, comparisons, nearest places (GPs, schools, well-run pubs, ...), score explanations,
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

The assistant has an eval suite ([api/evals/cases.yaml](../api/evals/cases.yaml): 58 cases
covering ranking with constraints, look-ups, follow-ups, ambiguous places, out-of-scope and
discriminatory requests, numbers grounded in tool results, analyst SQL, prompt injection,
profiles from a description, and replies in Welsh, Gaelic and Irish):

```bash
make eval MODEL=openrouter:anthropic/claude-haiku-5.5 CASES=family_leeds_budget,compare_two
make eval MODEL=openrouter:anthropic/claude-haiku-5.5    # all cases, about $0.05
```

Claude Haiku 5.5 passes the suite; Claude Opus 5.5 and Qwen 3.8 27B (open weights) passed
its October 2026 form; results, costs and what the evals caught
are in [docs/evals.md](evals.md).

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
([web/scripts/tiktok.mjs](../web/scripts/tiktok.mjs)). The browser suite runs against the
stack the same way (`LIX_E2E_STACK=docker npx playwright test`) once the web image is built
with `LIX_EXPOSE_MAP=1 make up BUILD=1`, which exposes the map to the tests; without it every
map test times out. `LIX_SMOKE=1` adds the smoke spec the release workflow runs.

## Docker

```bash
make up          # everything at http://localhost:3000 (LIX_PORT), data from ./data/serve
make up BUILD=1  # build the images from this checkout instead of pulling the release's
make up-demo     # the same on the committed demo dataset (fixtures/demo: six city cuts)
make down
```

One Caddy proxy ([docker/Caddyfile](../docker/Caddyfile)) is the only published port: it
serves the data files under `/data`, sends `/api/v1`, `/health` and `/mcp` to the API and
everything else to the web app, so the same images work on any host.

The data pack: `make data-pack` on a machine with a full build writes
`dist/lix-serve.tar.gz` (about 250 MB, with the licence notices). The api container is
capped at 3 GiB (`LIX_API_MEMORY`): the pack, a score cache of sixteen weightings and the
SQL views fit in about 2 GiB. Packs ship as monthly
data releases separate from code releases ([docs/data-releases.md](data-releases.md)):
`make data-download` fetches the newest one the checkout's serve schema can read
(`DATA_TAG=data-2026-10.6` picks one; a pack's schema must match the checkout's) and
`make data-unpack` restores a pack you have.
`make demo-data` re-cuts the demo dataset from a full build, and `lix validate serve`
checks any serve directory (CI runs the browser tests on the demo data).

## Front end

`web/` is a Next.js app (Node 24, see `.nvmrc`):

- a MapLibre map of every neighbourhood in the UK and Ireland from static PMTiles,
  coloured by percentile; weights are recomputed in the browser, so moving a slider
  recolours all 62,833 areas in under 200 ms
- an assistant chat ([CopilotKit](https://copilotkit.ai) over AG-UI) whose tool calls render as
  components: ranked lists, area profiles, side-by-side comparisons, nearby places, score
  explanations and SQL results. The assistant and the page share state, so it can move the map,
  outline areas, set the weight sliders and save a shortlist, and it sees what you change
- presets (balanced, family, young professional, retired, commuter), a "compare like with like" switch for
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

- More open datasets: private rents, jobs density, overcrowding, energy use and energy
  performance certificates, mobile coverage (v0.7.1)
- Answers composed as a page of cards rather than a chat reply (v0.8.0)
- Walking-network travel times instead of straight-line distances (v0.8.x), built on the
  OpenStreetMap ways table (`osm_ways`) that v0.7.0 stages

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

Set `LIX_DATA_DIR` to keep data somewhere other than `./data`. A one-nation build for
development (`LIX_NATIONS=I` while Ireland was added) is easiest in its own data root
with the raw downloads shared: `mkdir data-ie && ln -s ../data/raw data-ie/raw && ln -s
../data/manual data-ie/manual`, then `LIX_DATA_DIR=$PWD/data-ie LIX_NATIONS=I lix stage
--all`; the staged tables of the full build stay untouched.

## Refreshing the data

Every source has its own cadence (the `Updated` column of
[docs/data-sources.md](data-sources.md)), and the nightly source check opens a single
"Data refresh due" issue when editions have moved on. The monthly routine, on a machine
with the raw data (about 1–1.5 hours, mostly downloads):

```bash
make refresh-plan   # re-resolves every source and prints what is stale and why
make refresh        # fetch (conditional requests), re-stage and rebuild only what changed,
                    # then the docs, the anchor review and a fresh dist/lix-serve.tar.gz
```

`lix refresh` works from fingerprints: each staged table, the indicator table, the serve
files and the tiles record the lockfile checksums of the raw sources they were built from
(`data/staged/<slug>.meta.json`, `data/indicators/long.meta.json`, `data/serve/.meta.json`,
`data/serve/tiles/.meta.json`), so a run killed half way resumes, a second run after a
complete one finds nothing to do, and a new NSPL re-stages everything that reads
postcodes. A staged table's fingerprint also covers the fingerprints of the staged tables
it reads and the source of its own stager (with the stage-package functions it calls), so
a table rebuilt for its code or its boundaries re-stages its readers too, while a table
re-staged by hand from the same inputs and code changes nothing downstream. A helper
outside the stage package (`geo/*.py`) is left out of that hash on purpose: after fixing
one, `lix stage --slug <table> --with-dependents` re-stages the table and its readers in
run order, or `lix refresh --force` rebuilds everything. After a change to the fingerprint
scheme itself, `lix stage --rehash` rewrites the sidecars without re-staging. A source
that fails to resolve or fetch stops the run before anything is rebuilt: a publisher that
moved or reshaped a file is a registry fix, not something to build around. A publisher's
maintenance window is the other case: `lix refresh --keep-going` (`make refresh
REFRESH_FLAGS=--keep-going`) builds from the editions the lock names for the sources it
could not reach and lists them at the end. The run's plan, timings and changed sources
go to `data/logs/refresh-*.json`.

After a refresh: read `data/serve/qa.md` (the correlation report, the stale list and the
anchor review), fix any anchor the data has genuinely moved past, add a `period:` to the
registry for a source whose period the docs now show as a download date, and commit the
lockfile ("Data 2026-10: …"). Then `make data-release` publishes `dist/` as the next
`data-YYYY-MM` pre-release with generated notes (what each source's data is for, its
edition, and what changed since the last release). Re-record the demo cassettes
(`make demo-record`) when the storyline rehearsal fails or a stated fact is no longer
true, and at least once a quarter; re-cut the demo dataset (`make demo-data`) only with a
code release that changes the serve layout.

Changelog policy: data releases carry their generated notes and `CHANGELOG.md` stays
code-only; a serve schema bump names the first data release of the new schema, which must
exist before the code is tagged (`release.yml` refuses otherwise).

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
