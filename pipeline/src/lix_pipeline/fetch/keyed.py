"""Open data behind a free registered key.

The key lives in an environment variable named by the registry entry and travels on a
session of its own, as a header or a query parameter, so the URL the lockfile, the
``.meta.json`` and the logs record never carries it. Paged JSON APIs are read page by
page into one file of items.
"""

import json
import os
from pathlib import Path

import requests

from lix_core.config import ApiKeyAccess
from lix_core.log import setup_logging
from lix_pipeline.fetch.session import make_session

logger = setup_logging("fetch.keyed")

REDACTED = "***"


def key_for(access: ApiKeyAccess) -> str | None:
    """The key from the environment, or None when it is not set."""
    return os.environ.get(access.env, "").strip() or None


def keyed_session(access: ApiKeyAccess, session: requests.Session | None, key: str):
    """A session that sends the key with every request, otherwise like ``session``."""
    keyed = make_session()
    if session is not None:
        keyed.headers.update(session.headers)
    if access.header:
        keyed.headers[access.header] = f"{access.prefix}{key}"
    else:
        keyed.params = {access.query: key}
    return keyed


def redact(text: str, key: str) -> str:
    """``text`` with the key blanked, for error messages that quote a URL."""
    return text.replace(key, REDACTED) if key else text


def fetch_json_pages(access: ApiKeyAccess, url: str, out: Path, session: requests.Session) -> int:
    """Read a paged JSON API into ``out`` (a JSON list of items) and return the count.

    Pages run from 1 until one comes back short or empty; ``items_key`` names the list
    inside each page's object (a page that is itself a list needs none).
    """
    items: list = []
    page = 1
    while True:
        params = {access.page_param: page}
        if access.per_page_param:
            params[access.per_page_param] = access.per_page
        resp = session.get(url, params=params, timeout=(30, access.timeout_s))
        resp.raise_for_status()
        body = resp.json()
        batch = body.get(access.items_key, []) if access.items_key else body
        if not isinstance(batch, list):
            raise ValueError(f"page {page} of {url}: expected a list, got {type(batch).__name__}")
        items.extend(batch)
        logger.info(f"page {page}: {len(batch):,} items")
        if not batch or (access.per_page_param and len(batch) < access.per_page):
            break
        page += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False))
    return len(items)
