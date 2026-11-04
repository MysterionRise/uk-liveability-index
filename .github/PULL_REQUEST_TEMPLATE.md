## What and why

<!-- One change per pull request. Say what changed and why; for a score change, which
places moved and how (`uv run lix qa places` writes the review). -->

## Checks

- [ ] `make lint`, `make test` and `make web-test` pass
- [ ] `uv run lix docs --check` passes (generated docs are current)
- [ ] A changelog line under "Unreleased" in CHANGELOG.md
- [ ] For a new source: registry entry with licence and attribution, a staged table, a
      methodology paragraph and a place anchor (see CONTRIBUTING.md)
