"""ArcGIS Online (ONS Open Geography Portal): find the current item, fetch feature layers.

ONS deletes an item when it publishes a new version (V3 → V4), which silently breaks
pinned URLs. ``resolve_item`` searches for the newest matching item instead, falling
back to the pinned id. ``fetch_featureserver`` pages through a layer's query API,
which works even when the Hub's pre-generated download is missing or stale.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import polars as pl
import requests

from lix_core.config import ArcgisItemAccess, ArcgisLayerAccess
from lix_core.log import setup_logging

logger = setup_logging("fetch.arcgis")

ARCGIS = "https://www.arcgis.com/sharing/rest"


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _json(session: requests.Session, url: str, params: dict, fmt: str = "json") -> dict:
    resp = session.get(url, params={"f": fmt, **params}, timeout=(30, 120))
    resp.raise_for_status()
    data = resp.json()
    # ArcGIS reports errors with HTTP 200 and an "error" object
    if "error" in data:
        raise RuntimeError(f"ArcGIS error from {url}: {data['error']}")
    return data


def _item(session: requests.Session, item_id: str) -> dict:
    return _json(session, f"{ARCGIS}/content/items/{item_id}", {})


def resolve_item(access: ArcgisItemAccess, session: requests.Session) -> dict:
    """Find the item to download: newest search match, else the pinned item.

    Returns the lock "resolved" section: url, item_id, title, modified, and for
    feature services the service URL.
    """
    item = None
    if access.query:
        results = _json(
            session,
            f"{ARCGIS}/search",
            {"q": access.query, "sortField": "modified", "sortOrder": "desc", "num": 100},
        ).get("results", [])
        matches = [
            r
            for r in results
            if (access.item_type is None or r.get("type") == access.item_type)
            and (access.title_regex is None or re.match(access.title_regex, r.get("title", "")))
        ]
        if matches:
            item = max(matches, key=lambda r: r.get("modified", 0))
        else:
            logger.warning(f"No search match for {access.query!r}; using pinned {access.pin}")
    if item is None:
        item = _item(session, access.pin)
    elif item["id"] != access.pin:
        logger.info(f"Newer item than pin: {item['title']!r} ({item['id']})")

    item_id = item["id"]
    if access.mode == "data":
        url = f"{ARCGIS}/content/items/{item_id}/data"
    elif access.mode == "hub_export":
        hub = access.hub.rstrip("/")
        url = f"{hub}/api/download/v1/items/{item_id}/{access.export_format}?layers={access.layer}"
    else:
        url = f"{item['url'].rstrip('/')}/{access.layer}"

    return {
        "url": url,
        "item_id": item_id,
        "title": item.get("title"),
        "modified": _ms_to_iso(item.get("modified")),
    }


def resolve_layer(access: ArcgisLayerAccess, session: requests.Session) -> dict:
    """A layer addressed by its URL: the layer's last edit (else its service item) is the
    version, so the lock notices a republished list."""
    layer = _json(session, access.url, {})
    edited = (layer.get("editingInfo") or {}).get("lastEditDate")
    version = _ms_to_iso(edited) if edited else layer.get("serviceItemId") or access.url
    return {"url": access.url, "version": version, "title": layer.get("name")}


def _query_page(
    session: requests.Session, layer_url: str, params: dict, offset: int, size: int, fmt: str
) -> list[dict]:
    """Fetch ``size`` records from ``offset``, following short pages.

    Servers may return fewer records than asked (transfer limits on big polygons), so
    keep asking from where the last response stopped.
    """
    rows: list[dict] = []
    while len(rows) < size:
        data = _json(
            session,
            f"{layer_url}/query",
            {**params, "resultOffset": offset + len(rows), "resultRecordCount": size - len(rows)},
            fmt=fmt,
        )
        features = data.get("features", [])
        if not features:
            break
        rows.extend(features)
    return rows


def fetch_featureserver(
    layer_url: str,
    access: ArcgisItemAccess | ArcgisLayerAccess,
    out_path: Path,
    session: requests.Session,
    workers: int = 8,
) -> int:
    """Download every record of a feature layer to Parquet (tables/points) or GeoPackage.

    Points get ``x``/``y`` columns in ``access.out_sr`` (BNG by default); polygons are
    written to a GeoPackage in that CRS. Returns the number of records.
    """
    layer = _json(session, layer_url, {})
    page_size = int(layer.get("maxRecordCount") or 1000)
    oid_field = layer.get("objectIdField") or next(
        f["name"] for f in layer["fields"] if f["type"] == "esriFieldTypeOID"
    )
    expected = _json(session, f"{layer_url}/query", {"where": "1=1", "returnCountOnly": "true"})[
        "count"
    ]

    polygons = access.geometry == "polygon"
    params = {
        "where": "1=1",
        "outFields": ",".join(access.fields) if access.fields else "*",
        "orderByFields": oid_field,  # stable order makes offset paging safe
        "returnGeometry": "false" if access.geometry == "none" else "true",
        # GeoJSON is always WGS84; polygons are reprojected to out_sr locally
        "outSR": 4326 if polygons else access.out_sr,
    }
    fmt = "geojson" if polygons else "json"
    paged = (layer.get("advancedQueryCapabilities") or {}).get("supportsPagination", True)
    if paged:
        offsets = range(0, expected, page_size)
        logger.info(f"Fetching {expected:,} records in {len(offsets)} pages from {layer_url}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pages = list(
                pool.map(
                    lambda off: _query_page(session, layer_url, params, off, page_size, fmt),
                    offsets,
                )
            )
    else:
        # An older ArcGIS Server (Historic Environment Scotland's INSPIRE services) has no
        # result offset: read ranges of object ids instead
        ids = sorted(
            _json(session, f"{layer_url}/query", {"where": "1=1", "returnIdsOnly": "true"}).get(
                "objectIds"
            )
            or []  # a server answers null, not [], for an empty layer
        )
        chunks = [ids[i : i + page_size] for i in range(0, len(ids), page_size)]
        logger.info(f"Fetching {expected:,} records in {len(chunks)} id ranges from {layer_url}")
        by_range = {k: v for k, v in params.items() if k != "orderByFields"}

        def _ids(chunk: list[int]) -> list[dict]:
            where = f"{oid_field} >= {chunk[0]} AND {oid_field} <= {chunk[-1]}"
            return _json(session, f"{layer_url}/query", {**by_range, "where": where}, fmt=fmt).get(
                "features", []
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            pages = list(pool.map(_ids, chunks))
    features = [f for page in pages for f in page]
    if len(features) != expected:
        raise RuntimeError(f"Expected {expected:,} records from {layer_url}, got {len(features):,}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the real extension last: GDAL picks the driver from it
    tmp = out_path.with_name(f"{out_path.stem}.part{out_path.suffix}")
    if polygons:
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326").to_crs(access.out_sr)
        gdf.to_file(tmp, driver="GPKG")
    else:
        records = []
        for f in features:
            row = dict(f["attributes"])
            if access.geometry == "point":
                geom = f.get("geometry") or {}
                row["x"], row["y"] = geom.get("x"), geom.get("y")
            records.append(row)
        pl.DataFrame(records, infer_schema_length=None).write_parquet(tmp)
    tmp.replace(out_path)
    return expected
