"""Fetch datasets: resolve each registry entry to a concrete URL, then download it.

resolve(slug)  →  lock["resolved"]   (url + version; written to datasets.lock.json)
fetch(slug)    →  data/raw/{slug}/   (lock["fetched"]: sha256, size, when)
"""

import fnmatch
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import requests

from lix_core.config import DatasetSpec, HttpAccess, load_registry
from lix_core.log import setup_logging
from lix_core.paths import data_dir, get_project_root
from lix_pipeline.fetch import arcgis, ckan, govuk, html, nomis, overture, wcs, wfs
from lix_pipeline.fetch.http import _read_meta, _write_meta, download, download_set
from lix_pipeline.fetch.lock import read_lock, update_entry
from lix_pipeline.fetch.session import make_session

logger = setup_logging("fetch")


class ManualDownloadRequired(Exception):
    """A dataset that has to be downloaded by hand is missing."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe(url: str, session: requests.Session, timeout_s: int = 60) -> requests.Response:
    """HEAD the URL, falling back to a streamed GET for servers that mishandle HEAD.

    No retries here: some servers answer every HEAD with an error (GIAS returns 500,
    NHS ODS reports 405), and backing off on those just wastes a minute per probe.
    """
    try:
        resp = requests.head(
            url, headers=session.headers, allow_redirects=True, timeout=(30, timeout_s)
        )
        if resp.ok:
            return resp
    except requests.RequestException:
        pass
    resp = session.get(url, stream=True, timeout=(30, timeout_s))
    resp.close()
    return resp


def _resolve_http(access: HttpAccess, session: requests.Session) -> tuple[str, requests.Response]:
    """The URL to fetch and its HEAD response; for dated URLs, the newest that exists."""
    if access.tiles:
        # One URL pattern, many files: the first tile (in sorted order, so that listing
        # them differently is not a new edition) stands for the set
        url = access.url.format(tile=sorted(access.tiles)[0])
        head = _probe(url, session, access.timeout_s)
        head.raise_for_status()
        return url, head
    if not access.date_format:
        head = _probe(access.url, session, access.timeout_s)
        head.raise_for_status()
        return access.url, head
    today = date.today()
    for back in range(access.lookback_days + 1):
        day = today - timedelta(days=back)
        url = access.url.replace("{date}", day.strftime(access.date_format))
        head = _probe(url, session)
        if head.ok:
            return url, head
    raise RuntimeError(f"No file for {access.url} in the last {access.lookback_days} days")


def resolve(slug: str, spec: DatasetSpec, session: requests.Session) -> dict:
    """Turn a registry entry into a concrete URL plus a version string."""
    access = spec.access
    if access.type == "http":
        url, head = _resolve_http(access, session)
        resolved = {
            "url": url,
            # Dated URLs are their own version; otherwise trust the server's validators
            "version": url
            if access.date_format
            else head.headers.get("Last-Modified") or head.headers.get("ETag"),
        }
    elif access.type == "arcgis_item":
        resolved = arcgis.resolve_item(access, session)
        resolved["version"] = resolved["modified"]
    elif access.type == "html_link":
        resolved = html.resolve_link(access, session)
        resolved["version"] = resolved["url"]
    elif access.type == "govuk_attachment":
        resolved = govuk.resolve_attachment(access, session)
        # Republishing a file gives it a new /media/<id>/ URL, so the URL is the version
        resolved["version"] = resolved["url"]
    elif access.type == "ckan":
        resolved = ckan.resolve_resource(access, session)
        resolved["version"] = resolved["url"]
    elif access.type == "nomis":
        resolved = nomis.resolve_query(access, session)
    elif access.type == "overture":
        resolved = overture.resolve_query(access, session)
    elif access.type == "wcs":
        resolved = wcs.resolve_coverage(access, session)
    elif access.type == "wfs":
        resolved = wfs.resolve_layer(access, session)
    elif access.type == "curated":
        path = _curated_path(access.path)
        # The file's own checksum is its version, so an edited list is a new edition
        resolved = {"url": None, "version": _sha256(path)[:16], "path": access.path}
    else:  # manual
        resolved = {"url": None, "version": None, "instructions": access.instructions}
    return {**resolved, "resolved_at": _now()}


def _curated_path(path: str) -> Path:
    """A curated list's file: ``path`` is relative to the project root (absolute in tests)."""
    file = Path(path)
    if not file.is_absolute():
        file = get_project_root() / file
    if not file.is_file():
        raise FileNotFoundError(f"Curated list {path} is missing")
    return file


def _fetch_curated(slug: str, spec: DatasetSpec) -> dict:
    """Copy a curated list under data/raw/<slug>/ like a download, with its checksum."""
    import shutil

    source = _curated_path(spec.access.path)
    dest_dir = data_dir("raw") / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}.{spec.format}"
    shutil.copyfile(source, dest)
    sha = _sha256(dest)
    _write_meta(
        dest_dir,
        {
            "slug": slug,
            "url": None,
            "version": sha[:16],
            "downloaded_at": _now(),
            "bytes": dest.stat().st_size,
            "sha256": sha,
            "completed": True,
        },
    )
    return {"sha256": sha, "bytes": dest.stat().st_size, "version": sha[:16], "fetched_at": _now()}


def resolve_and_lock(slug: str, spec: DatasetSpec, session: requests.Session) -> dict:
    resolved = resolve(slug, spec, session)
    previous = read_lock().get(slug, {}).get("resolved", {})
    if previous and previous.get("version") != resolved["version"]:
        logger.info(
            f"[{slug}] New upstream version: {previous.get('version')} → {resolved['version']}"
        )
    update_entry(slug, "resolved", resolved)
    return resolved


def _fetch_manual(slug: str, spec: DatasetSpec) -> dict:
    folder = data_dir("manual") / slug
    files = sorted(
        p
        for p in folder.glob("**/*")
        if p.is_file() and any(fnmatch.fnmatch(p.name, pat) for pat in spec.access.expect)
    )
    if not files:
        raise ManualDownloadRequired(
            f"[{slug}] {spec.title} must be downloaded by hand.\n"
            f"  {spec.access.instructions.strip()}\n"
            f"  Put the file(s) matching {spec.access.expect} in {folder}/"
        )
    return {
        "files": {str(p.relative_to(folder)): _sha256(p) for p in files},
        "fetched_at": _now(),
    }


def _fetch_paged(slug: str, spec: DatasetSpec, resolved: dict, force: bool, download_to):
    """Download a paged API query (ArcGIS layer, Nomis) into one file, with caching."""
    dest_dir = data_dir("raw") / slug
    out_path = dest_dir / f"{slug}.{spec.format}"
    meta = _read_meta(dest_dir)
    same = meta.get("url") == resolved["url"] and meta.get("version") == resolved["version"]
    if not force and meta.get("completed") and same and out_path.exists():
        logger.info(f"[{slug}] Already downloaded — skipping (use --force to re-download)")
        return meta
    rows = download_to(out_path)
    meta = {
        "slug": slug,
        "url": resolved["url"],
        "version": resolved["version"],
        "downloaded_at": _now(),
        "rows": rows,
        "bytes": out_path.stat().st_size,
        "sha256": _sha256(out_path),
        "completed": True,
    }
    _write_meta(dest_dir, meta)
    logger.info(f"[{slug}] Saved {rows:,} records to {out_path}")
    return meta


def fetch(
    slug: str,
    force: bool = False,
    strict: bool = False,
    session: requests.Session | None = None,
    registry: dict[str, DatasetSpec] | None = None,
    revalidate: bool = False,
) -> dict:
    """Download one dataset as pinned in the lockfile (resolving it first if unpinned).

    With ``strict``, a download whose checksum differs from the lockfile's is an error
    (for reproducible rebuilds); otherwise it is logged and the lockfile updated. With
    ``revalidate``, a file already on disk is checked against the server (a conditional
    request, or a fresh download compared by checksum where the server gives no
    validator). The returned dict is the lock's ``fetched`` section plus ``changed``:
    whether the data differs from what the lockfile recorded before.
    """
    if registry is None:
        registry = load_registry()
    if slug not in registry:
        raise ValueError(f"Unknown dataset slug: {slug!r}. Available: {sorted(registry)}")
    spec = registry[slug]
    session = session or make_session()

    if not spec.licence_verified:
        logger.warning(f"[{slug}] Licence not yet verified ({spec.licence}) — check before use")

    entry = read_lock().get(slug, {})
    if spec.access.type == "manual":
        fetched = _fetch_manual(slug, spec)
        changed = entry.get("fetched", {}).get("files") != fetched["files"]
        update_entry(slug, "fetched", fetched)
        return {**fetched, "changed": changed}
    if spec.access.type == "curated":
        resolve_and_lock(slug, spec, session)
        fetched = _fetch_curated(slug, spec)
        changed = entry.get("fetched", {}).get("sha256") != fetched["sha256"]
        update_entry(slug, "fetched", fetched)
        logger.info(f"[{slug}] Copied the curated list ({fetched['bytes']:,} bytes)")
        return {**fetched, "changed": changed}

    resolved = entry.get("resolved") or {}
    # A lock entry written for another access type (or an older resolver) lacks what the
    # tile and page fetchers need: resolve again rather than trust it
    needed = {"wcs": ("envelope", "res"), "wfs": ("count",)}.get(spec.access.type, ())
    if not resolved or any(k not in resolved for k in needed):
        resolved = resolve_and_lock(slug, spec, session)

    if spec.access.type == "arcgis_item" and spec.access.mode == "featureserver":
        meta = _fetch_paged(
            slug,
            spec,
            resolved,
            force,
            lambda out: arcgis.fetch_featureserver(resolved["url"], spec.access, out, session),
        )
    elif spec.access.type == "nomis":
        meta = _fetch_paged(
            slug, spec, resolved, force, lambda out: nomis.fetch_query(spec.access, out, session)
        )
    elif spec.access.type == "overture":
        meta = _fetch_paged(
            slug,
            spec,
            resolved,
            force,
            lambda out: overture.fetch_query(spec.access, resolved["url"], out),
        )
    elif spec.access.type == "wcs":
        meta = wcs.fetch_tiles(slug, spec.access, resolved, spec.format, force, session)
    elif spec.access.type == "wfs":
        urls = wfs.page_urls(spec.access, spec.format, resolved["count"])
        meta = download_set(
            slug, urls, spec.format, resolved["version"], force, session, spec.access.timeout_s
        )
    elif spec.access.type == "http" and spec.access.tiles:
        urls = {t: spec.access.url.format(tile=t) for t in spec.access.tiles}
        meta = download_set(slug, urls, spec.format, resolved.get("version"), force, session)
    else:
        meta = download(
            slug,
            resolved["url"],
            spec.format,
            extract=spec.extract,
            force=force,
            version=resolved.get("version"),
            session=session,
            read_timeout_s=max(300, getattr(spec.access, "timeout_s", 0)),
            revalidate=revalidate,
        )

    previous = entry.get("fetched", {}).get("sha256")
    changed = not previous or (bool(meta.get("sha256")) and previous != meta["sha256"])
    if previous and meta.get("sha256") and previous != meta["sha256"]:
        msg = f"[{slug}] Checksum differs from lockfile ({previous[:12]}… → {meta['sha256'][:12]}…)"
        if strict:
            raise RuntimeError(msg + "; rerun without --strict to accept the new data")
        logger.warning(msg)

    fetched = {
        k: meta.get(k)
        for k in ("sha256", "bytes", "rows", "etag", "last_modified", "extracted", "files")
        if meta.get(k) is not None
    }
    # The edition the data came from, so a later resolve can tell the pack lacks a newer one
    fetched["version"] = resolved.get("version")
    fetched["fetched_at"] = meta.get("downloaded_at") or _now()
    update_entry(slug, "fetched", fetched)
    return {**fetched, "changed": changed}


def select(
    registry: dict[str, DatasetSpec],
    slugs: list[str] | None = None,
    theme: str | None = None,
    priority: str | None = None,
) -> list[str]:
    """Pick registry slugs by explicit list, theme and/or priority (in registry order)."""
    chosen = []
    for slug, spec in registry.items():
        if slugs and slug not in slugs:
            continue
        if theme and spec.theme != theme:
            continue
        if priority and spec.priority != priority:
            continue
        chosen.append(slug)
    unknown = set(slugs or []) - set(registry)
    if unknown:
        raise ValueError(f"Unknown dataset slug(s): {sorted(unknown)}")
    return chosen


LinkStatus = Literal["ok", "warn", "fail"]

# Access types whose URL is one plain file: a conditional probe and a size comparison
# say whether it changed. API queries, tile sets and pages are only probed for reachability
PLAIN_FILE_TYPES = ("http", "html_link", "govuk_attachment", "ckan")


def _reachable(
    url: str, session: requests.Session, validators: dict | None = None
) -> tuple[int, int | None]:
    """The status of the lightest request that proves the file is there, and the file's
    size when the server states it. With ``validators`` (the lockfile's ETag or
    Last-Modified) the request is conditional, so an unchanged file answers 304."""
    if url.startswith("s3://"):
        return overture.probe(url, session), None
    headers = {"Range": "bytes=0-0"}
    if validators and validators.get("etag"):
        headers["If-None-Match"] = validators["etag"]
    elif validators and validators.get("last_modified"):
        headers["If-Modified-Since"] = validators["last_modified"]
    resp = session.get(url, headers=headers, stream=True, timeout=60)
    resp.close()
    size = None
    content_range = resp.headers.get("Content-Range", "")
    if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
        size = int(content_range.rsplit("/", 1)[1])
    elif resp.status_code == 200 and (resp.headers.get("Content-Length") or "").isdigit():
        size = int(resp.headers["Content-Length"])
    return resp.status_code, size


def check_source(slug: str, spec: DatasetSpec, session: requests.Session) -> dict:
    """Re-resolve a dataset and confirm its file is reachable; used by the nightly check.

    Returns ``status`` (ok / warn / fail), a ``note``, the resolved and locked versions,
    and ``changed``: whether the pack built from the lockfile lacks a newer edition
    (a new resolved version, a changed validator, or a changed size where the server
    gives no validator).

    A 403 is a warning, not a failure: NHS Digital, Ofcom and NaPTAN refuse cloud addresses
    such as GitHub's runners while serving everyone else, and the check is after files that
    moved or vanished, not firewalls. A fetch from a blocked network still fails loudly.
    """
    entry = read_lock().get(slug, {})
    locked = entry.get("resolved", {})
    fetched = entry.get("fetched", {})
    out = {
        "slug": slug,
        "status": "ok",
        "note": "",
        "version": None,
        "locked_version": locked.get("version"),
        "fetched_at": fetched.get("fetched_at"),
        "changed": False,
    }

    def done(status: LinkStatus, note: str, changed: bool = False) -> dict:
        return {**out, "status": status, "note": note, "changed": changed}

    try:
        resolved = resolve(slug, spec, session)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            return done("warn", f"not verified: 403 from {e.response.url}")
        return done("fail", f"resolve failed: {e}")
    except Exception as e:  # report every failure, keep checking the rest
        return done("fail", f"resolve failed: {e}")
    out["version"] = resolved.get("version")
    new_version = bool(locked) and locked.get("version") != resolved["version"]
    if spec.access.type == "curated":
        return done("ok", "curated list" + (" (edited)" if new_version else ""), new_version)
    if resolved["url"] is None:
        return done("ok", "manual download")

    plain = spec.access.type in PLAIN_FILE_TYPES and not getattr(spec.access, "tiles", None)
    validators = {k: fetched.get(k) for k in ("etag", "last_modified")} if plain else None
    try:
        status, size = _reachable(resolved["url"], session, validators)
    except requests.RequestException as e:
        return done("fail", f"unreachable: {e}")
    if status == 403:
        return done("warn", f"not verified: 403 from {resolved['url']}")
    if status == 304:
        return done("ok", f"unchanged ({json.dumps(resolved.get('version'))})", new_version)
    if status >= 400:
        return done("fail", f"HTTP {status}")

    version = json.dumps(resolved.get("version"))
    if new_version:
        return done("ok", f"new upstream version ({version})", True)
    if plain and validators and any(validators.values()):
        # The server has a validator and did not answer 304: the file changed
        return done("ok", f"new upstream version: file changed ({version})", True)
    if plain and size is not None and fetched.get("bytes") and not fetched.get("files"):
        if size != fetched["bytes"]:
            return done(
                "ok", f"new upstream version: size {fetched['bytes']:,} → {size:,} bytes", True
            )
        return done("ok", f"ok, size unchanged ({version})")
    return done("ok", f"ok ({version})")


def check_link(slug: str, spec: DatasetSpec, session: requests.Session) -> tuple[LinkStatus, str]:
    """``check_source`` as (status, note)."""
    result = check_source(slug, spec, session)
    return result["status"], result["note"]
