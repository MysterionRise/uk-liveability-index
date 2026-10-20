"""Nearest points of interest of a category around a place."""

import json

from lix_api.models import Poi, Point, PoiResult
from lix_api.services.search import resolve_point
from lix_api.store import Store, to_bng

# "well_run_pub" has its own tree (the pubs meeting the well-run definition) when the
# pack has any; the aliases map everyday names onto categories
CATEGORY_ALIASES = {"school": "primary_school", "doctor": "gp"}


def categories(store: Store) -> list[str]:
    return sorted({*store.poi_trees, "well_run_pub"})


def nearest_pois(
    store: Store, category: str, near: str, max_km: float = 2.0, limit: int = 10
) -> PoiResult:
    base = CATEGORY_ALIASES.get(category, category)
    if base not in store.poi_trees:
        if base == "well_run_pub" and "pub" in store.poi_trees:
            origin, label = resolve_point(store, near)
            return PoiResult(category=category, origin=origin, origin_label=label, pois=[])
        raise ValueError(f"Unknown category {category!r}; choose from {categories(store)}")
    origin, label = resolve_point(store, near)
    x, y = to_bng(origin.lon, origin.lat)
    tree, df = store.poi_trees[base]
    k = min(len(df), limit)
    dist, idx = tree.query([x, y], k=k, distance_upper_bound=max_km * 1000)
    pois = []
    for d, i in zip(list(dist) if k > 1 else [dist], list(idx) if k > 1 else [idx]):
        if d == float("inf"):
            break
        row = df.row(int(i), named=True)
        detail = json.loads(row["detail"]) if row["detail"] else {}
        pois.append(
            Poi(
                category=category,
                name=row["name"],
                distance_m=round(float(d)),
                point=Point(lat=row["lat"], lon=row["lon"]),
                source=row["source"],
                licence=row["licence"],
                detail=detail,
            )  # fmt: skip
        )
        if len(pois) >= limit:
            break
    return PoiResult(category=category, origin=origin, origin_label=label, pois=pois)
