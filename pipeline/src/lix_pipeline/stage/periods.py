"""The period each source's data describes, as opposed to when it was downloaded.

A period is ``{"label", "start", "end", "kind"}``: the kind says how to print the dates
(``year``, ``month``, ``day``, ``range``) and ``text`` carries a label that is not a date
("Round 4 (2021 traffic)"). Where a source's data carries its own dates (police months,
Price Paid's as-of date, a GTFS reference day) the staged table is read; where the file
name or URL names a date it is parsed; the registry's ``period:`` covers sources that
publish none; and the lockfile's publish or download date is the last resort.
"""

import calendar
import re
from datetime import date
from pathlib import Path

from lix_core.config import DatasetSpec
from lix_core.paths import data_dir
from lix_pipeline.fetch.dates import find_date

Period = dict


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def year(y: int, label: str | None = None) -> Period:
    return {
        "label": label or str(y),
        "start": f"{y}-01-01",
        "end": f"{y}-12-31",
        "kind": "year",
    }


def month(d: date, label: str | None = None) -> Period:
    last = calendar.monthrange(d.year, d.month)[1]
    return {
        "label": label or d.strftime("%B %Y"),
        "start": d.replace(day=1).isoformat(),
        "end": d.replace(day=last).isoformat(),
        "kind": "month",
    }


def day(d: date, label: str | None = None) -> Period:
    return {"label": label or _day_label(d), "start": _iso(d), "end": _iso(d), "kind": "day"}


def month_range(start: date, end: date, label: str | None = None) -> Period:
    last = calendar.monthrange(end.year, end.month)[1]
    return {
        "label": label or f"{start.strftime('%B %Y')} to {end.strftime('%B %Y')}",
        "start": start.replace(day=1).isoformat(),
        "end": end.replace(day=last).isoformat(),
        "kind": "range",
    }


def text(label: str) -> Period:
    """A hand-written label; a four-digit year inside it gives the dates."""
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", label)
    if m:
        y = int(m.group(1))
        return {**year(y), "label": label, "kind": "text"}
    return {"label": label, "start": None, "end": None, "kind": "text"}


def _day_label(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def _staged(slug: str) -> Path:
    return data_dir("staged") / f"{slug}.parquet"


def _column_max(path: Path, column: str):
    import polars as pl

    if not path.exists():
        return None
    schema = pl.read_parquet_schema(path)
    if column not in schema:
        return None
    return pl.scan_parquet(path).select(pl.col(column).max()).collect().item()


def _column_min(path: Path, column: str):
    import polars as pl

    if not path.exists():
        return None
    if column not in pl.read_parquet_schema(path):
        return None
    return pl.scan_parquet(path).select(pl.col(column).min()).collect().item()


def _to_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text_value = str(value)
    if re.fullmatch(r"\d{4}-\d{2}", text_value):
        return date(int(text_value[:4]), int(text_value[5:7]), 1)
    return find_date(text_value)


# --- extractors for sources whose staged tables carry their own dates ------------------


def _police(slug: str) -> Period | None:
    first = _to_date(_column_min(_staged(slug), "first_month"))
    last = _to_date(_column_max(_staged(slug), "last_month"))
    return month_range(first, last) if first and last else None


def _price_paid(slug: str) -> Period | None:
    as_of = _to_date(_column_max(_staged(slug), "as_of"))
    return month(as_of, f"12 months to {as_of.strftime('%B %Y')}") if as_of else None


def _gtfs(slug: str) -> Period | None:
    ref = _to_date(_column_max(_staged("bods_gtfs"), "reference_day"))
    return day(ref, f"timetable for {_day_label(ref)}") if ref else None


def _ofsted(slug: str) -> Period | None:
    import polars as pl

    path = _staged(slug)
    if not path.exists():
        return None
    columns = [c for c in pl.read_parquet_schema(path) if c.endswith("_date")]
    latest = None
    for c in columns:
        d = _to_date(_column_max(path, c))
        if d and (latest is None or d > latest):
            latest = d
    return month(latest, f"inspections to {latest.strftime('%B %Y')}") if latest else None


def _osm(slug: str) -> Period | None:
    pbfs = sorted((data_dir("raw") / slug).glob("*.pbf"))
    if not pbfs:
        return None
    try:
        import osmium

        stamp = osmium.io.Reader(str(pbfs[0])).header().get("osmosis_replication_timestamp")
    except Exception:
        return None
    d = find_date(stamp or "")
    return day(d) if d else None


def _pcm(slug: str, lock_entry: dict) -> Period | None:
    url = (lock_entry.get("resolved") or {}).get("url") or ""
    m = re.search(r"map(?:no2|pm25|pm10)(20\d{2})", url)
    return year(int(m.group(1))) if m else None


STAGED_EXTRACTORS = {
    "police_crime": _police,
    "price_paid": _price_paid,
    "ofsted_schools": _ofsted,
}


def period_of(slug: str, spec: DatasetSpec, lock_entry: dict) -> Period | None:
    """The period a source's data describes, or None when nothing says."""
    if slug in STAGED_EXTRACTORS:
        found = STAGED_EXTRACTORS[slug](slug)
        if found:
            return found
    if slug.startswith("bods_gtfs"):
        found = _gtfs(slug)
        if found:
            return found
    if slug.startswith("osm_"):
        found = _osm(slug)
        if found:
            return found
    if slug.startswith("defra_pcm_"):
        found = _pcm(slug, lock_entry)
        if found:
            return found
    if spec.period:
        return text(spec.period)

    resolved = lock_entry.get("resolved") or {}
    fetched = lock_entry.get("fetched") or {}
    # A date in the version (unless it is an HTTP date or an ETag, which say when the
    # file was published, not what it is for), the file names or the URL
    version = resolved.get("version") if isinstance(resolved.get("version"), str) else ""
    if "GMT" in version or version.startswith('"') or re.match(r"\d{4}-\d{2}-\d{2}T", version):
        version = ""
    for candidate in [version, *(fetched.get("extracted") or []), resolved.get("url") or ""]:
        d = find_date(candidate)
        if d:
            # A day-precise date in a file name is a snapshot; year-month is an edition
            has_day = bool(
                re.search(r"(?<!\d)\d{1,2}[\s_\-.]*[A-Za-z]+[\s_\-.]*20\d{2}", candidate)
            ) or bool(
                re.search(
                    r"(?<!\d)20\d{2}[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)",
                    candidate,
                )
            )
            return day(d) if has_day else month(d)
    published = resolved.get("modified") or fetched.get("last_modified")
    d = find_date(str(published)) if published else None
    if d:
        return month(d, f"published {d.strftime('%B %Y')}")
    if fetched.get("fetched_at"):
        d = find_date(fetched["fetched_at"][:10])
        if d:
            return day(d, f"downloaded {_day_label(d)}")
    return None


def data_to(periods: dict[str, Period | None], today: date | None = None) -> str | None:
    """The latest ``end`` across the dated sources: how recent the data gets. Hand-written
    labels (a budget year, a census geography) do not count, and a timetable's reference
    day in the future is capped at today."""
    ends = [p["end"] for p in periods.values() if p and p.get("end") and p.get("kind") != "text"]
    if not ends:
        return None
    return min(max(ends), (today or date.today()).isoformat())
