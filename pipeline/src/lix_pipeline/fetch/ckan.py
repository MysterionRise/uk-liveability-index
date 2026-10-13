"""CKAN data portals: find a package's current resource via the action API."""

import re

import requests

from lix_core.config import CkanAccess
from lix_pipeline.fetch.dates import find_date


def _dated(resource: dict):
    found = find_date(resource.get("url") or "") or find_date(resource.get("name") or "")
    return found.isoformat() if found else ""


def resolve_resource(access: CkanAccess, session: requests.Session) -> dict:
    """Return the lock "resolved" section for the latest resource matching ``resource_regex``."""
    resp = session.get(
        f"{access.api.rstrip('/')}/package_show", params={"id": access.package}, timeout=(30, 60)
    )
    resp.raise_for_status()
    resources = resp.json()["result"]["resources"]
    matches = [
        r
        for r in resources
        if re.search(access.resource_regex, r.get("name") or "")
        or re.search(access.resource_regex, r.get("url") or "")
    ]
    if not matches:
        names = [r.get("name") for r in resources]
        raise RuntimeError(
            f"No resource of CKAN package {access.package!r} matches "
            f"{access.resource_regex!r}. Resources: {names[:20]}"
        )
    # Newest by the date in the file name or title (July 2026 beats January 2026, which
    # alphabetical order would not), then by name
    latest = max(matches, key=lambda r: (_dated(r), r.get("name") or ""))
    return {
        "url": latest["url"],
        "title": latest.get("name"),
        "modified": latest.get("last_modified") or latest.get("created"),
    }
