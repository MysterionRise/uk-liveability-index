"""Well-run pubs: OpenStreetMap pubs matched to Food Standards Agency hygiene ratings.

A pub counts as well run when:

- it is mapped in OpenStreetMap as ``amenity=pub`` (so nightclubs and bars, which share
  the FSA's "Pub/bar/nightclub" type, are left out), and
- it matches an FSA record rated 4 or 5 at an inspection in the last three years (in
  Scotland, where the Food Hygiene Information Scheme grades Pass or Improvement
  Required rather than 0–5, a Pass in the last three years).

Matching uses the OSM ``fhrs:id`` tag where mappers added one (about 63% of pubs),
whatever the FSA business type (food-led pubs often register as restaurants and inns
as hotels); otherwise the nearest pub, restaurant or hotel within 75m whose name is
similar enough. Requiring both
sources also drops pubs that have closed but linger in one of them. Pubs advertising
real ale, food, outdoor seating or a microbrewery count a little more (never less).

Pubs that OpenStreetMap hasn't mapped are added from Overture Maps Places (confidence
at least 0.8, no OSM pub within 60m by the same name); they still need an FSA match to
count, so every well-run pub is backed by two independent sources.

Hygiene ratings say how well a pub is run, not how good the beer is, so the UI calls
these "well-run pubs".
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
from rapidfuzz import fuzz, process
from scipy.spatial import cKDTree

from lix_core.paths import data_dir

PUB_BAR_NIGHTCLUB = 7843
# Food-led pubs often register as restaurants, and inns as hotels
PUB_LIKE_TYPES = [PUB_BAR_NIGHTCLUB, 1, 7842]  # + Restaurant/Cafe/Canteen, Hotel/B&B
MATCH_RADIUS_M = 75
NAME_SIMILARITY = 80
RATING_MAX_AGE_DAYS = 3 * 365
# Scotland's Food Hygiene Information Scheme outcomes that count as well run
FHIS_PASS = ["Pass", "Pass and Eat Safe"]
OVERTURE_MIN_CONFIDENCE = 0.8
DUPLICATE_RADIUS_M = 60
DUPLICATE_ANY_NAME_M = 20  # this close, a different name is still the same pub
SOURCES = {
    "osm": ("OpenStreetMap + Food Standards Agency", "ODbL-1.0"),
    "overture": ("Overture Maps + Food Standards Agency", "CDLA-Permissive-2.0"),
}
CHARACTER_TAGS = ["real_ale", "food", "outdoor_seating", "beer_garden", "microbrewery"]
CHARACTER_BONUS = 0.1  # per tag, up to MAX_BONUS
MAX_BONUS = 0.3

_STOPWORDS = r"\b(the|ye olde|ye|pub|inn|tavern|bar|public house|ph|and|ltd|limited)\b"


def normalise_name(name: pl.Expr) -> pl.Expr:
    return (
        name.str.to_lowercase()
        .str.replace_all("&", " and ")
        .str.replace_all(r"[’']", "")
        .str.replace_all(_STOPWORDS, " ")
        .str.replace_all(r"[^a-z0-9]+", " ")
        .str.strip_chars()
    )


def match_pubs(
    osm: pl.DataFrame, fsa: pl.DataFrame, spatial_fsa: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Attach the matching FSA record to each OSM pub (by fhrs:id, else near + similar name).

    ``osm``: osm_id, name, x, y, fhrs_id (string, may hold several ids separated by ";").
    ``fsa``: fhrs_id (int), name, x, y, rating, rating_status, rating_date — every
    business, since an explicit fhrs:id link is trusted whatever the business type.
    ``spatial_fsa``: the subset eligible for name + distance matching (default ``fsa``).
    Returns ``osm`` with fsa_id, rating, rating_status, rating_date and match ("fhrs_id",
    "spatial", None).
    """
    spatial_fsa = fsa if spatial_fsa is None else spatial_fsa
    # 1. Explicit links from OSM's fhrs:id tag
    links = (
        osm.select("osm_id", pl.col("fhrs_id").str.split(";"))
        .explode("fhrs_id")
        .with_columns(pl.col("fhrs_id").str.strip_chars().cast(pl.Int64, strict=False))
        .join(fsa.select(pl.col("fhrs_id"), "rating", "rating_status", "rating_date"), on="fhrs_id")
        .sort("rating_date", descending=True, nulls_last=True)
        .unique("osm_id", keep="first")
        .rename({"fhrs_id": "fsa_id"})
        .with_columns(pl.lit("fhrs_id").alias("match"))
    )
    matched = osm.join(links, on="osm_id", how="left")

    # 2. Spatial + name match for the rest
    todo = matched.filter(pl.col("match").is_null() & pl.col("x").is_not_null())
    cand = spatial_fsa.filter(pl.col("x").is_not_null()).with_columns(
        normalise_name(pl.col("name")).alias("_norm")
    )
    if todo.height and cand.height:
        tree = cKDTree(np.column_stack([cand["x"].to_numpy(), cand["y"].to_numpy()]))
        names = todo.select(normalise_name(pl.col("name").fill_null(""))).to_series().to_list()
        near = tree.query_ball_point(
            np.column_stack([todo["x"].to_numpy(), todo["y"].to_numpy()]), r=MATCH_RADIUS_M
        )
        cand_norm = cand["_norm"].to_list()
        found = {"osm_id": [], "fsa_id": [], "rating": [], "rating_status": [], "rating_date": []}
        for osm_id, name, idx in zip(todo["osm_id"].to_list(), names, near):
            if not name or not idx:
                continue
            best = process.extractOne(
                name, {i: cand_norm[i] for i in idx}, scorer=fuzz.token_set_ratio,
                score_cutoff=NAME_SIMILARITY,
            )  # fmt: skip
            if best:
                row = cand.row(best[2], named=True)
                found["osm_id"].append(osm_id)
                found["fsa_id"].append(row["fhrs_id"])
                found["rating"].append(row["rating"])
                found["rating_status"].append(row["rating_status"])
                found["rating_date"].append(row["rating_date"])
        spatial = pl.DataFrame(
            found,
            schema={
                "osm_id": pl.Int64,
                "fsa_id": pl.Int64,
                "rating": pl.Int8,
                "rating_status": pl.Utf8,
                "rating_date": pl.Date,
            },
        ).with_columns(pl.lit("spatial").alias("match"))
        matched = matched.update(spatial, on="osm_id", how="left")
    return matched


def overture_additions(osm: pl.DataFrame, overture: pl.DataFrame) -> pl.DataFrame:
    """Overture pubs that aren't already an OSM pub (by distance and name)."""
    candidates = overture.filter(pl.col("confidence") >= OVERTURE_MIN_CONFIDENCE)
    located = osm.filter(pl.col("x").is_not_null())
    if candidates.is_empty() or located.is_empty():
        return candidates
    tree = cKDTree(np.column_stack([located["x"].to_numpy(), located["y"].to_numpy()]))
    xy = np.column_stack([candidates["x"].to_numpy(), candidates["y"].to_numpy()])
    near = tree.query_ball_point(xy, r=DUPLICATE_RADIUS_M)
    dist, _ = tree.query(xy, k=1)
    osm_names = located.select(normalise_name(pl.col("name").fill_null(""))).to_series().to_list()
    ov_names = candidates.select(normalise_name(pl.col("name").fill_null(""))).to_series().to_list()
    keep = []
    for name, idx, d in zip(ov_names, near, dist):
        if d <= DUPLICATE_ANY_NAME_M:
            keep.append(False)
            continue
        same = any(fuzz.token_set_ratio(name, osm_names[i]) >= NAME_SIMILARITY for i in idx)
        keep.append(not same)
    return candidates.filter(pl.Series(keep))


def build_pubs(ctx, as_of: date | None = None) -> pl.DataFrame:
    """Every pub in England (OSM, plus Overture's that OSM lacks) with its FSA match."""
    as_of = as_of or date.today()
    osm = (
        ctx.staged("osm_pois")
        .filter(pl.col("value") == "pub")
        .select("osm_type", "osm_id", "name", "x", "y", "lon", "lat", "fhrs_id", *CHARACTER_TAGS)
        .with_columns(
            pl.concat_str("osm_type", "osm_id").alias("pub_id"), pl.lit("osm").alias("source")
        )
    )
    extra = overture_additions(osm, ctx.staged("overture_pubs")).select(
        pl.concat_str(pl.lit("overture:"), "id").alias("pub_id"),
        "name", "x", "y", "lon", "lat", pl.lit("overture").alias("source"),
    )  # fmt: skip
    candidates = pl.concat([osm, extra], how="diagonal_relaxed")
    # Ids are unique per source only; key on a row number while matching
    keyed = candidates.with_row_index("_key").with_columns(pl.col("_key").cast(pl.Int64))
    fsa = ctx.staged("fsa_fhrs").select(
        "fhrs_id", "name", "x", "y", "rating", "rating_status", "rating_date", "business_type_id"
    )
    matched = match_pubs(
        keyed.select(pl.col("_key").alias("osm_id"), "name", "x", "y", "fhrs_id"),
        fsa,
        spatial_fsa=fsa.filter(pl.col("business_type_id").is_in(PUB_LIKE_TYPES)),
    ).select(
        pl.col("osm_id").alias("_key"), "fsa_id", "rating", "rating_status", "rating_date", "match"
    )

    pubs = keyed.join(matched, on="_key", how="left").drop("_key")
    recent = pl.col("rating_date") >= pl.lit(as_of - timedelta(days=RATING_MAX_AGE_DAYS))
    character = pl.sum_horizontal(
        pl.col(t).is_in(["yes", "only", "real_ale"]).fill_null(False).cast(pl.Int8)
        for t in CHARACTER_TAGS
    )
    # A 0–5 rating of 4 or 5 (FHRS), or a Pass under Scotland's FHIS, which has no numbers
    good = (pl.col("rating") >= 4) | pl.col("rating_status").is_in(FHIS_PASS)
    pubs = pubs.with_columns(
        (good & recent).fill_null(False).alias("well_run"),
        (1 + (character * CHARACTER_BONUS).clip(0, MAX_BONUS)).alias("weight"),
        pl.col("source").replace_strict({k: v[0] for k, v in SOURCES.items()}).alias("source_name"),
        pl.col("source").replace_strict({k: v[1] for k, v in SOURCES.items()}).alias("licence"),
    )
    return pubs


def well_run_pub_access(ctx, radius_m: float, cap: float) -> pl.DataFrame:
    """Distance-decayed access to well-run pubs; also saves the pub list for the API."""
    pubs = build_pubs(ctx)
    out = data_dir("indicators") / "pubs.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pubs.write_parquet(out)
    well_run = pubs.filter(pl.col("well_run"))
    access = ctx.access(well_run, radius_m=radius_m, cap=cap, weight="weight")
    return access.select("lsoa21cd", pl.col("score").alias("value"))
