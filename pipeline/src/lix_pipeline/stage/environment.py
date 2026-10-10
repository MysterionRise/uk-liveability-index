"""Green space access points (OS Open Greenspace) and flood risk by postcode (EA)."""

import numpy as np
import polars as pl
import pyogrio

from lix_core.codes import in_scope
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.stage.health import geocode_postcodes

logger = setup_logging("stage.environment")


def stage_os_greenspace() -> pl.LazyFrame:
    """Every way into a green space, with the site's function and name.

    Access points rather than polygons: a park is only as close as its nearest
    entrance, and a big park has more entrances, so counting entrances within
    walking distance favours larger spaces without extra weighting. Kept for all of
    Great Britain so English homes near the borders see Welsh and Scottish parks.
    """
    gpkg = next((data_dir("raw") / "os_greenspace").glob("**/*.gpkg"))
    sites = pl.from_pandas(
        pyogrio.read_dataframe(
            gpkg,
            layer="greenspace_site",
            columns=["id", "function", "distinctive_name_1"],
            read_geometry=False,
        )
    ).rename({"id": "site_id", "distinctive_name_1": "site_name"})
    points = pyogrio.read_dataframe(
        gpkg, layer="access_point", columns=["access_type", "ref_to_greenspace_site"]
    )
    if points.crs is not None and points.crs.to_epsg() != 27700:
        points = points.to_crs(27700)
    access = pl.DataFrame(
        {
            "site_id": points["ref_to_greenspace_site"].to_numpy(),
            "access_type": points["access_type"].to_numpy(),
            "x": points.geometry.x.to_numpy(),
            "y": points.geometry.y.to_numpy(),
        }
    )
    df = access.join(sites, on="site_id", how="inner").filter(
        pl.col("access_type").str.contains("Pedestrian")
    )
    logger.info(
        f"{df.height:,} pedestrian access points to {df['site_id'].n_unique():,} green spaces"
    )
    return df.lazy()


FLOOD_BANDS = ("High", "Medium", "Low", "VeryLow")
NRW_RIVERS_SEA = ("nrw_fraw_rivers", "nrw_fraw_sea")
NRW_SURFACE_WATER = ("nrw_fraw_surface_water",)
# A property point stands for a building: buffered by this much, Wales's national totals
# land within a quarter of NRW's published counts for rivers and the sea and for surface
# water alike (the point alone finds half of NRW's surface-water properties; 10m finds
# twice as many), so this is the receptor the estimate uses
NRW_RECEPTOR_M = 5.0
COUNT_COLUMNS = (
    "res_high",
    "res_medium",
    "res_low",
    "res_verylow",
    "any_high",
    "any_medium",
    "any_low",
)


def _layer_polygons(slug: str, bands: tuple[str, ...]):
    """Risk polygons of one NRW layer in the given bands, from every file of the download."""
    import geopandas as gpd

    frames = []
    for path in sorted((data_dir("raw") / slug).glob("*.gpkg")):
        gdf = gpd.read_file(
            path, columns=["risk"], where=f"risk IN ({', '.join(repr(b) for b in bands)})"
        )
        frames.append(gdf.to_crs(27700)[["risk", "geometry"]])
    out = (
        gpd.GeoDataFrame(pd_concat(frames), crs=27700)
        if frames
        else gpd.GeoDataFrame(columns=["risk", "geometry"], crs=27700)
    )
    logger.info(f"{slug}: {len(out):,} polygons in {bands}")
    return out


def pd_concat(frames):
    import pandas as pd

    return pd.concat(frames, ignore_index=True)


def inside_share(receptors, polygons) -> np.ndarray:
    """1 for each receptor geometry touching any of the polygons, else 0."""
    import shapely

    out = np.zeros(len(receptors))
    if len(polygons) == 0:
        return out
    tree = shapely.STRtree(polygons.geometry.values)
    hit, _ = tree.query(receptors.values, predicate="intersects")
    out[np.unique(hit)] = 1
    return out


def _nrw_homes_at_risk() -> pl.DataFrame:
    """Welsh homes at each flood likelihood, estimated from NRW's risk areas.

    NRW publishes risk polygons rather than counts, so the count is made the way the
    agencies make theirs: the share of an LSOA's property points (OS Open UPRN, each
    buffered by ``NRW_RECEPTOR_M`` to stand for its building) touching the high areas,
    and the high or medium areas, across rivers and the sea (``res_``) and across those
    plus surface water and small watercourses (``any_``), applied to the LSOA's
    dwellings (VOA). Bands match the EA's: High above 1 in 30 a year, Medium 1 in 30 to
    1 in 100.
    """
    import geopandas as gpd

    from lix_core.codes import nation_of

    props = (
        pl.scan_parquet(data_dir("staged") / "os_open_uprn.parquet")
        .filter(nation_of("lsoa21cd") == "W")
        .select("lsoa21cd", "x", "y")
        .collect()
    )
    points = gpd.GeoSeries(
        gpd.points_from_xy(props["x"].to_numpy(), props["y"].to_numpy()), crs=27700
    ).buffer(NRW_RECEPTOR_M)
    layers = {
        slug: _layer_polygons(slug, ("High", "Medium"))
        for slug in NRW_RIVERS_SEA + NRW_SURFACE_WATER
    }
    shares = {}
    for prefix, slugs in (("res", NRW_RIVERS_SEA), ("any", NRW_RIVERS_SEA + NRW_SURFACE_WATER)):
        polys = gpd.GeoDataFrame(pd_concat([layers[s] for s in slugs]), crs=27700)
        high_or_medium = inside_share(points, polys)
        high = inside_share(points, polys[polys["risk"] == "High"])
        shares[f"{prefix}_high"] = high
        shares[f"{prefix}_medium"] = np.clip(high_or_medium - high, 0, 1)
    per_point = props.select("lsoa21cd").with_columns(
        *[pl.Series(name, values) for name, values in shares.items()]
    )
    dwellings = pl.read_parquet(data_dir("staged") / "voa_ctsop.parquet").select(
        "lsoa21cd", "dwellings"
    )
    logger.info(f"Wales: {props.height:,} property points against the NRW risk areas")
    return (
        per_point.group_by("lsoa21cd")
        .agg(pl.col(c).mean() for c in shares)
        .join(dwellings, on="lsoa21cd", how="inner")
        .select(
            "lsoa21cd",
            *[(pl.col(c) * pl.col("dwellings")).round().cast(pl.Int64).alias(c) for c in shares],
        )
        .with_columns(
            pl.lit(0, pl.Int64).alias("res_low"),
            pl.lit(0, pl.Int64).alias("res_verylow"),
            pl.lit(0, pl.Int64).alias("any_low"),
            pl.lit(True).alias("estimated"),
        )
        .select("lsoa21cd", *COUNT_COLUMNS, "estimated")
        .sort("lsoa21cd")
    )


def stage_flood() -> pl.LazyFrame:
    """Homes per flood likelihood band per LSOA for every active nation.

    ``res_*`` count homes at risk from rivers and the sea, ``any_*`` from rivers, the sea
    or surface water (each home by its highest risk); ``estimated`` marks nations where
    the counts are split from risk areas rather than published per postcode.
    """
    from lix_core.codes import active_nations, nation_of

    frames = []
    if "E" in active_nations():
        rivers_sea = pl.read_parquet(data_dir("staged") / "ea_flood_postcodes.parquet")
        any_ = pl.read_parquet(data_dir("staged") / "ea_flood_all_postcodes.parquet").select(
            "lsoa21cd", "any_high", "any_medium", "any_low"
        )
        frames.append(
            rivers_sea.join(any_, on="lsoa21cd", how="full", coalesce=True)
            # Postcodes straddling the border put a few Welsh LSOAs in the English files
            .filter(nation_of("lsoa21cd") == "E")
            .with_columns(pl.lit(False).alias("estimated"))
        )
    if "W" in active_nations():
        wales = _nrw_homes_at_risk()
        logger.info(f"Wales: {wales.height:,} LSOAs with homes in NRW flood risk areas")
        frames.append(wales)
    return (
        pl.concat(frames, how="diagonal_relaxed")
        .with_columns(pl.col(c).fill_null(0) for c in COUNT_COLUMNS)
        .select("lsoa21cd", *COUNT_COLUMNS, "estimated")
        .sort("lsoa21cd")
        .lazy()
    )


def stage_ea_flood_all_postcodes() -> pl.LazyFrame:
    """Addresses at each flood likelihood from rivers, the sea or surface water, per LSOA.

    The Environment Agency's postcode search tool data lists every English postcode with
    its addresses in high, medium and low risk areas, each address by the higher of the
    rivers-and-sea and surface-water assessments; very low is not listed. The counts are
    addresses (AddressBase), so a parade of shops counts alongside the flats above it.
    """
    path = data_dir("raw") / "ea_flood_postcode_tool" / "ea_flood_postcode_tool.csv"
    raw = pl.read_csv(path, infer_schema=False, encoding="utf8-lossy")
    raw = raw.rename({c: c.lstrip("\ufeff") for c in raw.columns})
    df = raw.select(
        pl.col("Postcode").alias("postcode"),
        pl.col("HIGH_CNT").cast(pl.Int64).alias("any_high"),
        pl.col("MED_CNT").cast(pl.Int64).alias("any_medium"),
        pl.col("LOW_CNT").cast(pl.Int64).alias("any_low"),
        (pl.col("GWTR_RISK") == "Possible").alias("groundwater"),
    )
    df = geocode_postcodes(df)
    at_risk = pl.col("any_high") + pl.col("any_medium")
    located = df.filter(pl.col("lsoa21cd").is_not_null()).select(at_risk.sum()).item()
    located_share = located / max(df.select(at_risk.sum()).item(), 1)
    per_lsoa = (
        df.filter(in_scope("lsoa21cd"))
        .group_by("lsoa21cd")
        .agg(
            pl.col("any_high").sum(),
            pl.col("any_medium").sum(),
            pl.col("any_low").sum(),
            pl.col("groundwater").mean().alias("share_groundwater"),
        )
        .sort("lsoa21cd")
    )
    logger.info(
        f"{per_lsoa.height:,} LSOAs; {located_share:.2%} of addresses at high or medium risk "
        f"from any source located"
    )
    return per_lsoa.lazy()


def stage_ea_flood_postcodes() -> pl.LazyFrame:
    """Residential properties at each flood likelihood, summed per LSOA.

    The file lists only postcodes with at least one property in a risk area (any
    likelihood); LSOAs with none listed have no property at risk from rivers or sea.
    High = at least 3.3% a year, Medium = 1–3.3%, Low = 0.1–1%, Very low = under 0.1%.
    """
    path = next((data_dir("raw") / "ea_flood_postcodes").glob("**/*Postcodes_AtRisk.csv"))
    raw = pl.read_csv(path, infer_schema=False, encoding="utf8-lossy")
    df = raw.select(
        pl.col("PC").alias("postcode"),
        *[pl.col(f"RES_CNT_{b}").cast(pl.Int64).alias(f"res_{b.lower()}") for b in FLOOD_BANDS],
    )
    df = geocode_postcodes(df)
    # Unmatched rows are mostly pseudo-postcodes (e.g. "VPO01148") with very-low-risk
    # property counts, so coverage is judged on the high and medium bands
    at_risk = pl.col("res_high") + pl.col("res_medium")
    located = df.filter(pl.col("lsoa21cd").is_not_null()).select(at_risk.sum()).item()
    located_share = located / max(df.select(at_risk.sum()).item(), 1)
    per_lsoa = (
        df.filter(in_scope("lsoa21cd"))
        .group_by("lsoa21cd")
        .agg(pl.col(f"res_{b.lower()}").sum() for b in FLOOD_BANDS)
        .sort("lsoa21cd")
    )
    logger.info(
        f"{per_lsoa.height:,} LSOAs with properties in flood risk areas; "
        f"{located_share:.2%} of homes at high or medium risk located"
    )
    return per_lsoa.lazy()
