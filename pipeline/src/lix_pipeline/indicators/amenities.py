"""Access to OpenStreetMap points of interest from residential postcodes."""

import polars as pl


def _pois(
    ctx, values: list[str] | None, key: str | None, exclude: dict[str, list[str]] | None = None
) -> pl.DataFrame:
    """Staged OpenStreetMap points by key and value; ``exclude`` drops rows whose tag has
    one of the listed values (``{"access": ["private"]}`` for members-only facilities)."""
    pois = ctx.staged("osm_pois")
    if key:
        pois = pois.filter(pl.col("key") == key)
    if values:
        pois = pois.filter(pl.col("value").is_in(values))
    for tag, banned in (exclude or {}).items():
        if tag in pois.columns:
            pois = pois.filter(~pl.col(tag).is_in(banned))
    return pois


def osm_access(
    ctx,
    radius_m: float,
    cap: float,
    values: list[str] | None = None,
    key: str | None = None,
    sigma_m: float | None = None,
    exclude: dict[str, list[str]] | None = None,
) -> pl.DataFrame:
    """Distance-decayed, log-capped count of matching POIs (0–1)."""
    pois = _pois(ctx, values, key, exclude)
    access = ctx.access(pois, radius_m=radius_m, cap=cap, sigma_m=sigma_m)
    return access.select("lsoa21cd", pl.col("score").alias("value"))


def osm_nearest(ctx, values: list[str] | None = None, key: str | None = None) -> pl.DataFrame:
    """Mean distance from homes to the nearest matching POI (metres)."""
    access = ctx.access(_pois(ctx, values, key), radius_m=1)
    return access.select("lsoa21cd", pl.col("nearest_m").alias("value"))
