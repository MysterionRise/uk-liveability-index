# API + assistant + MCP. Installs only lix-core and lix-api (no GDAL/osmium stack).
# uv is a named stage, not `COPY --from=<image>`: Dependabot's docker updater reads FROM
# lines only, so the image named in a COPY was never bumped (#253)
FROM ghcr.io/astral-sh/uv:0.9 AS uv

FROM python:3.11-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv

# Dependencies first, for layer caching
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/
COPY pipeline/pyproject.toml pipeline/
COPY api/pyproject.toml api/
RUN uv sync --frozen --no-dev --package lix-api --no-install-workspace

COPY core core
COPY api api
COPY config config
RUN uv sync --frozen --no-dev --package lix-api

# Data is mounted at /data (scores, lookups and tiles live in /data/serve)
ENV LIX_ROOT=/app LIX_DATA_DIR=/data LIX_HOST=0.0.0.0 LIX_SERVE_DATA=0 PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["lix-api"]
