"""Find a file by following links from a landing page (NHS England, Defra, police.uk...).

Publication pages list releases; release pages list files. Announced releases often
have a page before they have files, so each step's candidates are tried in order and
the first that leads to a match for the next step wins.
"""

import re
from datetime import date
from urllib.parse import urljoin

import requests

from lix_core.config import HtmlLinkAccess, LinkStep
from lix_pipeline.fetch.dates import find_date
from lix_pipeline.fetch.outage import raise_if_maintenance


def _candidates(html: str, base_url: str, step: LinkStep) -> list[str]:
    links = [urljoin(base_url, m.group(1)) for m in re.finditer(step.pattern, html)]
    links = list(dict.fromkeys(links))  # de-duplicate, keep page order
    if step.order == "desc":
        links.sort(reverse=True)
    elif step.order == "date_desc":
        links.sort(key=lambda u: find_date(u) or date.min, reverse=True)
    return links


def resolve_link(access: HtmlLinkAccess, session: requests.Session) -> dict:
    """Return the lock "resolved" section for the file the steps lead to."""

    def walk(url: str, steps: list[LinkStep], trail: list[str]) -> dict | None:
        resp = session.get(url, timeout=(30, 60))
        raise_if_maintenance(resp, url)
        resp.raise_for_status()
        candidates = _candidates(resp.text, url, steps[0])
        if len(steps) == 1:
            if not candidates:
                return None
            found = candidates[0]
            release = find_date(found) or next(
                (d for d in (find_date(t) for t in reversed(trail)) if d), None
            )
            return {"url": found, "release": release.isoformat() if release else None}
        for candidate in candidates:
            result = walk(candidate, steps[1:], trail + [candidate])
            if result:
                return result
        return None

    result = walk(access.page, access.steps, [access.page])
    if result is None:
        raise RuntimeError(
            f"No link matching {[s.pattern for s in access.steps]} from {access.page}"
        )
    return result
