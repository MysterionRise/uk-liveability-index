"""Overture Maps: query a release in place with DuckDB (httpfs), save the rows as Parquet.

Releases live under s3://overturemaps-us-west-2/release/<YYYY-MM-DD.n>/ and are listed
through the bucket's public HTTPS endpoint; the newest is used unless one is pinned.
Row groups carry bbox statistics, so a bounding-box filter reads a small part of the
global files.
"""

import hashlib
import json
import re
from pathlib import Path

import requests

from lix_core.config import OvertureAccess

S3_HTTPS = "https://{bucket}.s3.amazonaws.com"
BUCKET_HTTPS = S3_HTTPS.format(bucket="overturemaps-us-west-2")
BUCKET_S3 = "s3://overturemaps-us-west-2"


def latest_release(session: requests.Session) -> str:
    resp = session.get(
        BUCKET_HTTPS, params={"list-type": "2", "prefix": "release/", "delimiter": "/"}, timeout=60
    )
    resp.raise_for_status()
    releases = re.findall(r"<Prefix>release/(\d{4}-\d{2}-\d{2}\.\d+)/</Prefix>", resp.text)
    if not releases:
        raise RuntimeError("No Overture releases listed")
    return max(releases, key=lambda r: (r.split(".")[0], int(r.split(".")[1])))


def resolve_query(access: OvertureAccess, session: requests.Session) -> dict:
    """The release's prefix and a version that names the release and the query: a
    widened bbox or a changed filter is a new edition, so the file is fetched again
    (the Scottish pubs were missing after Scotland joined until this was so)."""
    release = access.release or latest_release(session)
    url = f"{BUCKET_S3}/release/{release}/theme={access.theme}/type={access.kind}/*"
    query = json.dumps(
        {"bbox": list(access.bbox), "where": access.where, "select": list(access.select)},
        sort_keys=True,
    )
    digest = hashlib.sha256(query.encode()).hexdigest()[:8]
    return {"url": url, "version": f"{release}+{digest}"}


def probe(url: str, session: requests.Session) -> int:
    """An HTTP status for an ``s3://bucket/prefix/*`` glob, which no HTTP client can open:
    200 when the public bucket lists an object under the prefix, 404 when it lists none."""
    m = re.fullmatch(r"s3://([^/]+)/(.*?)\*?", url)
    if not m:
        raise ValueError(f"Not an s3:// URL: {url}")
    bucket, prefix = m.groups()
    resp = session.get(
        S3_HTTPS.format(bucket=bucket),
        params={"list-type": "2", "prefix": prefix, "max-keys": "1"},
        timeout=60,
    )
    if not resp.ok:
        return resp.status_code
    return 200 if "<Contents>" in resp.text else 404


def build_sql(access: OvertureAccess, url: str) -> str:
    xmin, ymin, xmax, ymax = access.bbox
    where = [
        f"bbox.xmin BETWEEN {xmin} AND {xmax}",
        f"bbox.ymin BETWEEN {ymin} AND {ymax}",
    ]
    if access.where:
        where.append(f"({access.where})")
    return (
        f"SELECT {', '.join(access.select)} "
        f"FROM read_parquet('{url}', hive_partitioning=1) WHERE {' AND '.join(where)}"
    )


def fetch_query(access: OvertureAccess, url: str, out_path: Path) -> int:
    """Run the query and write the rows to ``out_path`` (Parquet); returns the row count."""
    import duckdb

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".part.parquet")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    con.execute(f"COPY ({build_sql(access, url)}) TO '{tmp}' (FORMAT parquet)")
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{tmp}')").fetchone()[0]
    tmp.replace(out_path)
    return rows
