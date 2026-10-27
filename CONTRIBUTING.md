# Contributing

Thanks for helping. Bug reports, data corrections, new open sources and fixes are all
welcome. Open an issue first for anything large, so we can agree the approach.

## Setting up

You need [uv](https://docs.astral.sh/uv/) (Python 3.11), Node 24 (see `.nvmrc`) and
Docker for the full stack.

```bash
make install         # Python workspace: core, pipeline, api
make web-install     # front end
make data-download   # the release's data pack, instead of the 20 GB build
make dev             # API on :8000 and front end on :3000
```

`make dev` uses the scripted assistant (`LIX_MODEL=test`) unless `.env` has a model key.
For a smaller dataset, `LIX_DATA_DIR=fixtures/demo make dev` runs on Leeds, Brighton, Cardiff, Edinburgh, Belfast and Dublin.
How the parts fit together is in [docs/development.md](docs/development.md).

## Checks

Run these before opening a pull request. CI runs them all; Python tests never touch the
network.

```bash
make lint test   # ruff, pytest
make web-test    # typecheck, eslint, vitest (including scoring parity with Python)
make e2e         # Playwright against the local build
make schemas     # after changing API models: regenerate contracts/ and web types
```

If you change the assistant's instructions or tools, run the evals on a cheap model
(`make eval MODEL=openrouter:anthropic/claude-haiku-5.5`, about $0.05) and say how they
went in the pull request.

## Adding a data source

1. **Check the licence.** Only open licences: OGL, ODbL, CC BY, CC0, CDLA, Apache. Add the
   source to [config/datasets.yaml](config/datasets.yaml) with its licence, attribution and
   `licence_verified: true` once you have read the licence on the publisher's site.
2. **Find its file.** Use an existing access type (`http`, `arcgis_item`,
   `govuk_attachment`, `html_link`, `ckan`, `nomis`, `overture`) so `lix resolve` keeps
   finding the current version.
3. **Stage it** with a stager in `pipeline/src/lix_pipeline/stage/`, plus a test on a small
   fixture (see `pipeline/tests/test_stage_sources.py`).
4. **Build the indicator** in [config/indicators.yaml](config/indicators.yaml), preferably
   with a generic builder from `pipeline/src/lix_pipeline/indicators/common.py` (`nearest`,
   `access`, `count_within`, `share`, `msoa`). Make it `context` rather than `scored` if it
   closely tracks an indicator that is already scored.
5. **Check it.** `make score tiles validate`, then read the QA report
   (`data/serve/qa.md`) for coverage and within-theme correlations.
6. **Document it.** `make docs` regenerates [ATTRIBUTION.md](ATTRIBUTION.md) and
   [docs/data-sources.md](docs/data-sources.md); describe any method choices in
   [docs/methodology.md](docs/methodology.md).

## Pull requests

- Keep each one to a single change, with tests for new behaviour.
- Describe what changed and why, and for score changes, which places moved and how
  (`uv run lix qa places` writes the face-validity review).
- By contributing, you agree your code is released under the [MIT licence](LICENSE) and
  any data under the project's [data licence](DATA-LICENCE.md).

## Translating

The interface and the data labels are translated through two catalogues per language,
reviewed by fluent speakers before they lose their "draft" label. How to review a draft
or add a language is in [docs/i18n.md](docs/i18n.md).
