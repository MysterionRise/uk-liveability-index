"""WFS 2.0.0 layers in pages (NRW's surface-water flood areas: 2.8 million polygons)."""

import re
from urllib.parse import urlencode

import requests

from lix_core.config import WfsAccess


def _params(access: WfsAccess, **extra) -> dict:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": access.typename,
    }
    if access.cql_filter:
        params["CQL_FILTER"] = access.cql_filter
    params.update(extra)
    return params


def hits_url(access: WfsAccess) -> str:
    return f"{access.url}?{urlencode(_params(access, resultType='hits'))}"


def count_features(access: WfsAccess, session: requests.Session) -> int:
    resp = session.get(hits_url(access), timeout=(30, access.timeout_s))
    resp.raise_for_status()
    m = re.search(r'numberMatched="(\d+)"', resp.text)
    if not m:
        raise RuntimeError(f"No numberMatched in the hits reply for {access.typename}")
    return int(m.group(1))


def resolve_layer(access: WfsAccess, session: requests.Session) -> dict:
    n = count_features(access, session)
    return {"url": hits_url(access), "version": f"{access.typename} {n} features", "count": n}


def page_url(access: WfsAccess, fmt: str, start: int) -> str:
    output = {"gpkg": "gpkg", "geojson": "application/json"}[fmt]
    params = _params(
        access,
        outputFormat=output,
        srsName=access.srs,
        sortBy=access.sort_by,
        count=access.page_size,
        startIndex=start,
    )
    return f"{access.url}?{urlencode(params)}"


def page_urls(access: WfsAccess, fmt: str, count: int) -> dict[str, str]:
    """Page name → GetFeature URL covering ``count`` features."""
    starts = range(0, max(count, 1), access.page_size)
    return {f"page_{i:03d}": page_url(access, fmt, start) for i, start in enumerate(starts)}
