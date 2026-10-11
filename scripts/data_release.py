#!/usr/bin/env python3
"""Find and name data releases: the monthly packs published as GitHub pre-releases.

Standard library only, so `make quickstart` needs python3 but not uv or gh.

    data_release.py latest --schema 2        the newest data-* tag whose pack has that
                                             serve schema (empty output, exit 1 if none)
    data_release.py assets --tag data-2026-10  the asset download URLs of a release
    data_release.py next-tag                 the tag the next release should take

A data release's body carries ``<!-- lix-data schema=N -->``; releases are read through
the public API (no token needed for public repositories; GH_TOKEN is sent when set).
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date

REPO = "MysterionRise/uk-liveability-index"
API = "https://api.github.com"
TAG = re.compile(r"^data-(\d{4})-(\d{2})(?:\.(\d+))?$")
SCHEMA = re.compile(r"<!--\s*lix-data\s+schema=(\d+)\s*-->")


def parse_schema(body: str | None) -> int | None:
    m = SCHEMA.search(body or "")
    return int(m.group(1)) if m else None


def tag_key(tag: str) -> tuple[int, int, int] | None:
    m = TAG.match(tag)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def pick_latest(releases: list[dict], schema: int) -> dict | None:
    """The newest data release (by tag, not by publish date) whose body names ``schema``;
    drafts are skipped, pre-releases are what data releases are."""
    candidates = [
        r
        for r in releases
        if not r.get("draft")
        and tag_key(r.get("tag_name", ""))
        and parse_schema(r.get("body")) == schema
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: tag_key(r["tag_name"]))


def next_tag(existing: list[str], today: date | None = None) -> str:
    """``data-YYYY-MM`` for this month, or ``data-YYYY-MM.n`` when that tag exists."""
    today = today or date.today()
    base = f"data-{today.year}-{today.month:02d}"
    taken = {t for t in existing if t == base or t.startswith(base + ".")}
    if base not in taken:
        return base
    n = 1
    while f"{base}.{n}" in taken:
        n += 1
    return f"{base}.{n}"


def fetch_releases(repo: str = REPO, per_page: int = 100, pages: int = 5) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "lix-data-release"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out: list[dict] = []
    for page in range(1, pages + 1):
        req = urllib.request.Request(
            f"{API}/repos/{repo}/releases?per_page={per_page}&page={page}", headers=headers
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            batch = json.load(resp)
        out.extend(batch)
        if len(batch) < per_page:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=REPO)
    sub = parser.add_subparsers(dest="command", required=True)
    latest = sub.add_parser("latest", help="newest data release tag for a serve schema")
    latest.add_argument("--schema", type=int, required=True)
    assets = sub.add_parser("assets", help="asset download URLs of a release")
    assets.add_argument("--tag", required=True)
    sub.add_parser("next-tag", help="the tag the next data release should take")
    args = parser.parse_args(argv)

    releases = fetch_releases(args.repo)
    if args.command == "latest":
        found = pick_latest(releases, args.schema)
        if not found:
            print(f"No data release with serve schema {args.schema}", file=sys.stderr)
            return 1
        print(found["tag_name"])
    elif args.command == "assets":
        release = next((r for r in releases if r.get("tag_name") == args.tag), None)
        if not release:
            print(f"No release {args.tag}", file=sys.stderr)
            return 1
        for asset in release.get("assets", []):
            print(asset["browser_download_url"])
    else:
        print(next_tag([r.get("tag_name", "") for r in releases]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
