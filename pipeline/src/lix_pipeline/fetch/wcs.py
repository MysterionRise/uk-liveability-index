"""WCS 2.0.1 rasters (Defra's strategic noise maps): describe the coverage, fetch it as tiles.

The servers are GeoServer: axes are labelled E and N, subsets are in the coverage's CRS,
output is GeoTIFF (deflate on request) and a tile much above 8192 pixels times out.
"""

import re
from urllib.parse import quote

import requests

from lix_core.config import WcsAccess


def describe_url(access: WcsAccess) -> str:
    return (
        f"{access.url}?service=WCS&version=2.0.1&request=DescribeCoverage"
        f"&coverageId={quote(access.coverage_id, safe='')}"
    )


def _floats(text: str) -> list[float]:
    return [float(v) for v in text.split()]


def describe(access: WcsAccess, session: requests.Session) -> dict:
    """Envelope (E0, N0, E1, N1), resolution, grid size and nodata of the coverage."""
    resp = session.get(describe_url(access), timeout=120)
    resp.raise_for_status()
    x = resp.text
    if "ExceptionReport" in x[:500] or "<gml:lowerCorner>" not in x:
        raise RuntimeError(f"DescribeCoverage failed for {access.coverage_id}: {x[:300]}")
    lower = _floats(re.search(r"<gml:lowerCorner>([^<]+)", x).group(1))
    upper = _floats(re.search(r"<gml:upperCorner>([^<]+)", x).group(1))
    high = re.search(r"<gml:high>([^<]+)", x).group(1).split()
    offsets = re.findall(r"<gml:offsetVector[^>]*>([^<]+)", x)
    res = next(abs(v) for v in _floats(offsets[0]) if v)
    nil = re.search(r"<swe:nilValue[^>]*>\s*([-\d.]+)", x)
    return {
        "envelope": [lower[0], lower[1], upper[0], upper[1]],
        "res": res,
        "grid": [int(high[0]) + 1, int(high[1]) + 1],
        "nodata": float(nil.group(1)) if nil else None,
    }


def resolve_coverage(access: WcsAccess, session: requests.Session) -> dict:
    d = describe(access, session)
    w, h = d["grid"]
    return {
        "url": describe_url(access),
        "version": f"{access.coverage_id} {w}x{h}@{d['res']:g}m",
        **d,
    }


def tiles(
    envelope: list[float], res: float, tile_px: int, bbox: list[float] | None = None
) -> list[tuple[float, float, float, float]]:
    """Square tiles of ``tile_px`` pixels covering the envelope (clipped to ``bbox``)."""
    e0, n0, e1, n1 = envelope
    if bbox:
        e0, n0 = max(e0, bbox[0]), max(n0, bbox[1])
        e1, n1 = min(e1, bbox[2]), min(n1, bbox[3])
    step = tile_px * res
    out = []
    e = e0
    while e < e1:
        n = n0
        while n < n1:
            out.append((e, n, min(e + step, e1), min(n + step, n1)))
            n += step
        e += step
    return out


def tile_name(tile: tuple[float, float, float, float]) -> str:
    return f"E{int(tile[0])}_N{int(tile[1])}"


def coverage_url(access: WcsAccess, tile: tuple[float, float, float, float]) -> str:
    e0, n0, e1, n1 = tile
    url = (
        f"{access.url}?service=WCS&version=2.0.1&request=GetCoverage"
        f"&coverageId={quote(access.coverage_id, safe='')}"
        f"&subset=E({e0:g},{e1:g})&subset=N({n0:g},{n1:g})&format=image/tiff"
    )
    if access.compression:
        url += f"&compression={access.compression}"
    return url


def tile_urls(access: WcsAccess, resolved: dict) -> dict[str, str]:
    """Tile name → GetCoverage URL for the whole coverage."""
    return {
        tile_name(t): coverage_url(access, t)
        for t in tiles(resolved["envelope"], resolved["res"], access.tile_px, access.bbox)
    }
