"""TransXChange timetables → weekday daytime departures per stop (Translink's files).

TransXChange describes a service as journey patterns (ordered timing links between stops
with run and wait times) and vehicle journeys (a pattern, a departure time and the days
it runs). Northern Ireland publishes no GTFS, so this reader produces the same table as
the GTFS one: every stop with its departures on a reference Tuesday between 07:00 and
19:00 where passengers can board. Read with the standard library's iterparse: the Metro
file is 136 MB.
"""

import re
import zipfile
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import polars as pl

from lix_core.log import setup_logging

logger = setup_logging("stage.txc")

NS = "{http://www.transxchange.org.uk/}"
DAYTIME_S = (7 * 3600, 19 * 3600)
# DaysOfWeek elements that include a Tuesday
TUESDAY_DAYS = {
    "Tuesday",
    "MondayToFriday",
    "MondayToSaturday",
    "MondayToSunday",
    "NotSaturday",
    "NotSunday",
    "Weekdays",
}
PICKUPS = {None, "", "pickUp", "pickUpAndSetDown"}
_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")


def seconds(duration: str | None) -> float:
    """``PT1M30S`` → 90."""
    if not duration:
        return 0.0
    m = _DURATION.fullmatch(duration.strip())
    if not m:
        raise ValueError(f"Cannot read duration {duration!r}")
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _text(el, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


def _runs_on_tuesday(profile) -> bool | None:
    """Whether an OperatingProfile element covers Tuesdays; None when it has no regular
    days (the caller falls back to the service's profile)."""
    if profile is None:
        return None
    days = profile.find(f"{NS}RegularDayType/{NS}DaysOfWeek")
    if days is None:
        if profile.find(f"{NS}RegularDayType/{NS}HolidaysOnly") is not None:
            return False
        return None
    names = {child.tag.removeprefix(NS) for child in days}
    return bool(names & TUESDAY_DAYS)


class _Timetable:
    """Everything one file contributes, collected by one pass of iterparse."""

    def __init__(self):
        self.stops: dict[str, tuple[str | None, float | None, float | None]] = {}
        self.sections: dict[str, list[tuple[str, str, float, float, bool, bool]]] = {}
        self.patterns: dict[str, list[str]] = {}
        self.service_days: dict[str, bool | None] = {}
        self.periods: list[tuple[date, date]] = []
        self.journeys: list[tuple[str, str, float, bool | None]] = []

    def stop_point(self, el) -> None:
        code = _text(el, f"{NS}AtcoCode") or _text(el, f"{NS}StopPointRef")
        if not code:
            return
        name = _text(el, f"{NS}Descriptor/{NS}CommonName") or _text(el, f"{NS}CommonName")
        loc = el.find(f".//{NS}Location")
        lon = lat = None
        if loc is not None:
            lon_t, lat_t = _text(loc, f"{NS}Longitude"), _text(loc, f"{NS}Latitude")
            if lon_t and lat_t:
                lon, lat = float(lon_t), float(lat_t)
        self.stops[code] = (name, lon, lat)

    def section(self, el) -> None:
        links = []
        for link in el.findall(f"{NS}JourneyPatternTimingLink"):
            frm, to = link.find(f"{NS}From"), link.find(f"{NS}To")
            if frm is None or to is None:
                continue
            links.append(
                (
                    _text(frm, f"{NS}StopPointRef") or "",
                    _text(to, f"{NS}StopPointRef") or "",
                    seconds(_text(link, f"{NS}RunTime")),
                    seconds(_text(to, f"{NS}WaitTime")),
                    _text(frm, f"{NS}Activity") in PICKUPS,
                    _text(to, f"{NS}Activity") in PICKUPS,
                )
            )
        self.sections[el.get("id", "")] = links

    def service(self, el) -> None:
        code = _text(el, f"{NS}ServiceCode") or ""
        period = el.find(f"{NS}OperatingPeriod")
        if period is not None:
            start, end = _text(period, f"{NS}StartDate"), _text(period, f"{NS}EndDate")
            if start:
                s = date.fromisoformat(start)
                e = date.fromisoformat(end) if end else s + timedelta(days=90)
                self.periods.append((s, e))
        self.service_days[code] = _runs_on_tuesday(el.find(f"{NS}OperatingProfile"))
        for jp in el.findall(f".//{NS}JourneyPattern"):
            refs = [r.text.strip() for r in jp.findall(f"{NS}JourneyPatternSectionRefs") if r.text]
            self.patterns[jp.get("id", "")] = refs

    def journey(self, el) -> None:
        pattern = _text(el, f"{NS}JourneyPatternRef")
        departure = _text(el, f"{NS}DepartureTime")
        if not pattern or not departure:
            return  # journeys defined by reference to another journey are rare; skipped
        h, m, s = (int(x) for x in departure.split(":")[:3])
        self.journeys.append(
            (
                _text(el, f"{NS}ServiceRef") or "",
                pattern,
                h * 3600 + m * 60 + s,
                _runs_on_tuesday(el.find(f"{NS}OperatingProfile")),
            )
        )

    def departures(self) -> dict[str, int]:
        """Boardable departures per stop between 07:00 and 19:00 on a running Tuesday."""
        counts: dict[str, int] = {}
        lo, hi = DAYTIME_S

        def count(stop: str, t: float) -> None:
            if lo <= t < hi:
                counts[stop] = counts.get(stop, 0) + 1

        for service, pattern, start, runs in self.journeys:
            runs = runs if runs is not None else self.service_days.get(service)
            if runs is False or runs is None:
                continue
            t = float(start)
            first = True
            for section in self.patterns.get(pattern, []):
                for frm, to, run, wait, from_pickup, to_pickup in self.sections.get(section, []):
                    if first:
                        if from_pickup:
                            count(frm, t)
                        first = False
                    t += run
                    if to_pickup:
                        count(to, t + wait)
                    t += wait
        return counts


def read_txc(source) -> _Timetable:
    """Parse one TransXChange document (a path or an open binary file)."""
    tt = _Timetable()
    for _event, el in ET.iterparse(source, events=("end",)):
        tag = el.tag
        if tag in (f"{NS}StopPoint", f"{NS}AnnotatedStopPointRef"):
            tt.stop_point(el)
            el.clear()
        elif tag == f"{NS}JourneyPatternSection":
            tt.section(el)
            el.clear()
        elif tag == f"{NS}Service":
            tt.service(el)
            el.clear()
        elif tag == f"{NS}VehicleJourney":
            tt.journey(el)
            el.clear()
        elif tag in (f"{NS}StopPoints", f"{NS}JourneyPatternSections", f"{NS}VehicleJourneys"):
            el.clear()
    return tt


def txc_documents(folder: Path) -> Iterable[tuple[str, object]]:
    """Every TransXChange document under ``folder``: XML files, and XML members of zips
    (Translink nests one zip per operator inside the download)."""
    for path in sorted(folder.glob("**/*.xml")):
        yield path.name, path
    for path in sorted(folder.glob("**/*.zip")):
        with zipfile.ZipFile(path) as outer:
            for name in outer.namelist():
                if name.lower().endswith(".xml"):
                    with outer.open(name) as member:
                        yield name, member
                elif name.lower().endswith(".zip"):
                    import io

                    with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                        for inner_name in inner.namelist():
                            if inner_name.lower().endswith(".xml"):
                                with inner.open(inner_name) as member:
                                    yield inner_name, member


def txc_departures(folder: Path) -> pl.DataFrame:
    """Departures per stop from every TransXChange document under ``folder``, in the shape
    of the GTFS reader: stop_id, name, lon, lat, departures, per_hour, reference_day."""
    from lix_pipeline.stage.transport import reference_tuesday

    stops: dict[str, tuple[str | None, float | None, float | None]] = {}
    counts: dict[str, int] = {}
    periods: list[tuple[date, date]] = []
    for name, source in txc_documents(folder):
        tt = read_txc(source)
        stops.update({k: v for k, v in tt.stops.items() if v[1] is not None})
        for stop, n in tt.departures().items():
            counts[stop] = counts.get(stop, 0) + n
        periods.extend(tt.periods)
        logger.info(f"{name}: {len(tt.journeys):,} journeys, {len(tt.stops):,} stops")
    if not periods:
        raise ValueError(f"No service periods in the TransXChange files under {folder}")
    start = min(p[0] for p in periods)
    end = max(p[1] for p in periods)
    day = reference_tuesday(start, end)
    rows = [
        {
            "stop_id": code,
            "name": stops[code][0],
            "lon": stops[code][1],
            "lat": stops[code][2],
            "departures": n,
        }
        for code, n in counts.items()
        if code in stops
    ]
    df = pl.DataFrame(
        rows,
        schema={
            "stop_id": pl.Utf8,
            "name": pl.Utf8,
            "lon": pl.Float64,
            "lat": pl.Float64,
            "departures": pl.Int64,
        },
    )
    return df.with_columns(
        (pl.col("departures") / 12).alias("per_hour"), pl.lit(day).alias("reference_day")
    )


def txc_stops(folder: Path) -> pl.DataFrame:
    """Every located stop of the documents under ``folder``: stop_id, name, lon, lat."""
    stops: dict[str, tuple[str | None, float | None, float | None]] = {}
    for _name, source in txc_documents(folder):
        stops.update({k: v for k, v in read_txc(source).stops.items() if v[1] is not None})
    return pl.DataFrame(
        {
            "stop_id": list(stops),
            "name": [v[0] for v in stops.values()],
            "lon": [v[1] for v in stops.values()],
            "lat": [v[2] for v in stops.values()],
        },
        schema={"stop_id": pl.Utf8, "name": pl.Utf8, "lon": pl.Float64, "lat": pl.Float64},
    )


def _unused(_: datetime) -> None:  # pragma: no cover - keeps datetime for type hints
    return None
