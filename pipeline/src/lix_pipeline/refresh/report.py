"""The nightly "Data refresh due" issue: what the source check found that a refresh
would pick up, written so the maintainer can act on it from the issue alone."""

from datetime import date, datetime, timezone

from lix_core.config import DatasetSpec
from lix_pipeline.serve.scores import is_stale

TITLE = "Data refresh due"
LABEL = "data-refresh"


def _row(slug: str, note: str) -> str:
    return f"- `{slug}`: {note}"


def issue_body(
    checks: dict, lock: dict, registry: dict[str, DatasetSpec], today: date | None = None
) -> tuple[str, bool]:
    """(markdown body, pending) from ``lix resolve --check --json`` output, the lockfile
    and the registry. ``pending`` is True when a refresh would change something or a
    source failed; "not verified" warnings alone do not count."""
    today = today or date.today()
    now = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    results = {r["slug"]: r for r in checks.get("results", [])}

    new_versions = [
        _row(
            slug,
            f"{r['note']} (built from {r.get('locked_version') or 'an unknown edition'}, "
            f"fetched {(r.get('fetched_at') or '')[:10] or 'never'})",
        )
        for slug, r in sorted(results.items())
        if r.get("status") == "ok" and r.get("changed")
    ]
    stale = []
    for slug, spec in sorted(registry.items()):
        fetched = lock.get(slug, {}).get("fetched") or {}
        if is_stale(fetched.get("fetched_at"), spec.cadence, now):
            stale.append(
                _row(slug, f"fetched {fetched['fetched_at'][:10]}, published {spec.cadence}")
            )
    not_verified = [
        _row(slug, r["note"]) for slug, r in sorted(results.items()) if r.get("status") == "warn"
    ]
    failures = [
        _row(slug, r["note"]) for slug, r in sorted(results.items()) if r.get("status") == "fail"
    ]
    pending = bool(new_versions or stale or failures)

    checked = (checks.get("checked_at") or now.isoformat())[:10]
    lines = [
        f"Nightly source check on {checked}: {len(results)} sources. "
        "This issue is rewritten by every check and closed when nothing is pending.",
        "",
        "## New upstream versions",
        "",
        *(new_versions or ["None."]),
        "",
        "## Older than their cadence",
        "",
        *(stale or ["None."]),
        "",
        "## Not verified from this network",
        "",
        *(not_verified or ["None."]),
        "",
        "## Failures",
        "",
        *(failures or ["None."]),
        "",
        "## What to do",
        "",
        "On a machine with the data: `make refresh-plan`, then `make refresh`; commit the "
        "lockfile; `make data-release`. A failure usually means a publisher moved or "
        "reshaped a file: fix its registry entry first.",
    ]
    return "\n".join(lines) + "\n", pending
