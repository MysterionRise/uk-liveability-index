"""Release notes for a data release, generated from the manifest.

Code releases keep CHANGELOG.md; data releases say what data they carry: how recent it
gets, and per source the period, the edition and whether it changed since the previous
data release (its manifest, when one is given).
"""

from datetime import datetime

SCHEMA_MARKER = "<!-- lix-data schema={schema} -->"


def _period(source: dict) -> str:
    period = source.get("period") or {}
    return period.get("label") or ""


def _version(source: dict) -> str:
    v = source.get("fetched_version") or source.get("version")
    if v is None:
        return ""
    v = str(v)
    return v if len(v) <= 48 else v[:45] + "…"


def _changed(slug: str, source: dict, previous: dict | None) -> str:
    if previous is None:
        return ""
    before = previous.get("sources", {}).get(slug)
    if before is None:
        return "new source"
    if (before.get("fetched_version") or before.get("version")) != (
        source.get("fetched_version") or source.get("version")
    ):
        return "new edition"
    if _period(before) != _period(source):
        return "new period"
    return ""


def release_notes(manifest: dict, previous: dict | None, tag: str) -> str:
    """Markdown for the GitHub release body."""
    sources = manifest.get("sources", {})
    built = manifest.get("generated_at", "")
    try:
        built_text = datetime.fromisoformat(built).strftime("%-d %B %Y")
    except ValueError:
        built_text = built
    data_to = manifest.get("data_to")
    geography = manifest.get("geography", {})
    counts = geography.get("area_counts", {})
    nations = ", ".join(
        f"{geography.get('nations', {}).get(n, {}).get('name', n)} {c:,}" for n, c in counts.items()
    )
    stale = sorted(s for s, v in sources.items() if v.get("stale"))
    changed = [s for s in sources if _changed(s, sources[s], previous)]

    lines = [
        f"Data release {tag}: the data pack (`lix-serve.tar.gz`) for code releases that read "
        f"serve schema {manifest.get('schema_version')}.",
        "",
        f"- Built {built_text}" + (f"; data to {data_to}" if data_to else ""),
        f"- {manifest.get('lsoa_count', 0):,} areas ({nations})" if counts else "",
        f"- {len(sources)} sources"
        + (f", {len(changed)} changed since the previous release" if previous is not None else ""),
        f"- Stale: {', '.join(stale)}" if stale else "- No source is behind its latest edition",
        "",
        "```\nmake data-download DATA_TAG=" + tag + "\n```",
        "",
        "| Source | Data for | Edition | Changed |",
        "|---|---|---|---|",
    ]
    for slug in sorted(sources):
        s = sources[slug]
        lines.append(f"| `{slug}` | {_period(s)} | {_version(s)} | {_changed(slug, s, previous)} |")
    lines += ["", SCHEMA_MARKER.format(schema=manifest.get("schema_version"))]
    return "\n".join(line for line in lines if line is not None) + "\n"
