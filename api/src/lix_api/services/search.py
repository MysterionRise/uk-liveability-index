"""Find what a user means: a postcode, an LSOA/MSOA/LAD code, a place or an area name."""

import re

import polars as pl
from rapidfuzz import fuzz, process

from lix_api.models import Place, Point
from lix_api.services.text import fold
from lix_api.store import Store, to_bng

POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}$")
# Any ONS GSS code (nation letter + entity type + 6 digits); whether it names an LSOA
# or an area in this build is decided by looking it up
GSS = re.compile(r"^[EWSN]\d{8}$")

# Higher wins when names score equally: "Leeds" the city beats Leeds village in Kent
# WRatio similarity a name must reach: typos ("Headingly", "Mancheser") score ~95,
# while unrelated names sharing a suffix ("Nowheresville" vs "Teesville") score ~80.
# Query and names are both folded first (see services.text), so "leeds" and "Ynys Mon"
# score 100 against "Leeds" and "Ynys Môn"
MIN_SIMILARITY = 88
# A name shorter than this matches only the query that equals it: WRatio scores two-
# and three-letter names ("Ae", "Ely") against anything that contains their letters
MIN_FUZZY_LETTERS = 4

KIND_PRIORITY = {"nation": 7, "lad": 6, "region": 5, "City": 5, "Town": 4, "msoa": 3,
                 "Suburban Area": 3, "Village": 2, "Other Settlement": 1, "Hamlet": 0}  # fmt: skip


def normalise_postcode(text: str) -> str:
    return re.sub(r"\s", "", text).upper()


def _bbox(row: dict) -> tuple[float, float, float, float] | None:
    if row.get("bbox_w") is None:
        return None
    return (row["bbox_w"], row["bbox_s"], row["bbox_e"], row["bbox_n"])


def lookup_postcode(store: Store, text: str) -> Place | None:
    norm = normalise_postcode(text)
    if not POSTCODE.match(norm):
        return None
    hit = store.postcodes.filter(pl.col("postcode_norm") == norm)
    if hit.is_empty():
        return None
    row = hit.row(0, named=True)
    return Place(
        kind="postcode", name=row["postcode"], code=row["postcode"], lsoa21cd=row["lsoa21cd"],
        centre=Point(lat=row["lat"], lon=row["lon"]),
    )  # fmt: skip


def lookup_code(store: Store, text: str) -> Place | None:
    code = text.strip().upper()
    if not GSS.match(code):
        return None
    if code in store.lsoa_index:
        row = store.features.row(store.lsoa_index[code], named=True)
        return Place(
            kind="lsoa", name=row["lsoa21nm"], code=code, lsoa21cd=code,
            detail=row["msoa_name"], centre=Point(lat=row["pwc_lat"], lon=row["pwc_lon"]),
            bbox=_bbox(row),
        )  # fmt: skip
    hit = store.areas.filter(pl.col("code") == code)
    if hit.is_empty():
        return None
    row = hit.row(0, named=True)
    return _area_place(row)


def lookup_nation(store: Store, text: str) -> Place | None:
    """A nation by its name in any language the pack knows ("Scotland", "Alba", "Yr
    Alban"): an exact match only, never a fuzzy one, so "Stirling, Scotland" is never
    read as Scotland."""
    code = store.nation_aliases.get(fold(text))
    if code is None:
        return None
    hit = store.areas.filter((pl.col("level") == "nation") & (pl.col("nation") == code))
    if hit.is_empty():
        return None
    row = hit.row(0, named=True)
    shown = text.strip() if fold(text) != fold(row["name"]) else row["name"]
    return _area_place(row, shown)


def _area_place(row: dict, name: str | None = None) -> Place:
    bbox = _bbox(row)
    centre = Point(lat=(bbox[1] + bbox[3]) / 2, lon=(bbox[0] + bbox[2]) / 2) if bbox else None
    detail = row["lad_nm"] if row["level"] == "msoa" else None
    shown = name or row["name"]
    return Place(kind=row["level"], name=shown, code=row["code"], detail=detail,
                 centre=centre, bbox=bbox,
                 name_en=row["name"] if shown != row["name"] else None)  # fmt: skip


def _candidates(store: Store, query: str, limit: int = 40) -> list[Place]:
    """Fuzzy matches for free text over places and areas (nations excepted), best first."""
    candidates: list[tuple[float, Place]] = []
    folded = fold(query)

    def too_short(matched: str) -> bool:
        return len(matched) < MIN_FUZZY_LETTERS and fold(matched) != folded

    n_places = store.places.height
    for _, score, idx in process.extract(
        folded, store.place_search_names, scorer=fuzz.WRatio, processor=None, limit=limit,
        score_cutoff=MIN_SIMILARITY,
    ):  # fmt: skip
        row = store.places.row(idx % n_places, named=True)
        # The form that matched is the one shown ("Caerdydd" for a Welsh query), with the
        # English beside it
        matched = row[store.place_name_columns[idx // n_places]]
        if too_short(matched):
            continue
        place = Place(
            kind="place", name=matched, code=row["place_id"], lsoa21cd=row["lsoa21cd"],
            detail=f"{row['place_type']}, {row['local_authority']}",
            centre=Point(lat=row["lat"], lon=row["lon"]),
            name_en=row["name"] if matched != row["name"] else None,
        )  # fmt: skip
        candidates.append((score + KIND_PRIORITY.get(row["place_type"], 0), place))

    n_areas = store.areas.height
    for _, score, idx in process.extract(
        folded, store.area_search_names, scorer=fuzz.WRatio, processor=None, limit=20,
        score_cutoff=MIN_SIMILARITY,
    ):  # fmt: skip
        row = store.areas.row(idx % n_areas, named=True)
        if row["level"] == "nation":
            continue
        matched = row[store.area_name_columns[idx // n_areas]]
        if too_short(matched):
            continue
        candidates.append((score + KIND_PRIORITY.get(row["level"], 0), _area_place(row, matched)))

    candidates.sort(key=lambda c: c[0], reverse=True)
    seen, out = set(), []
    for _, place in candidates:
        key = (place.kind, place.name, place.detail)
        if key not in seen:
            seen.add(key)
            out.append(place)
    return out


def nation_of(place: Place) -> str | None:
    """The nation letter of a result (from the LSOA it sits in, or its GSS code)."""
    code = place.lsoa21cd or (place.code if place.kind != "place" else None)
    return code[0] if code else None


def _council_of(place: Place) -> str | None:
    """The folded council name a result sits in, where the result says."""
    if place.kind == "lad":
        return fold(place.name_en or place.name)
    if place.kind == "msoa" and place.detail:
        return fold(place.detail)
    if place.kind == "place" and place.detail and ", " in place.detail:
        return fold(place.detail.rsplit(", ", 1)[1])
    return None


def _within_scopes(
    store: Store, candidates: list[Place], scopes: list[str]
) -> tuple[list[Place], bool]:
    """Keep the candidates inside every scope named after the comma ("Morningside,
    Edinburgh", "Stirling, Scotland"): a nation by name in any language, a council by
    name. A scope that names nothing known is ignored; the flag says whether any scope
    was recognised, so "Richmond, Scotland" is empty rather than Richmond in Yorkshire."""
    keep = candidates
    recognised = False
    for scope in scopes:
        nation = store.nation_aliases.get(fold(scope))
        if nation:
            recognised = True
            keep = [p for p in keep if nation_of(p) == nation]
            continue
        councils = [p for p in _candidates(store, scope, limit=10) if p.kind == "lad"]
        if not councils:
            continue
        recognised = True
        council = councils[0]
        names = {fold(council.name), fold(council.name_en or council.name)}
        keep = [
            p
            for p in keep
            if (p.kind == "lad" and p.code == council.code) or _council_of(p) in names
        ]
    return keep, recognised


def search_place(store: Store, query: str, limit: int = 8) -> list[Place]:
    """Rank candidate places for free text, best first.

    A postcode, an area code or a nation's name is exact. "Place, Scope" (one or more
    scopes after commas) searches the place and keeps the results inside the scope: a
    nation or a council, in any language the pack knows.
    """
    query = query.strip()
    if not query:
        return []
    for exact in (
        lookup_postcode(store, query),
        lookup_code(store, query),
        lookup_nation(store, query),
    ):
        if exact:
            return [exact]
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if len(parts) >= 2:
        scoped, recognised = _within_scopes(store, _candidates(store, parts[0]), parts[1:])
        if scoped or recognised:
            return scoped[:limit]
    return _candidates(store, query)[:limit]


def resolve_point(store: Store, ref: str) -> tuple[Point, str]:
    """A location for "near X": postcode, code or place name → (point, label)."""
    places = search_place(store, ref, limit=1)
    if not places or places[0].centre is None:
        raise LookupError(f"Couldn't find a place called {ref!r}")
    place = places[0]
    return place.centre, place.name if not place.detail else f"{place.name} ({place.detail})"


def resolve_lsoa(store: Store, ref: str) -> str:
    """An LSOA for a postcode, LSOA code or place name (the LSOA it sits in).

    A local authority or region resolves through its namesake place ("Manchester" the
    city, whose point is the city centre) rather than the middle of its bounding box,
    which is often a suburb.
    """
    places = search_place(store, ref, limit=6)
    if not places:
        raise LookupError(f"Couldn't find an area for {ref!r}")
    top = places[0]
    if top.kind in ("nation", "region"):
        # The middle of a nation is not a neighbourhood: a profile needs a place
        raise LookupError(
            f"{top.name} is a {top.kind}, not an area: name a place, a postcode or a "
            "council, or rank within it"
        )
    if top.lsoa21cd:
        return top.lsoa21cd
    namesake = next(
        (p for p in places[1:] if p.lsoa21cd and p.name.lower() == top.name.lower()), None
    )
    if namesake:
        return namesake.lsoa21cd
    if top.centre:
        return nearest_lsoa(store, top.centre)
    raise LookupError(f"Couldn't find an area for {ref!r}")


def nearest_lsoa(store: Store, point: Point) -> str:
    """The LSOA whose population-weighted centroid is nearest the point (on the grid)."""
    _, idx = store.lsoa_tree.query(to_bng(point.lon, point.lat))
    return store.features["lsoa21cd"][int(idx)]
