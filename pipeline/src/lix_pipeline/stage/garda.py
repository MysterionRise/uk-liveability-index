"""Ireland's recorded crime: the CSO's counts by Garda station spread over the station's
sub-district by population.

The CSO publishes recorded crime (PULSE, statistics under reservation) by Garda station
and offence group (CJA11), not by location. The Census 2011 boundary files draw a
sub-district around each station as it was then; a station's incidents are spread over
the Small Areas whose centroid falls in its sub-district, in proportion to population,
so every area carries its station's rate. Station names that changed since 2011 (Irish
forms, spellings, stations opened or merged) come through the curated
``garda_station_aliases`` list; a sub-district whose station no longer reports joins the
nearest one that does. The rows look like police.uk's: ``lsoa21cd``, ``crime_type``,
``force``, ``n`` and ``force_months``, so the safety indicators read them unchanged.
Offence groups map onto the police.uk categories the indicators name; "theft and related
offences" covers every kind of theft, so Ireland's theft rate reads wider than Britain's
(it is benchmarked within Ireland).
"""

import re
import unicodedata

import geopandas as gpd
import numpy as np
import polars as pl

from lix_core.geo import working_crs
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file

logger = setup_logging("stage.garda")

FORCE = "An Garda Síochána"
# ICCS offence group (the code the CSO appends to the label) → the police.uk category
# the safety indicators count
OFFENCE_GROUPS = {
    "03": "Violence and sexual offences",
    "06": "Robbery",
    "07": "Burglary",
    "08": "Theft from the person",
    "12": "Criminal damage and arson",
    "13": "Anti-social behaviour",
    "10": "Drugs",
    "11": "Possession of weapons",
    "04": "Dangerous or negligent acts",
    "05": "Kidnapping and related offences",
    "09": "Fraud",
    "14": "Road and traffic offences",
    "15": "Offences against justice",
    "16": "Other crime",
}
# Dublin's divisions are named after the metropolitan region, not the county
REGION_COUNTIES = {"D.M.R.": "Dublin", "DMR": "Dublin"}


def fold(text: str | None) -> str:
    ascii_ = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", ascii_.lower()).strip()


def station_name(label: str) -> str:
    """ "Bailieboro, Louth/Cavan/Monaghan Division (11101)" → "Bailieboro"."""
    return re.sub(r",.*$", "", label).strip()


def station_counts(cja11: pl.DataFrame, year: int | None = None) -> tuple[pl.DataFrame, int]:
    """The CSO table (strings) → one row per station, offence category and the chosen
    year (the latest unless given): ``station``, ``station_code``, ``crime_type``, ``n``."""
    df = cja11.select(
        pl.col("Garda Station").alias("station"),
        pl.col("C03037V05454").alias("station_code"),
        pl.col("Year").cast(pl.Int32).alias("year"),
        pl.col("Type of Offence").str.extract(r"\((\d\d)\)\s*$", 1).alias("group"),
        pl.col("VALUE").cast(pl.Float64, strict=False).alias("n"),
    ).filter(pl.col("group").is_in(list(OFFENCE_GROUPS)) & pl.col("n").is_not_null())
    year = year or int(df["year"].max())
    out = (
        df.filter(pl.col("year") == year)
        .with_columns(pl.col("group").replace_strict(OFFENCE_GROUPS).alias("crime_type"))
        .group_by("station", "station_code", "crime_type")
        .agg(pl.col("n").sum())
        .sort("station", "crime_type")
    )
    return out, year


def match_stations(
    labels: list[str], subdistricts: pl.DataFrame, aliases: pl.DataFrame
) -> dict[str, str | None]:
    """Station label → the ``SUB_CODE`` of its 2011 sub-district, by the station's name
    (English or Irish, accents and punctuation folded), through the alias list where the
    names differ, and with homonyms told apart by the county the label names."""
    by_name: dict[str, list[tuple[str, str]]] = {}
    for row in subdistricts.iter_rows(named=True):
        for name in (row["SUB_DIST"], row["SUB_IRISH"]):
            if name:
                by_name.setdefault(fold(name), []).append((row["SUB_CODE"], row["COUNTY_1"]))
    by_alias = {
        fold(r["station"]): (r["sub_district"], r["county"]) for r in aliases.iter_rows(named=True)
    }
    out: dict[str, str | None] = {}
    for label in labels:
        name = station_name(label)
        wanted_county = None
        key = fold(name)
        if key in by_alias:
            sub_district, wanted_county = by_alias[key]
            key = fold(sub_district)
        candidates = by_name.get(key, [])
        if len(candidates) > 1:
            hint = wanted_county or next(
                (
                    county
                    for county in sorted({c for _, c in candidates})
                    if county.lower() in label.lower()
                ),
                None,
            )
            if hint is None:
                hint = next((c for r, c in REGION_COUNTIES.items() if r in label), None)
            if hint:
                candidates = [c for c in candidates if c[1].lower() == hint.lower()] or candidates
        elif wanted_county and candidates and candidates[0][1].lower() != wanted_county.lower():
            candidates = []
        out[label] = candidates[0][0] if candidates else None
    return out


def areas_to_subdistricts(
    areas: pl.DataFrame, polygons: gpd.GeoDataFrame, served: set[str]
) -> pl.DataFrame:
    """Each area (``lsoa21cd``, ``pwc_x``, ``pwc_y``) → the ``sub_code`` whose polygon
    holds its centroid (the nearest polygon for a centroid outside all), then the nearest
    sub-district with a reporting station where its own has none."""
    pts = gpd.GeoDataFrame(
        {"lsoa21cd": areas["lsoa21cd"].to_list()},
        geometry=gpd.points_from_xy(areas["pwc_x"].to_numpy(), areas["pwc_y"].to_numpy()),
        crs=polygons.crs,
    )
    hit = gpd.sjoin(pts, polygons[["SUB_CODE", "geometry"]], how="left", predicate="intersects")
    hit = hit[~hit.index.duplicated(keep="first")]
    missing = hit["SUB_CODE"].isna()
    if missing.any():
        nearest = gpd.sjoin_nearest(
            pts[missing.to_numpy()], polygons[["SUB_CODE", "geometry"]], how="left"
        )
        nearest = nearest[~nearest.index.duplicated(keep="first")]
        hit.loc[missing, "SUB_CODE"] = nearest["SUB_CODE"].to_numpy()
    # A sub-district without a reporting station takes the nearest served one
    centroids = polygons.set_index("SUB_CODE").geometry.centroid
    served_codes = [c for c in centroids.index if c in served]
    if served_codes:
        served_xy = np.column_stack([centroids[served_codes].x, centroids[served_codes].y])
        remap = {}
        for code in centroids.index:
            if code not in served:
                d = np.hypot(
                    served_xy[:, 0] - centroids[code].x, served_xy[:, 1] - centroids[code].y
                )
                remap[code] = served_codes[int(d.argmin())]
        if remap:
            logger.info(
                f"{len(remap)} sub-districts without a reporting station joined the nearest"
            )
            hit["SUB_CODE"] = hit["SUB_CODE"].map(lambda c: remap.get(c, c))
    return pl.DataFrame(
        {"lsoa21cd": hit["lsoa21cd"].to_list(), "sub_code": hit["SUB_CODE"].to_list()}
    )


def spread_to_areas(counts: pl.DataFrame, areas: pl.DataFrame) -> pl.DataFrame:
    """Sub-district counts (``sub_code``, ``crime_type``, ``n``) → each of its areas
    (``lsoa21cd``, ``population``, ``sub_code``) in proportion to population."""
    weights = areas.with_columns(
        (pl.col("population") / pl.col("population").sum().over("sub_code")).alias("share")
    )
    return (
        weights.join(counts, on="sub_code", how="inner")
        .select(
            "lsoa21cd",
            "crime_type",
            pl.lit(FORCE).alias("force"),
            (pl.col("n") * pl.col("share")).alias("n"),
            pl.lit(12, pl.Int32).alias("force_months"),
        )
        .sort("lsoa21cd", "crime_type")
    )


def garda_crime() -> pl.DataFrame:
    cja11 = pl.read_csv(raw_file("cso_cja11", "csv"), infer_schema_length=0, encoding="utf8-lossy")
    counts, year = station_counts(cja11)
    folder = data_dir("raw") / "cso_garda_subdistricts"
    shp = sorted(folder.glob("**/*.shp"))
    if not shp:
        raise FileNotFoundError(f"No Garda sub-district shapefile under {folder}")
    polygons = gpd.read_file(shp[0]).to_crs(working_crs())
    subdistricts = pl.from_pandas(polygons[["SUB_CODE", "SUB_DIST", "SUB_IRISH", "COUNTY_1"]])
    aliases = pl.read_csv(raw_file("garda_station_aliases", "csv"), infer_schema_length=0)
    matched = match_stations(counts["station"].unique().to_list(), subdistricts, aliases)
    unmatched = [s for s, code in matched.items() if code is None]
    if unmatched:
        lost = counts.filter(pl.col("station").is_in(unmatched))["n"].sum()
        logger.warning(
            f"{len(unmatched)} stations match no sub-district ({lost:,.0f} incidents left "
            f"out): {[station_name(s) for s in unmatched][:10]}"
        )
    counts = (
        counts.with_columns(
            pl.col("station").replace_strict(matched, default=None).alias("sub_code")
        )
        .filter(pl.col("sub_code").is_not_null())
        .group_by("sub_code", "crime_type")
        .agg(pl.col("n").sum())
    )
    geo = pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet").filter(pl.col("nation") == "I")
    areas = areas_to_subdistricts(
        geo.select("lsoa21cd", "pwc_x", "pwc_y"), polygons, set(counts["sub_code"])
    ).join(geo.select("lsoa21cd", "population"), on="lsoa21cd")
    out = spread_to_areas(counts, areas)
    logger.info(
        f"Garda {year}: {counts['n'].sum():,.0f} incidents at {counts['sub_code'].n_unique()} "
        f"stations spread over {out['lsoa21cd'].n_unique():,} Small Areas"
    )
    return out
