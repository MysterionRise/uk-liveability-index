"""Access to OpenStreetMap points of interest from residential postcodes."""

import polars as pl

from lix_core.log import setup_logging

logger = setup_logging("indicators.amenities")


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
            # Most facilities carry no access tag at all: only a listed value excludes
            # (``~is_in`` alone is null for a null tag, and a filter drops null)
            pois = pois.filter(pl.col(tag).is_null() | ~pl.col(tag).is_in(banned))
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


def osm_nearest(
    ctx, values: list[str] | None = None, key: str | None = None, beyond_cap: float | None = None
) -> pl.DataFrame:
    """Mean distance from homes to the nearest matching POI (metres), under the shared
    search-cap rule (``common.capped_distance``, #213)."""
    from lix_pipeline.indicators.common import capped_distance

    return capped_distance(ctx.access, _pois(ctx, values, key), beyond_cap)


# A service type counts as staged for a nation when most of its areas have one this close
STAGED_WITHIN_M = 50_000.0


def _service_points(ctx, service: dict) -> pl.DataFrame:
    """The points of one everyday service type: a union of ``any`` entries, OpenStreetMap
    by key and values, or a staged table filtered by ``column`` to ``values`` or to at
    least ``min``."""
    if "any" in service:
        frames = [_service_points(ctx, part).select("x", "y") for part in service["any"]]
        return pl.concat(frames)
    if service["slug"] == "osm_pois":
        return _pois(ctx, service.get("values"), service.get("key")).select("x", "y")
    pts = ctx.staged(service["slug"])
    column = service.get("column")
    if column and "values" in service:
        pts = pts.filter(pl.col(column).is_in(service["values"]))
    if column and "min" in service:
        pts = pts.filter(pl.col(column) >= service["min"])
    return pts.select("x", "y")


def service_mix(ctx, radius_m: float, services: list[dict]) -> pl.DataFrame:
    """The share of everyday service types the typical home has within ``radius_m``
    (0–100): over the types, the share of the area's homes with any of that type in
    reach, summed, over the number of types the nation has data for (Ireland stages no
    schools, so its homes are measured over the other types). A type is "staged" for a
    nation when more than half of its areas have one within 50 km: Irish homes near the
    border reach Northern Irish schools, which does not make a school list for Ireland
    (the v0.6.3 release pass found every Irish area capped at six of seven). A staged
    type nobody has in reach still counts against the homes, and the log names the
    types left out (#75)."""
    from lix_core.codes import nation_of

    shares = []
    for i, service in enumerate(services):
        access = ctx.access(
            _service_points(ctx, service), radius_m=radius_m, max_nearest_m=STAGED_WITHIN_M
        )
        shares.append(
            access.select(
                "lsoa21cd",
                pl.col("share_with_any").alias(f"s{i}"),
                # A home with nothing within the cap reads the cap itself
                (pl.col("nearest_m") < STAGED_WITHIN_M).alias(f"near{i}"),
            )
        )
    wide = shares[0]
    for frame in shares[1:]:
        wide = wide.join(frame, on="lsoa21cd", how="full", coalesce=True)
    columns = [f"s{i}" for i in range(len(services))]
    wide = wide.with_columns(nation_of("lsoa21cd").alias("nation"))
    # A type within the search cap of no more than half the nation's areas is not
    # measured there
    staged = wide.group_by("nation").agg(
        *[
            (pl.col(f"near{i}").fill_null(False).mean() > 0.5).alias(c)
            for i, c in enumerate(columns)
        ]
    )
    for row in staged.iter_rows(named=True):
        missing = [services[int(c[1:])].get("name", c) for c in columns if not row[c]]
        if missing:
            logger.info(f"{row['nation']}: no points for {missing}, measured over the rest")
    counted = staged.select(
        "nation", pl.sum_horizontal([pl.col(c).cast(pl.Int32) for c in columns]).alias("n_types")
    )
    return wide.join(counted, on="nation", how="left").select(
        "lsoa21cd",
        pl.when(pl.col("n_types") > 0)
        .then(
            pl.sum_horizontal([pl.col(c).fill_null(0) for c in columns]) / pl.col("n_types") * 100
        )
        .otherwise(None)
        .alias("value"),
    )
