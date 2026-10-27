.PHONY: up down up-demo gif tiktok quickstart data-download data-pack data-unpack data-release eval demo demo-offline demo-check demo-record install resolve fetch stage indicators score tiles build-data validate demo-data docs schemas api web-install web web-test e2e dev test lint format refresh-plan refresh

PY_DIRS := core pipeline api
APP_URL := http://localhost:$(or $(LIX_PORT),3000)
# The release version (pyproject.toml); docker compose pulls the images of this version
VERSION ?= $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
export LIX_VERSION ?= $(VERSION)
SHA256 := $(shell command -v sha256sum >/dev/null && echo sha256sum || echo "shasum -a 256")
# Local runs read model keys from .env when it exists (see .env.example)
ENV_FILE := $(if $(wildcard .env),--env-file $(CURDIR)/.env,)

install:
	uv sync

# Re-resolve every source to its current upstream file (updates config/datasets.lock.json)
resolve:
	uv run $(ENV_FILE) lix resolve --all

# Download every dataset pinned in the lockfile into data/raw/ (Price Paid alone is 5.5 GB)
fetch:
	uv run $(ENV_FILE) lix fetch --all

# Stage every downloaded dataset to data/staged/*.parquet; nspl runs first (price_paid needs it)
stage:
	uv run lix stage --all

# Build data/indicators/long.parquet from the staged tables
indicators:
	uv run lix indicators

# Write data/serve/ (features, browser scores, manifest) and the QA report
score:
	uv run lix score

# Vector map tiles (PMTiles) for the browser
tiles:
	uv run lix tiles

# Everything from download to scores and tiles
build-data: fetch stage indicators score tiles validate

# What a refresh would rebuild (re-resolves every source, then compares the lockfile with
# the sidecars under data/); `make refresh` does it: only stale stages, indicators, scores
# and tiles are rebuilt, then the docs, the anchor review and a fresh data pack
refresh-plan:
	uv run $(ENV_FILE) lix refresh --dry-run

refresh:
	uv run $(ENV_FILE) lix refresh $(REFRESH_FLAGS)
	uv run lix docs
	uv run lix qa places
	$(MAKE) data-pack

# JSON Schema of the API models (contracts/schemas.json) for the front end's types
schemas:
	uv run python -m lix_api.schemas
	cd web && npm run types

# Run the API on :8000 (set LIX_MODEL, e.g. anthropic:claude-opus-5-5, or "test")
api:
	LIX_RELOAD=1 uv run $(ENV_FILE) lix-api

# Front end (Node 24, see .nvmrc) on :3000
web-install:
	cd web && npm ci

web:
	cd web && npm run dev

# Typecheck, lint and unit tests (incl. the scoring parity test against Python)
web-test:
	cd web && npm run typecheck && npm run lint && npm test

# Browser tests against the local build with the scripted assistant (needs make score tiles)
e2e:
	cd web && npx playwright test

# API (scripted assistant unless LIX_MODEL is set) and front end together
dev:
	($(if $(ENV_FILE),,LIX_MODEL=test) uv run $(ENV_FILE) lix-api &) && cd web && npm run dev

validate:
	uv run lix validate config
	uv run lix validate geo
	uv run lix validate serve
	uv run lix validate places

# Small dataset (each nation's demo_upper in config/nations.yaml: Leeds, Brighton, Cardiff,
# Edinburgh) cut from a full build, for CI end-to-end tests
demo-data:
	uv run lix demo-data --out fixtures/demo

# Regenerate docs/data-sources.md and ATTRIBUTION.md from config/datasets.yaml
docs:
	uv run lix docs

test:
	uv run pytest

lint:
	uv run ruff check $(PY_DIRS)
	uv run ruff format --check $(PY_DIRS)

format:
	uv run ruff format $(PY_DIRS)
	uv run ruff check --fix $(PY_DIRS)

# Docker: everything at http://localhost:3000 (LIX_PORT), data from ./data/serve. Uses the
# release's images from GitHub's registry, building any that aren't published; BUILD=1
# builds from this checkout instead
up:
	$(if $(BUILD),,docker compose pull --quiet --ignore-pull-failures api web proxy)
	docker compose up -d $(if $(BUILD),--build,) --remove-orphans --wait
	@echo "Running at $(APP_URL); stop with make down"

# The same on the small committed demo dataset (Leeds, Brighton, Cardiff, Edinburgh, Belfast, Dublin)
up-demo:
	LIX_SERVE_DIR=./fixtures/demo/serve $(MAKE) up

down:
	docker compose down

# The data pack: a built data/serve (about 250 MB) with its licence notices, so another
# machine can skip the 25 GB raw download. Packs ship as monthly data releases
# (data-YYYY-MM, GitHub pre-releases) separate from code releases; a code release reads
# the newest pack with the serve schema it understands (SERVE_SCHEMA_VERSION):
#   make data-pack                       write dist/lix-serve.tar.gz (+ .sha256)
#   make data-download [DATA_TAG=...]    fetch the newest compatible pack and unpack it
#   make data-unpack [PACK=...]          unpack a pack you have
#   make data-release                    publish dist/ as the next data release
PACK ?= dist/lix-serve.tar.gz
RELEASES := https://github.com/MysterionRise/uk-liveability-index/releases/download
SCHEMA := $(shell sed -n 's/^SERVE_SCHEMA_VERSION = //p' core/src/lix_core/config.py)
data-pack:
	uv run lix validate serve
	mkdir -p dist
	tar -czf $(PACK) -C data serve -C $(CURDIR) ATTRIBUTION.md DATA-LICENCE.md
	cd $(dir $(PACK)) && $(SHA256) $(notdir $(PACK)) > $(notdir $(PACK)).sha256
	@echo "Wrote $(PACK) ($$(du -h $(PACK) | cut -f1))"

data-download:
	$(eval DATA_TAG := $(or $(DATA_TAG),$(shell python3 scripts/data_release.py latest --schema $(SCHEMA))))
	@test -n "$(DATA_TAG)" || (echo "No data release with serve schema $(SCHEMA) yet: make build-data, or make data-unpack PACK=..." && exit 1)
	@echo "Data release $(DATA_TAG)"
	mkdir -p $(dir $(PACK))
	curl -fL --progress-bar -o $(PACK) $(RELEASES)/$(DATA_TAG)/$(notdir $(PACK))
	curl -fsSL -o $(PACK).sha256 $(RELEASES)/$(DATA_TAG)/$(notdir $(PACK)).sha256
	$(MAKE) data-unpack

data-release:
	scripts/data-release.sh

data-unpack:
	@test -f $(PACK) || (echo "No $(PACK): make data-download, or make data-unpack PACK=path/to/lix-serve.tar.gz" && exit 1)
	cd $(dir $(PACK)) && $(SHA256) -c $(notdir $(PACK)).sha256
	rm -rf data/serve
	mkdir -p data
	tar -C data -xzf $(PACK)
	@# The full checks need the Python workspace (uv sync); a quickstart has only Docker
	@if command -v uv >/dev/null && [ -d .venv ]; then uv run lix validate serve; fi
	@echo "Data ready in data/serve (built $$(sed -n 's/.*"generated_at": *"\([^"]*\)".*/\1/p' data/serve/manifest.json | head -1))"

# From a fresh clone to the app in the browser: Docker, make and curl are all it needs.
# NO_OPEN=1 skips opening the browser
quickstart:
	@test -f data/serve/manifest.json || $(MAKE) data-download
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example: add a model key there to use the assistant")
	$(MAKE) up
	@$(if $(NO_OPEN),true,(open $(APP_URL) || xdg-open $(APP_URL)) >/dev/null 2>&1 || true)

# Assistant eval on the full build (api/evals/cases.yaml). Real models cost money:
#   make eval MODEL=openrouter:anthropic/claude-opus-5.5 CASES=family_leeds_budget,compare_two
eval:
	cd api && uv run $(ENV_FILE) python -m evals.run --model $(MODEL) $(if $(CASES),--cases $(CASES),)

# --- Stakeholder demo (docs/demo.md) ---------------------------------------------------
# make demo          real model from .env (Docker, built from this checkout)
# make demo-offline  recorded conversations (config/demo_cassettes.json): no network needed
# make demo-record   re-record those conversations with a real model
# NO_OPEN=1 skips opening the browser (rehearsal scripts)
demo: demo-check
	docker compose up -d --build --remove-orphans --wait
	@curl -sf "$(APP_URL)/api/v1/areas/LS6%203AA" >/dev/null && echo "API warmed up"
	@echo "Demo running at $(APP_URL) (assistant: $${LIX_MODEL:-from .env}); stop with make down"
	@$(if $(NO_OPEN),true,(open $(APP_URL) || xdg-open $(APP_URL)) >/dev/null 2>&1 || true)

demo-offline:
	LIX_MODEL=replay $(MAKE) demo

demo-check:
	@test -f data/serve/manifest.json || (echo "No built data: make build-data, or make data-unpack PACK=..." && exit 1)
	@uv run lix validate serve
	@test -f config/demo_cassettes.json || echo "Note: no recorded demo (make demo-record); offline mode falls back to the scripted assistant"
	@if [ "$$LIX_MODEL" != "replay" ] && ! grep -qE '^(OPENROUTER|ANTHROPIC|OPENAI|GEMINI)_API_KEY=.+' .env 2>/dev/null; then \
		echo "Note: no model key in .env; the assistant will say it isn't configured (or use make demo-offline)"; fi

demo-record:
	uv run $(ENV_FILE) python -m lix_api.agent.replay record --model $${MODEL:-openrouter:anthropic/claude-opus-5.5}


# Screen recordings of the interface for the README and release (docs/media/*.gif and
# dist/walkthrough.mp4): the Docker stack with the recorded assistant answers. Needs ffmpeg
gif: demo-check
	LIX_MODEL=replay LIX_REPLAY_DELAY=0.025 docker compose up -d --build --wait
	cd web && LIX_RECORD=1 LIX_E2E_STACK=docker npx playwright test record
	./scripts/make-gif.sh

# A vertical, TikTok-style clip of the phone layout with captions, zooms and speed ramps
# (docs/media/tiktok.gif, and dist/tiktok.mp4 for uploading)
tiktok: demo-check
	LIX_MODEL=replay LIX_REPLAY_DELAY=0.02 docker compose up -d --build --wait
	cd web && node scripts/tiktok.mjs
	cp dist/tiktok.gif docs/media/tiktok.gif
