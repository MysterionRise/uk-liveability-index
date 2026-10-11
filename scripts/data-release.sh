#!/usr/bin/env bash
# Publish the built data as the next monthly data release (a GitHub pre-release tagged
# data-YYYY-MM[.n]) with the pack, its checksum and the manifest as assets.
#
#   make data-release            after make refresh (or make build-data)
#   DATA_TAG=data-2026-10.1 make data-release   choose the tag yourself
#
# Refuses to run with an uncommitted lockfile, so the release always matches a commit
# that records exactly which source editions went into it.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! git diff --quiet -- config/datasets.lock.json; then
  echo "config/datasets.lock.json has uncommitted changes: commit the refresh first" >&2
  exit 1
fi
test -f data/serve/manifest.json || { echo "No built data in data/serve" >&2; exit 1; }

tag="${DATA_TAG:-$(python3 scripts/data_release.py next-tag)}"
schema="$(sed -n 's/^SERVE_SCHEMA_VERSION = //p' core/src/lix_core/config.py)"
echo "Data release $tag (serve schema $schema)"

# The previous release of this schema, for the "changed since" column
previous=""
if prev_tag="$(python3 scripts/data_release.py latest --schema "$schema" 2>/dev/null)"; then
  mkdir -p dist/previous
  if gh release download "$prev_tag" --pattern manifest.json --dir dist/previous --clobber 2>/dev/null; then
    previous="dist/previous/manifest.json"
    echo "Comparing with $prev_tag"
  fi
fi

uv run lix data-release --tag "$tag" ${previous:+--previous "$previous"} --notes dist/data-release-notes.md
make data-pack
cp data/serve/manifest.json dist/manifest.json

gh release create "$tag" --prerelease --title "Data $tag" --notes-file dist/data-release-notes.md \
  dist/lix-serve.tar.gz dist/lix-serve.tar.gz.sha256 dist/manifest.json

built="$(sed -n 's/.*"generated_at": *"\([^"]*\)".*/\1/p' data/serve/manifest.json | head -1 | cut -c1-10)"
data_to="$(sed -n 's/.*"data_to": *"\([^"]*\)".*/\1/p' data/serve/manifest.json | head -1)"
line="| [$tag](https://github.com/MysterionRise/uk-liveability-index/releases/tag/$tag) | $built | ${data_to:-} | $schema |"
if ! grep -q "\[$tag\]" docs/data-releases.md; then
  printf '%s\n' "$line" >> docs/data-releases.md
fi
echo "Published $tag; docs/data-releases.md updated (commit it)"
