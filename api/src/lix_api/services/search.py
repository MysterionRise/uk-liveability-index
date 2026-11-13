"""Find what a user means: a postcode, an LSOA/MSOA/LAD code, a place or an area name."""

import re
from dataclasses import dataclass
from typing import Literal

import polars as pl
from rapidfuzz import fuzz, process

from lix_api.agent.messages import message
from lix_api.models import Place, Point
from lix_api.services.names import base_locale, council_name
from lix_api.services.text import fold
from lix_api.store import Store, to_bng
from lix_core.text import repair_mojibake

POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}$")
# A district ("LS6", "EC1A") is the outward code; a sector ("LS6 3") adds the inward
# code's digit. Both are read from the postcode table, so only districts that exist match
POSTCODE_DISTRICT = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$")
POSTCODE_SECTOR = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\d$")
# Any ONS GSS code (nation letter + entity type + 6 digits), or one minted for Ireland in
# the same shape; whether it names an LSOA or an area in this build is decided by looking
# it up
GSS = re.compile(r"^[EWSNI]\d{8}$")
# The longest text a reference may be (a postcode, a code, a place and a scope or two):
# anything longer is a mistake or an attack, not a place
MAX_QUERY = 200
# How many "Place, Scope, Scope" parts are read: each costs a scan of every name
MAX_SCOPES = 3

# Higher wins when names score equally: "Leeds" the city beats Leeds village in Kent
# WRatio similarity a name must reach: typos ("Headingly", "Mancheser") score ~95,
# while unrelated names sharing a suffix ("Nowheresville" vs "Teesville") score ~80.
# Query and names are both folded first (see services.text), so "leeds" and "Ynys Mon"
# score 100 against "Leeds" and "Ynys Môn"
MIN_SIMILARITY = 88
# A name shorter than this matches only the query that equals it: WRatio scores two-
# and three-letter names ("Ae", "Ely") against anything that contains their letters; and
# a query shorter than it matches only the name that equals it ("UK" is no neighbourhood)
MIN_FUZZY_LETTERS = 4
# A leading article is no part of a short name: "Y Rhyl" is Rhyl, "An t-Òban" is Oban
ARTICLE = re.compile(r"^(?:yr|y|an t|an|am|na h|na)[\s-]+")
# The words of "Rye East Sussex" tried as "Rye, East Sussex" when the whole finds nothing
MAX_SPLIT_WORDS = 3

KIND_PRIORITY = {"nation": 7, "lad": 6, "region": 5, "City": 5, "Town": 4, "msoa": 3,
                 "Suburban Area": 3, "Village": 2, "Other Settlement": 1, "Hamlet": 0}  # fmt: skip


def normalise_postcode(text: str) -> str:
    return re.sub(r"\s", "", text).upper()


def _bbox(row: dict) -> tuple[float, float, float, float] | None:
    if row.get("bbox_w") is None:
        return None
    return (row["bbox_w"], row["bbox_s"], row["bbox_e"], row["bbox_n"])


def postcode_groups(postcodes: pl.DataFrame) -> dict[str, dict[str, dict]]:
    """``{"district": {"LS6": {...}}, "sector": {"LS63": {...}}}`` from the postcode table:
    each group's postcode count, mean point, bounding box and the area of the postcode
    nearest its mean point (so a district resolves to a real neighbourhood inside it)."""
    length = pl.col("postcode_norm").str.len_chars()
    pc = postcodes.with_columns(
        pl.col("postcode_norm").str.slice(0, length - 3).alias("district"),
        pl.col("postcode_norm").str.slice(0, length - 2).alias("sector"),
    ).with_columns(
        # Central London's districts are lettered (SW1A … SW1Y, EC1A …, W1A …); people
        # call the whole "SW1" (#210). Only lettered districts belong to a prefix group
        pl.when(pl.col("district").str.contains(r"^[A-Z]{1,2}\d[A-Z]$"))
        .then(pl.col("district").str.slice(0, pl.col("district").str.len_chars() - 1))
        .alias("prefix"),
    )
    out: dict[str, dict[str, dict]] = {}
    for kind in ("district", "sector", "prefix"):
        # The postcode nearest the group's mean point names the area the group stands in
        off = (pl.col("lat") - pl.col("lat").mean().over(kind)) ** 2 + (
            pl.col("lon") - pl.col("lon").mean().over(kind)
        ) ** 2
        groups = (
            pc.filter(pl.col(kind).is_not_null())
            .with_columns(off.alias("_off"))
            .group_by(kind)
            .agg(
                pl.len().alias("count"),
                pl.col("lat").mean().alias("lat"),
                pl.col("lon").mean().alias("lon"),
                pl.col("lon").min().alias("bbox_w"),
                pl.col("lat").min().alias("bbox_s"),
                pl.col("lon").max().alias("bbox_e"),
                pl.col("lat").max().alias("bbox_n"),
                pl.col("lsoa21cd").sort_by("_off").first().alias("lsoa21cd"),
            )
        )
        out[kind] = {row[kind]: row for row in groups.iter_rows(named=True)}
    return out


def _group_place(kind: str, code: str, shown: str, row: dict, locale: str | None) -> Place:
    return Place(
        kind=f"postcode_{kind}", name=shown, code=code, lsoa21cd=row["lsoa21cd"],
        detail=message("postcodes_count", base_locale(locale), n=f"{row['count']:,}"),
        centre=Point(lat=row["lat"], lon=row["lon"]),
        bbox=(row["bbox_w"], row["bbox_s"], row["bbox_e"], row["bbox_n"]),
    )  # fmt: skip


def lookup_postcode_group(store: Store, text: str, locale: str | None = None) -> Place | None:
    """A postcode district ("LS6") or sector ("LS6 3", "ls63") as the pack's postcodes
    know it; a sector is tried first when the text is written with its space."""
    norm = normalise_postcode(text)
    spaced = " " in text.strip()
    tries = ("sector", "district") if spaced else ("district", "sector")
    for kind in tries:
        pattern = POSTCODE_SECTOR if kind == "sector" else POSTCODE_DISTRICT
        if not pattern.match(norm):
            continue
        row = store.postcode_groups[kind].get(norm)
        if row is None and kind == "district":
            # "SW1" names no district of its own: the lettered districts it stands for
            row = store.postcode_groups.get("prefix", {}).get(norm)
        if row is None:
            continue
        shown = f"{norm[:-1]} {norm[-1]}" if kind == "sector" else norm
        return _group_place(kind, norm, shown, row, locale)
    return None


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
    return _area_place(row, store=store)


def lookup_nation(store: Store, text: str, locale: str | None = None) -> Place | None:
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
    return _area_place(row, shown, store, locale)


def repeated_area_names(
    areas: pl.DataFrame, features: pl.DataFrame | None = None
) -> set[tuple[str, str, str]]:
    """(level, folded name, council) of every area whose name another area of the same
    level and council also carries: Ireland has 39 division names repeated within one
    council ("Kilmeadan" twice in Waterford, #194) and 55 small-area names ("Togher 001"
    twice in Wicklow, #210), so the council alone cannot tell them apart."""
    out: set[tuple[str, str, str]] = set()
    if "lad_nm" in areas.columns:
        dup = (
            areas.filter(pl.col("level").is_in(["lsoa", "msoa"]))
            .group_by("level", "name", "lad_nm")
            .len()
            .filter(pl.col("len") > 1)
        )
        out |= {
            (level, fold(name), lad)
            for level, name, lad in dup.select("level", "name", "lad_nm").iter_rows()
        }
    if features is not None and {"lsoa21nm", "lad_nm"} <= set(features.columns):
        small = features.group_by("lsoa21nm", "lad_nm").len().filter(pl.col("len") > 1)
        out |= {("lsoa", fold(name), lad) for name, lad, _ in small.iter_rows()}
    return out


def small_area_label(
    store, shown: str, english: str | None, lad: str | None, code: str, neighbourhood: str | None
) -> str:
    """A small area's label: "Headingley 003A (Headingley)", and with the code inside the
    one bracket when the council repeats the name: "Togher 001 (Togher, I01013732)"."""
    repeated = store is not None and ("lsoa", fold(english or shown), lad) in store.repeated_areas
    parts = [p for p in (neighbourhood, code if repeated else None) if p]
    return f"{shown} ({', '.join(parts)})" if parts else shown


def distinct_name(
    store, level: str, shown: str | None, lad: str | None, code: str, english: str | None = None
) -> str | None:
    """``shown``, with the area's code beside it when another area of the same level and
    council shares the name (``english``, the pack's name, decides): the label of a
    profile, a comparison column or a ranked row (#210)."""
    if shown is None or store is None:
        return shown
    if (level, fold(english or shown), lad) in store.repeated_areas:
        return f"{shown} ({code})"
    return shown


def _area_place(
    row: dict, name: str | None = None, store: Store | None = None, locale: str | None = None
) -> Place:
    bbox = _bbox(row)
    centre = Point(lat=(bbox[1] + bbox[3]) / 2, lon=(bbox[0] + bbox[2]) / 2) if bbox else None
    council = council_name(store, row.get("lad_nm"), locale) if store else row.get("lad_nm")
    detail = council if row["level"] == "msoa" else None
    if (
        store is not None
        and (row["level"], fold(row["name"]), row.get("lad_nm")) in store.repeated_areas
    ):
        # A name shared within the council: the code tells the areas apart
        detail = f"{council} ({row['code']})"
    shown = name or row["name"]
    return Place(kind=row["level"], name=shown, code=row["code"], detail=detail,
                 centre=centre, bbox=bbox,
                 name_en=row["name"] if shown != row["name"] else None)  # fmt: skip


def lookup_area_name(store: Store, text: str, locale: str | None = None) -> Place | None:
    """An area whose name, as the pack spells it, is the whole query ("Bristol, City of",
    "Kingston upon Hull, City of"): an exact folded match, so the comma inside the name
    is never read as a scope. The highest level wins when several share a name."""
    folded = fold(text)
    if "," not in text or not folded:
        return None
    n_areas = store.areas.height
    hits = [i % n_areas for i, name in enumerate(store.area_search_names) if name == folded]
    if not hits:
        return None
    rows = [store.areas.row(i, named=True) for i in dict.fromkeys(hits)]
    rows = [r for r in rows if r["level"] != "nation"]
    if not rows:
        return None
    row = max(rows, key=lambda r: KIND_PRIORITY.get(r["level"], 0))
    return _area_place(row, store=store, locale=locale)


def _candidates(
    store: Store, query: str, limit: int = 40, locale: str | None = None
) -> list[Place]:
    """Fuzzy matches for free text over places and areas (nations excepted), best first;
    one row per code, the best-scoring form of its name shown."""
    candidates: list[tuple[float, Place]] = []
    folded = fold(query)
    bare = ARTICLE.sub("", folded)

    def too_short(matched: str) -> bool:
        """A short name matches only the query that is it, or nearly: WRatio finds "Cath"
        and "Lairg" inside "Cathair Phort Láirge", so names under six letters must also
        score as a whole against the whole query (#193); and a short query matches only
        the name that is it, since "UK" is inside "Carluke" and "x" inside "Bexley"."""
        name = ARTICLE.sub("", fold(matched))
        if name == bare:
            return False
        if len(name) < MIN_FUZZY_LETTERS or len(bare) < MIN_FUZZY_LETTERS:
            return True
        return len(name) < 6 and fuzz.ratio(bare, name) < MIN_SIMILARITY

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
            # The settlement type is a key the page words; the council in its language
            place_type=row["place_type"],
            detail=council_name(store, row["local_authority"], locale),
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
        candidates.append(
            (score + KIND_PRIORITY.get(row["level"], 0), _area_place(row, matched, store, locale))
        )

    candidates.sort(key=lambda c: c[0], reverse=True)
    seen, out = set(), []
    for _, place in candidates:
        # One result per code: "Dùn Èideann" and "Dún Éideann" are the same Edinburgh
        key = (place.kind, place.code)
        if key not in seen:
            seen.add(key)
            out.append(place)
    return out


def nation_of(place: Place) -> str | None:
    """The nation letter of a result (from the LSOA it sits in, or its GSS code)."""
    code = place.lsoa21cd or (place.code if place.kind != "place" else None)
    return code[0] if code else None


def _council_of(store: Store, place: Place) -> str | None:
    """The folded council name a result sits in: as the result says, or for a postcode,
    a district, a sector or an LSOA, the council of the LSOA it stands in."""
    if place.kind == "lad":
        return fold(place.name_en or place.name)
    if place.kind == "msoa" and place.detail:
        return fold(place.detail.split(" (")[0])
    if place.kind == "place" and place.detail:
        return fold(place.detail)
    if place.lsoa21cd and place.kind in (
        "postcode",
        "postcode_district",
        "postcode_sector",
        "lsoa",
    ):
        return fold(store.features.row(store.lsoa_index[place.lsoa21cd], named=True)["lad_nm"])
    return None


# A compass point alone names no county or region ("East" is not East Sussex)
COMPASS = frozenset({"north", "south", "east", "west"})


def _named(names: dict[str, str], councils: dict[str, set[str]], words: str) -> dict:
    """The entries of ``names`` (folded name → name as spelt) a word names, each with its
    council codes: the one of that name, else every one whose name ends with the word."""
    folded = fold(words)
    if not folded or folded in COMPASS:
        return {}
    if folded in names:
        return {names[folded]: set(councils[folded])}
    return {names[k]: set(councils[k]) for k in sorted(names) if k.endswith(f" {folded}")}


def named_counties(store: Store, words: str) -> dict[str, set[str]]:
    """The counties a word names, each with its council codes: the county of that name
    ("Devon", "County Durham", "Dublin"), else every county whose name ends with the word
    ("Yorkshire": North, South and West Yorkshire and the East Riding of Yorkshire;
    "Sussex": East and West)."""
    return _named(store.county_names, store.county_councils, words)


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


# "the Midlands", "the West Country": the article is not part of the name
_ARTICLE = re.compile(r"^the\s+", re.IGNORECASE)


@dataclass(frozen=True)
class WideScope:
    """A county, or several counties or regions, as the place to rank within: the name to
    show, the features column that holds it, the values to keep, and the councils with a
    small area inside (all of them for most counties; a council the county divides is in
    the list and only its part is ranked)."""

    label: str
    column: Literal["county", "rgn_nm"]
    values: tuple[str, ...]
    councils: frozenset[str]


def county_scope(store: Store, words: str) -> WideScope | None:
    """What a ranking "within" covers when the words name a county, or an area wider than
    one, rather than a single place; the label says what was used ("Devon"; "East Sussex
    and West Sussex" for "Sussex").

    - A county's own name is the county: Devon's small areas across its ten councils, not
      the district search finds first (#233), and Dublin's four councils. Outside England
      a county that shares a council's name is left to the council ("Aberdeenshire" and
      "Gwynedd" mean the councils; a lieutenancy of the name is another shape).
    - A word that ends two or more regions' names is those regions ("Midlands"); else one
      that ends counties' names is those counties ("Yorkshire", "Sussex").
    - A name that is also a region's ("West Midlands") stays the region, and one that is
      exactly a place or an area ("Durham" the city, "Manchester") stays that place: only
      "County Durham" and "Greater Manchester" are the counties.
    """
    words = _ARTICLE.sub("", words.strip())
    folded = fold(words)
    if not folded or folded in store.region_councils or folded in store.scope_aliases:
        return None

    def scope(found: dict[str, set[str]], column: str) -> WideScope:
        return WideScope(
            _listed(list(found)), column, tuple(found), frozenset().union(*found.values())
        )

    counties = named_counties(store, words)
    if folded in store.county_names:
        english = "E" in store.county_nations.get(folded, ())
        if not english and folded in store.council_names_folded:
            return None
        return scope(counties, "county")
    regions = _named(store.region_names, store.region_councils, words)
    found, column = (regions, "rgn_nm") if len(regions) >= 2 else (counties, "county")
    if not found:
        return None
    top = search_place(store, words, limit=1)
    if top and folded in (fold(top[0].name), fold(top[0].name_en or "")):
        return None
    return scope(found, column)


def scope_councils(store: Store, scope: str) -> set[str]:
    """The council codes a scope word means: a region's councils ("London"), the councils
    of the county or counties it names ("Dublin": Dublin City, South Dublin, Fingal, Dún
    Laoghaire-Rathdown; "Devon"; "Yorkshire"), and every council whose name is or
    contains the word ("Dublin" also finds "South Dublin"); the best fuzzy match alone
    when nothing matches that way ("Edinburgh" for "Edinbrugh")."""
    folded = fold(scope)
    codes = set(store.region_councils.get(folded, ()))
    # Every council with a small area in the county: a place the county divides its
    # council from ("Yarm, North Yorkshire" in Stockton-on-Tees) is still found
    for county_codes in named_counties(store, scope).values():
        codes |= county_codes
    councils = [p for p in _candidates(store, scope, limit=10) if p.kind == "lad"]
    word = re.compile(rf"(^|\W){re.escape(folded)}($|\W)")
    named = [
        p
        for p in councils
        if word.search(fold(p.name)) or (p.name_en and word.search(fold(p.name_en)))
    ]
    codes |= {p.code for p in named}
    if not codes and councils:
        codes.add(councils[0].code)
    return {c for c in codes if c}


def _within_scopes(
    store: Store, candidates: list[Place], scopes: list[str]
) -> tuple[list[Place], bool]:
    """Keep the candidates inside every scope named after the comma ("Morningside,
    Edinburgh", "Stirling, Scotland", "Ranelagh, Dublin", "Clapham, London"): a nation
    or country by any of its names, a region, a county, or councils by name. A scope that
    names nothing known is ignored; the flag says whether any scope was recognised, so
    "Richmond, Scotland" is empty rather than Richmond in Yorkshire."""
    keep = candidates
    recognised = False
    for scope in scopes[:MAX_SCOPES]:
        nations = store.scope_aliases.get(fold(scope))
        if nations:
            recognised = True
            keep = [p for p in keep if nation_of(p) in nations]
            continue
        codes = scope_councils(store, scope)
        if not codes:
            continue
        recognised = True
        names = {fold(name) for code in codes for name in _council_names(store, code)}
        keep = [
            p
            for p in keep
            if (p.kind == "lad" and p.code in codes) or _council_of(store, p) in names
        ]
    return keep, recognised


def _council_names(store: Store, code: str) -> list[str]:
    """Every form of a council's name the pack knows (English and the other languages),
    so a place's "detail" matches whichever form the search returned."""
    rows = store.areas.filter((pl.col("level") == "lad") & (pl.col("code") == code))
    if rows.is_empty():
        return []
    row = rows.row(0, named=True)
    return [row[c] for c in store.area_name_columns if row.get(c)]


def search_place(
    store: Store, query: str, limit: int = 8, locale: str | None = None
) -> list[Place]:
    """Rank candidate places for free text, best first.

    A postcode, an area code or a nation's name is exact. "Place, Scope" (one or more
    scopes after commas) searches the place and keeps the results inside the scope: a
    nation or a council, in any language the pack knows; an exact reference before the
    comma ("LS6, Leeds") is kept when the scope agrees. "Rye East Sussex" without the
    comma is tried as "Rye, East Sussex" when the whole finds nothing.
    """
    # A query pasted from a page or a file that was decoded wrongly ("Ynys MÃ´n") is read
    # as it was written: folded as it came it is "ynys ma n", which finds the village
    # Ynys before Ynys Môn (#230). Every lookup by name, postcode or code starts here
    query = repair_mojibake(query).strip()
    if not query:
        return []
    if len(query) > MAX_QUERY:
        raise ValueError(f"A place reference is at most {MAX_QUERY} characters")
    for exact in (
        lookup_postcode(store, query),
        lookup_postcode_group(store, query, locale),
        lookup_code(store, query),
        lookup_nation(store, query, locale),
        lookup_area_name(store, query, locale),
    ):
        if exact:
            return [exact]
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if len(parts) >= 2:
        exact = next(
            (
                e
                for e in (
                    lookup_postcode(store, parts[0]),
                    lookup_postcode_group(store, parts[0], locale),
                    lookup_code(store, parts[0]),
                )
                if e
            ),
            None,
        )
        if exact:
            scoped, recognised = _within_scopes(store, [exact], parts[1:])
            if scoped or recognised:
                return scoped
        scoped, recognised = _within_scopes(
            store, _candidates(store, parts[0], locale=locale), parts[1:]
        )
        if scoped or recognised:
            return scoped[:limit]
    found = _candidates(store, query, locale=locale)
    words = query.split()
    if not found and len(parts) == 1 and len(words) > 1:
        for i in range(1, min(len(words), MAX_SPLIT_WORDS + 1)):
            scoped, _ = _within_scopes(
                store,
                _candidates(store, " ".join(words[:i]), locale=locale),
                [" ".join(words[i:])],
            )
            if scoped:
                return scoped[:limit]
    return found[:limit]


def _reject_wide(place: Place) -> None:
    """A nation or a region is no neighbourhood and no point: say so."""
    if place.kind in ("nation", "region"):
        raise LookupError(
            f"{place.name} is a {place.kind}, not an area: name a place, a postcode or a "
            "council, or rank within it"
        )


def resolve_point(store: Store, ref: str) -> tuple[Point, str]:
    """A location for "near X": postcode, code or place name → (point, label)."""
    places = search_place(store, ref, limit=1)
    if not places or places[0].centre is None:
        raise LookupError(f"Couldn't find a place called {ref!r}")
    place = places[0]
    _reject_wide(place)
    return place.centre, place.name if not place.detail else f"{place.name} ({place.detail})"


def resolve_lsoa(store: Store, ref: str) -> str:
    """An LSOA for a postcode, LSOA code or place name (the LSOA it sits in).

    A local authority or region resolves through its namesake place ("Manchester" the
    city, whose point is the city centre) rather than the middle of its bounding box,
    which is often a suburb.
    """
    return resolve_area(store, ref)[0]


def resolve_area(store: Store, ref: str) -> tuple[str, Place | None]:
    """The LSOA that stands for a reference (see ``resolve_lsoa``) and, when the reference
    named something larger than one small area (a neighbourhood, a council, a town or a
    suburb), the place it named: a profile of "Hebden Bridge" is of one small area in it,
    and should say so (#228)."""
    places = search_place(store, ref, limit=6)
    if not places:
        raise LookupError(f"Couldn't find an area for {ref!r}")
    top = places[0]
    _reject_wide(top)  # the middle of a nation is not a neighbourhood
    larger = top if top.kind in ("msoa", "lad", "place") else None
    if top.lsoa21cd:
        return top.lsoa21cd, larger
    column = {"msoa": "msoa21cd", "lad": "lad_cd"}.get(top.kind)
    inside = (
        store.features.filter(pl.col(column) == top.code)
        if column and top.code
        else store.features.clear()
    )
    # A namesake stands for a neighbourhood or a council only from inside it: there is a
    # village called Croydon in Cambridgeshire, and it was the London borough's profile
    within = set(inside["lsoa21cd"]) if inside.height else None
    namesake = next(
        (
            p
            for p in places[1:]
            if p.lsoa21cd
            and p.name.lower() == top.name.lower()
            and (within is None or p.lsoa21cd in within)
        ),
        None,
    )
    if namesake:
        return namesake.lsoa21cd, larger
    # A neighbourhood or council without a namesake place: its most populous area, which
    # is inside it (the middle of its bounding box may be a bay or the next council)
    if inside.height:
        return inside.sort("population", descending=True)["lsoa21cd"][0], larger
    if top.centre:
        return nearest_lsoa(store, top.centre), larger
    raise LookupError(f"Couldn't find an area for {ref!r}")


def named_area(store: Store, place: Place) -> tuple[str, str] | None:
    """The (level, code) a place means wherever areas are averaged or compared: a
    neighbourhood or a council is itself; a named town or suburb is the neighbourhood it
    sits in, not one small area of it. The rule compare_areas applies."""
    if place.kind in ("msoa", "lad") and place.code:
        return place.kind, place.code
    if place.kind == "place" and place.lsoa21cd in store.lsoa_index:
        return "msoa", store.features["msoa21cd"][store.lsoa_index[place.lsoa21cd]]
    return None


def nearest_lsoa(store: Store, point: Point) -> str:
    """The LSOA whose population-weighted centroid is nearest the point (on the grid)."""
    _, idx = store.lsoa_tree.query(to_bng(point.lon, point.lat))
    return store.features["lsoa21cd"][int(idx)]
