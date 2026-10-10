"""HTTP download engine: cached, resumable, checksummed downloads into data/raw/{slug}/."""

import fnmatch
import hashlib
import json
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from tqdm import tqdm

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.fetch.session import make_session

logger = setup_logging("download")

CHUNK_SIZE = 1024 * 1024

# Leading bytes each binary format must start with. ArcGIS and GOV.UK sometimes answer
# a bad request with HTTP 200 and a JSON or HTML error body, so we check before trusting it.
MAGIC_BYTES = {
    "zip": [b"PK\x03\x04"],
    "xlsx": [b"PK\x03\x04"],
    "ods": [b"PK\x03\x04"],
    "gpkg": [b"SQLite format 3\x00"],
    "parquet": [b"PAR1"],
    # Classic and BigTIFF headers, either byte order
    "tif": [b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"],
}
ERROR_BODY_PREFIXES = (b"{", b"<!doctype", b"<html", b"<?xml")


def _meta_path(dest_dir: Path) -> Path:
    return dest_dir / ".meta.json"


def _read_meta(dest_dir: Path) -> dict:
    mp = _meta_path(dest_dir)
    if mp.exists():
        with open(mp) as f:
            return json.load(f)
    return {}


def _write_meta(dest_dir: Path, meta: dict) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with open(_meta_path(dest_dir), "w") as f:
        json.dump(meta, f, indent=2)


def _safe_extract(
    zf: zipfile.ZipFile, dest_dir: Path, patterns: list[str] | None = None
) -> list[str]:
    """Extract a ZIP archive with path traversal protection (Zip Slip guard).

    If ``patterns`` is given, only members matching one of the glob patterns are extracted.
    Returns the names of the extracted members.
    """
    dest_dir = dest_dir.resolve()
    members = [m for m in zf.infolist() if not m.is_dir()]
    for member in zf.infolist():
        member_path = (dest_dir / member.filename).resolve()
        # is_relative_to, not str.startswith: "data/nspl_evil" starts with "data/nspl"
        if not member_path.is_relative_to(dest_dir):
            raise ValueError(
                f"ZIP entry {member.filename!r} would extract outside destination directory"
            )
    if patterns:
        members = [m for m in members if any(fnmatch.fnmatch(m.filename, p) for p in patterns)]
        if not members:
            raise ValueError(f"No ZIP entries match {patterns!r}")
    for member in members:
        zf.extract(member, dest_dir)
    return [m.filename for m in members]


def _check_payload(path: Path, fmt: str) -> None:
    """Raise if the downloaded file is an error page instead of the expected format."""
    with open(path, "rb") as f:
        head = f.read(64)
    expected = MAGIC_BYTES.get(fmt)
    if fmt == "pbf":
        # OSM PBF: a 4-byte length, then a BlobHeader whose type is "OSMHeader"
        if b"OSMHeader" not in head[:20]:
            raise ValueError(f"Downloaded file is not an OSM PBF (starts with {head[:32]!r})")
    elif expected is not None:
        if not any(head.startswith(m) for m in expected):
            raise ValueError(f"Downloaded file is not a valid {fmt} (starts with {head[:32]!r})")
    elif head.lstrip().lower().startswith(ERROR_BODY_PREFIXES) and fmt not in (
        "json",
        "geojson",
        "xml",
    ):
        raise ValueError(f"Downloaded {fmt} looks like an error page: {head[:32]!r}")


def _get(
    session: requests.Session,
    url: str,
    headers: dict,
    export_wait_s: float,
    poll_s: float,
    read_timeout_s: float = 300,
) -> requests.Response:
    """GET that waits out ArcGIS Hub's "202: download file is being generated" replies."""
    deadline = time.monotonic() + export_wait_s
    while True:
        # (connect, read) timeout; large files like price_paid (5.5GB) need generous reads
        resp = session.get(url, headers=headers, stream=True, timeout=(30, read_timeout_s))
        if resp.status_code != 202:
            return resp
        resp.close()
        if time.monotonic() > deadline:
            raise TimeoutError(f"Export still being generated after {export_wait_s:.0f}s: {url}")
        logger.info(f"Server is still generating the file; retrying in {poll_s:.0f}s")
        time.sleep(poll_s)


def download(
    slug: str,
    url: str,
    fmt: str,
    extract: list[str] | None = None,
    force: bool = False,
    version: str | None = None,
    session: requests.Session | None = None,
    export_wait_s: float = 900,
    poll_s: float = 15,
    read_timeout_s: float = 300,
) -> dict:
    """Download ``url`` into data/raw/{slug}/{slug}.{fmt} (extracting zips) and return its meta.

    Skips the download if the same URL (and ``version``, when the resolver knows one) was
    already fetched completely. Downloads stream
    to ``{file}.part`` and resume with an HTTP Range request if a previous attempt was
    interrupted. The finished file is moved into place atomically.
    """
    dest_dir = data_dir("raw") / slug
    session = session or make_session()

    # Check cache: a different URL or version means a new upstream release
    meta = _read_meta(dest_dir)
    # Plain HTTP sources are versioned by Last-Modified, which older metas recorded alone
    meta_version = meta.get("version") or meta.get("last_modified")
    same_source = meta.get("url") == url and (version is None or meta_version == version)
    if not force and meta.get("completed") and same_source:
        logger.info(f"[{slug}] Already downloaded — skipping (use --force to re-download)")
        return meta

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_file = dest_dir / f"{slug}.{fmt}"
    part_file = out_file.with_name(out_file.name + ".part")
    if force and part_file.exists():
        part_file.unlink()

    headers = {}
    resume_from = part_file.stat().st_size if part_file.exists() else 0
    # Only resume against the same remote version: If-Range makes the server send the
    # whole file (200) instead of a range if it changed since the partial was written.
    partial_validator = meta.get("partial_etag") or meta.get("partial_last_modified")
    if resume_from and partial_validator:
        headers["Range"] = f"bytes={resume_from}-"
        headers["If-Range"] = partial_validator
    elif resume_from:
        resume_from = 0
    if not resume_from and not force and meta.get("last_modified") and same_source:
        headers["If-Modified-Since"] = meta["last_modified"]

    logger.info(f"[{slug}] Downloading from {url}")
    resp = _get(session, url, headers, export_wait_s, poll_s, read_timeout_s)

    if resp.status_code == 304:
        logger.info(f"[{slug}] Not modified since last download — skipping")
        # Mark as completed since server confirms data hasn't changed
        meta["completed"] = True
        _write_meta(dest_dir, meta)
        return meta

    resp.raise_for_status()

    sha256 = hashlib.sha256()
    if resume_from and resp.status_code == 206:
        logger.info(f"[{slug}] Resuming at {resume_from / 1e6:.1f} MB")
        with open(part_file, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                sha256.update(chunk)
        mode = "ab"
    else:
        # Nothing to resume, or the server sent the whole file (version changed or
        # Range unsupported): start over
        resume_from = 0
        mode = "wb"
        _write_meta(
            dest_dir,
            {
                "slug": slug,
                "url": url,
                "completed": False,
                "partial_etag": resp.headers.get("ETag"),
                "partial_last_modified": resp.headers.get("Last-Modified"),
            },
        )

    try:
        total_size = int(resp.headers.get("content-length", 0)) + resume_from
    except (ValueError, TypeError):
        total_size = 0

    try:
        with (
            open(part_file, mode) as f,
            tqdm(
                total=total_size,
                initial=resume_from,
                unit="B",
                unit_scale=True,
                desc=slug,
                disable=total_size == 0,
            ) as pbar,
        ):
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)
                sha256.update(chunk)
                pbar.update(len(chunk))
    except BaseException:
        # Keep the .part file so the next run can resume; never leave a file at out_file
        logger.error(f"[{slug}] Download interrupted — partial data kept in {part_file.name}")
        raise

    try:
        _check_payload(part_file, fmt)
    except ValueError:
        part_file.unlink()
        raise

    os.replace(part_file, out_file)
    size = out_file.stat().st_size
    logger.info(f"[{slug}] Saved to {out_file} ({size / 1e6:.1f} MB)")

    extracted = None
    if fmt == "zip":
        logger.info(f"[{slug}] Extracting ZIP archive...")
        with zipfile.ZipFile(out_file) as zf:
            extracted = _safe_extract(zf, dest_dir, extract)
        out_file.unlink()
        logger.info(f"[{slug}] Extracted {len(extracted)} file(s) to {dest_dir}")

    new_meta = {
        "slug": slug,
        "url": url,
        "version": version,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "last_modified": resp.headers.get("Last-Modified"),
        "etag": resp.headers.get("ETag"),
        "bytes": size,
        "sha256": sha256.hexdigest(),
        "extracted": extracted,
        "completed": True,
    }
    _write_meta(dest_dir, new_meta)

    return new_meta


def download_file(
    session: requests.Session,
    url: str,
    path: Path,
    fmt: str,
    attempts: int = 3,
    read_timeout_s: float = 900,
) -> str:
    """Stream ``url`` to ``path`` (through a .part file) and return its sha256.

    Retries transient server errors and timeouts: a WCS server rendering a large tile
    answers 504 now and then. The payload is checked against the format's magic bytes.
    """
    part = path.with_name(path.name + ".part")
    for attempt in range(1, attempts + 1):
        try:
            sha256 = hashlib.sha256()
            with session.get(url, stream=True, timeout=(30, read_timeout_s)) as resp:
                resp.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        f.write(chunk)
                        sha256.update(chunk)
            _check_payload(part, fmt)
            os.replace(part, path)
            return sha256.hexdigest()
        except (requests.RequestException, ValueError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            transient = isinstance(e, requests.ConnectionError | requests.Timeout) or (
                status is not None and status >= 500
            )
            if not transient or attempt == attempts:
                raise
            logger.warning(f"{path.name}: {e}; retrying ({attempt}/{attempts})")
            time.sleep(10 * attempt)
    raise AssertionError("unreachable")


def download_set(
    slug: str,
    urls: dict[str, str],
    fmt: str,
    version: str | None,
    force: bool = False,
    session: requests.Session | None = None,
    read_timeout_s: float = 900,
) -> dict:
    """Download several files of one dataset into data/raw/{slug}/{name}.{fmt}.

    Files already present are kept unless ``force``; the set is complete when every
    name has its file. The meta's sha256 covers the sorted per-file checksums, so a
    changed tile changes the dataset's checksum.
    """
    dest_dir = data_dir("raw") / slug
    session = session or make_session()
    meta = _read_meta(dest_dir)
    same = meta.get("version") == version and set(meta.get("files") or {}) == set(urls)
    if not force and meta.get("completed") and same:
        logger.info(f"[{slug}] Already downloaded — skipping (use --force to re-download)")
        return meta
    dest_dir.mkdir(parents=True, exist_ok=True)
    known = meta.get("files") or {} if same else {}
    files: dict[str, str] = {}
    for i, (name, url) in enumerate(urls.items(), 1):
        path = dest_dir / f"{name}.{fmt}"
        if path.exists() and not force and name in known:
            files[name] = known[name]
            continue
        logger.info(f"[{slug}] {i}/{len(urls)} {name}")
        files[name] = download_file(session, url, path, fmt, read_timeout_s=read_timeout_s)
        _write_meta(
            dest_dir, {"slug": slug, "version": version, "files": files, "completed": False}
        )
    combined = hashlib.sha256("\n".join(f"{k} {v}" for k, v in sorted(files.items())).encode())
    new_meta = {
        "slug": slug,
        "url": next(iter(urls.values())),
        "version": version,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "bytes": sum((dest_dir / f"{n}.{fmt}").stat().st_size for n in files),
        "sha256": combined.hexdigest(),
        "completed": True,
    }
    _write_meta(dest_dir, new_meta)
    logger.info(f"[{slug}] {len(files)} files, {new_meta['bytes'] / 1e6:.0f} MB")
    return new_meta
