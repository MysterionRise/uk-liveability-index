"""WCS 2.0.1 rasters (Defra's strategic noise maps): describe the coverage, fetch it as tiles.

The servers are GeoServer: axes are labelled E and N, subsets are in the coverage's CRS,
output is GeoTIFF (deflate on request) and a tile much above 8192 pixels times out.
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from lix_core.config import WcsAccess
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.fetch.http import _read_meta, _write_meta, download_file

logger = setup_logging("fetch.wcs")


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


Tile = tuple[float, float, float, float]


def quarters(tile: Tile) -> list[Tile]:
    e0, n0, e1, n1 = tile
    em, nm = (e0 + e1) / 2, (n0 + n1) / 2
    return [(e0, n0, em, nm), (em, n0, e1, nm), (e0, nm, em, n1), (em, nm, e1, n1)]


def _have(dest: Path, fmt: str, entry) -> bool:
    names = entry if isinstance(entry, list) else None
    if names is not None:
        return all((dest / f"{q}.{fmt}").exists() for q in names)
    return isinstance(entry, str) and bool(entry)


def fetch_tiles(
    slug: str,
    access: WcsAccess,
    resolved: dict,
    fmt: str,
    force: bool = False,
    session: requests.Session | None = None,
) -> dict:
    """Fetch every tile of the coverage into data/raw/{slug}/{E}_{N}.{fmt}.

    A dense tile can take longer than the server's gateway allows; rather than retry it
    behind the session's silent back-off, a tile that fails is fetched as its four
    quarters, recorded under the tile's name. Tiles already on disk are kept unless
    ``force``; the set is complete when every tile (or its quarters) is there.
    """
    dest = data_dir("raw") / slug
    meta = _read_meta(dest)
    same = meta.get("version") == resolved["version"]
    if not force and meta.get("completed") and same:
        logger.info(f"[{slug}] Already downloaded — skipping (use --force to re-download)")
        return meta
    dest.mkdir(parents=True, exist_ok=True)
    files: dict = dict(meta.get("files") or {}) if same and not force else {}
    plain = requests.Session()  # no retry adapter: a slow tile should fail, then split
    if session is not None:
        plain.headers.update(session.headers)
    todo = tiles(resolved["envelope"], resolved["res"], access.tile_px, access.bbox)
    for i, tile in enumerate(todo, 1):
        name = tile_name(tile)
        if _have(dest, fmt, files.get(name)) and (
            isinstance(files[name], list) or (dest / f"{name}.{fmt}").exists()
        ):
            continue
        logger.info(f"[{slug}] {i}/{len(todo)} {name}")
        try:
            files[name] = download_file(
                plain, coverage_url(access, tile), dest / f"{name}.{fmt}", fmt, attempts=1
            )
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"[{slug}] {name}: {e}; fetching it as four quarter tiles")
            names = []
            for q in quarters(tile):
                qname = tile_name(q)
                download_file(plain, coverage_url(access, q), dest / f"{qname}.{fmt}", fmt)
                names.append(qname)
            files[name] = names
        _write_meta(
            dest, {"slug": slug, "version": resolved["version"], "files": files, "completed": False}
        )
    digest = hashlib.sha256(
        "\n".join(f"{k} {v}" for k, v in sorted(files.items())).encode()
    ).hexdigest()
    paths = [
        dest / f"{q}.{fmt}"
        for entry, name in ((files[n], n) for n in files)
        for q in (entry if isinstance(entry, list) else [name])
    ]
    new_meta = {
        "slug": slug,
        "url": resolved["url"],
        "version": resolved["version"],
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "bytes": sum(p.stat().st_size for p in paths),
        "sha256": digest,
        "completed": True,
    }
    _write_meta(dest, new_meta)
    logger.info(f"[{slug}] {len(paths)} files, {new_meta['bytes'] / 1e6:.0f} MB")
    return new_meta
