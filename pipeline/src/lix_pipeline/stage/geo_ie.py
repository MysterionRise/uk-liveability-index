"""Ireland's geography backbone: Census 2022 Small Areas → Electoral Divisions → councils.

``build()`` is called by ``stage_geo_lsoa`` with ``("I",)`` and returns the same columns
as the other builders. Sources (config/nations.yaml ``geo:``): Tailte Éireann's Small
Area and Electoral Division boundaries (which carry the division's and county's names in
English and Irish), its local-authority boundaries, the CSO's Small Area Population
Statistics (population and the six-class urban/rural classification) and the curated
``ie_local_authorities`` list, which folds the census file's 34 pre-2014 county codes into
the 31 councils and names them in both languages.

The publisher's Small Area ids carry "/" for areas split or merged since 2016 (2,081 of
18,919: "017010012/01", "017010029/017010046"), so they cannot be codes: every tier's codes
are minted by rank (``lix_core.codes.minted_codes``: ``I01000001``…, ``I02``… for the
divisions, ``I06``… for the councils from the curated list) and the id is kept as
``source_code``. Small Areas have no names of their own: an area is named after its
division and the tail of its id ("Carlow Rural 016").

There is no open postcode file (Eircode is licensed), so the population-weighted centroid
of a Small Area is the mean of its home points (Tailte Éireann building footprints, see
``stage_homes``); an area without any takes its polygon's representative point.
"""

import re

import geopandas as gpd
import numpy as np
import polars as pl
import pyogrio

from lix_core.codes import minted_codes
from lix_core.geo import to_working, working_crs, working_to_lonlat
from lix_core.log import setup_logging
from lix_core.paths import data_dir, raw_file
from lix_pipeline.geo.boundaries import bounds_table, tier_polygons
from lix_pipeline.stage.geo_ew import nation_columns, region_columns, ruc_class_expr

logger = setup_logging("stage.geo_ie")

Nations = tuple[str, ...]
NATION = "I"
SA_COLUMNS = [
    "SA_PUB2022", "SA_GUID_2022", "ED_ID_STR", "ED_ENGLISH", "ED_GAEILGE", "COUNTY_CODE",
    "COUNTY_ENGLISH", "CSO_LEA", "SA_URBAN_AREA_NAME",
]  # fmt: skip
# Building footprints kept as homes (m²): below, sheds and kiosks; above, warehouses,
# schools and blocks whose one centroid would stand for hundreds of homes elsewhere
HOME_FOOTPRINT_M2 = (20.0, 1000.0)
BUILDINGS_CHUNK = 500_000

# Words kept lower case inside a title-cased name
ENGLISH_PARTICLES = {"and", "of", "the", "on", "in", "at", "by"}
IRISH_PARTICLES = {"na", "an", "agus", "de", "ag", "ar", "as", "i", "le", "ó", "um", "go"}
# Eclipsis: the prefix stays lower and the eclipsed letter takes the capital (nGall,
# mBaile, bhFear, tSráid); no Irish word starts with these pairs unmutated
IRISH_ECLIPSIS = (
    ("bh", "f"),
    ("n", "g"),
    ("m", "b"),
    ("g", "c"),
    ("d", "t"),
    ("n", "d"),
    ("b", "p"),
    ("t", "s"),
)
IRISH_VOWELS = "aeiouáéíóú"
_WORD_BREAK = re.compile(r"([\s\-/()]+)")


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:]


def english_title(name: str | None) -> str | None:
    """ "CARLOW RURAL" → "Carlow Rural", "O'BRIEN'S BRIDGE" → "O'Brien's Bridge"."""
    if name is None:
        return None
    parts = _WORD_BREAK.split(name.strip().lower())
    out = []
    first = True
    for part in parts:
        if not part or _WORD_BREAK.fullmatch(part):
            out.append(part)
            continue
        if part in ENGLISH_PARTICLES and not first:
            out.append(part)
        else:
            # "o'brien's": the segment after an apostrophe is capitalised only when the
            # segment before it is a single letter (O', D')
            pieces = part.split("'")
            pieces = [
                _cap(p) if i == 0 or len(pieces[i - 1]) == 1 else p for i, p in enumerate(pieces)
            ]
            out.append("'".join(pieces))
        first = False
    return "".join(out)


def irish_title(name: str | None) -> str | None:
    """ "CEATHARLACH (TUATH)" → "Ceatharlach (Tuath)", "DÚN NA NGALL" → "Dún na nGall",
    "NA HÁIRDE" → "Na hÁirde": title case that respects Irish particles and mutations."""
    if name is None:
        return None
    parts = _WORD_BREAK.split(name.strip().lower())
    out = []
    first = True
    for part in parts:
        if not part or _WORD_BREAK.fullmatch(part):
            out.append(part)
            continue
        if part in IRISH_PARTICLES and not first:
            out.append(part)
        elif eclipsed := next(
            (
                (p, e)
                for p, e in IRISH_ECLIPSIS
                if part.startswith(p + e) and len(part) > len(p) + 1
            ),
            None,
        ):
            prefix, letter = eclipsed
            out.append(prefix + letter.upper() + part[len(prefix) + 1 :])
        elif part[0] == "h" and len(part) > 1 and part[1] in IRISH_VOWELS:
            out.append("h" + _cap(part[1:]))
        else:
            out.append(_cap(part))
        first = False
    return "".join(out)


def area_suffix(source_code: str) -> str:
    """The tail of a Small Area id after its division's six digits, shortened for split
    and merged areas: "017010016" → "016", "017010012/01" → "012/01",
    "017010029/017010046" → "029/046"."""
    parts = source_code.split("/")
    return "/".join([parts[0][6:]] + [p[6:] if len(p) == 9 else p for p in parts[1:]])


def read_councils() -> pl.DataFrame:
    return pl.read_csv(raw_file("ie_local_authorities", "csv"), infer_schema_length=0)


def read_small_areas() -> pl.DataFrame:
    """The Small Area attributes (no geometry) from the boundary file."""
    df = pyogrio.read_dataframe(
        raw_file("cso_sa22_boundaries", "gpkg"), columns=SA_COLUMNS, read_geometry=False
    )
    return pl.from_pandas(df[SA_COLUMNS]).with_columns(pl.all().cast(pl.Utf8).str.strip_chars())


def read_urban_classes() -> pl.DataFrame:
    """Each Small Area's CSO urban/rural class from the census file (its ids match the
    boundary file's)."""
    path = raw_file("cso_saps22_sa", "csv")
    return (
        pl.scan_csv(path, infer_schema_length=0, encoding="utf8-lossy")
        .select("GEOGID", "UR_Category", "UR_Category_Desc")
        .filter(pl.col("UR_Category").is_not_null())  # the State total row
        .collect()
        .with_columns(pl.col("UR_Category_Desc").str.replace(r"^\d+\.\s*", ""))
    )


def tidy_small_areas(sa: pl.DataFrame, councils: pl.DataFrame) -> pl.DataFrame:
    """The boundary file's attributes → codes, names and the hierarchy of every area."""
    low = minted_codes(NATION, "low", sa["SA_PUB2022"].to_list())
    mid = minted_codes(NATION, "mid", sa["ED_ID_STR"].to_list())
    council = councils.select(
        pl.col("county_code").alias("COUNTY_CODE"), "lad_cd", "lad_nm", "lad_nm_ga"
    )
    out = (
        sa.with_columns(
            pl.col("SA_PUB2022").replace_strict(low).alias("lsoa21cd"),
            pl.col("SA_PUB2022").alias("source_code"),
            pl.col("ED_ID_STR").replace_strict(mid).alias("msoa21cd"),
            pl.col("ED_ENGLISH")
            .map_elements(english_title, return_dtype=pl.Utf8)
            .alias("msoa_name"),
            pl.col("ED_GAEILGE")
            .map_elements(irish_title, return_dtype=pl.Utf8)
            .alias("msoa_name_ga"),
            pl.col("SA_PUB2022").map_elements(area_suffix, return_dtype=pl.Utf8).alias("_suffix"),
        )
        .join(council, on="COUNTY_CODE", how="left")
        .with_columns(
            pl.concat_str([pl.col("msoa_name"), pl.col("_suffix")], separator=" ").alias(
                "lsoa21nm"
            ),
            pl.when(pl.col("msoa_name_ga").is_not_null())
            .then(pl.concat_str([pl.col("msoa_name_ga"), pl.col("_suffix")], separator=" "))
            .otherwise(None)
            .alias("lsoa21nm_ga"),
            pl.col("msoa_name").alias("msoa21nm"),
            pl.col("lad_cd").alias("lad22cd"),
            pl.lit(None, pl.Utf8).alias("rgn_cd"),
            pl.lit(None, pl.Utf8).alias("rgn_nm"),
            pl.lit(None, pl.Utf8).alias("pfa_cd"),
            pl.lit(None, pl.Utf8).alias("pfa_nm"),
            pl.col("CSO_LEA").map_elements(english_title, return_dtype=pl.Utf8).alias("lea_nm"),
            pl.col("SA_GUID_2022").alias("source_guid"),
        )
        .drop("_suffix")
    )
    missing = out.filter(pl.col("lad_cd").is_null())
    if missing.height:
        raise ValueError(
            f"{missing.height} Small Areas have a county code the curated council list lacks: "
            f"{missing['COUNTY_CODE'].unique().to_list()}"
        )
    return out


def home_centroids(homes: pl.DataFrame) -> pl.DataFrame:
    """The mean of each area's home points: ``pwc_x``/``pwc_y`` and their lon/lat."""
    means = homes.group_by("lsoa21cd").agg(
        pl.col("east1m").mean().alias("pwc_x"), pl.col("north1m").mean().alias("pwc_y")
    )
    lon, lat = working_to_lonlat(means["pwc_x"].to_numpy(), means["pwc_y"].to_numpy())
    return means.with_columns(pwc_lon=lon, pwc_lat=lat)


def representative_points(polygons: gpd.GeoDataFrame) -> pl.DataFrame:
    """A point inside each polygon, for areas without home points."""
    pts = polygons.geometry.representative_point()
    x, y = pts.x.to_numpy(), pts.y.to_numpy()
    lon, lat = working_to_lonlat(x, y)
    return pl.DataFrame(
        {
            "lsoa21cd": polygons.index.to_list(),
            "pwc_x": x,
            "pwc_y": y,
            "pwc_lon": lon,
            "pwc_lat": lat,
        }
    )


def build(nations: Nations) -> pl.DataFrame:
    """One row per Census 2022 Small Area (``nations`` must be ``("I",)``)."""
    if tuple(nations) != (NATION,):
        raise ValueError(f"geo_ie builds Ireland, not {list(nations)}")
    sa = tidy_small_areas(read_small_areas(), read_councils())
    low = minted_codes(NATION, "low", sa["source_code"].to_list())

    ruc = read_urban_classes()
    unknown = set(ruc["GEOGID"]) ^ set(low)
    if unknown:
        raise ValueError(
            f"The census file and the boundary file disagree on {len(unknown)} Small Area ids, "
            f"e.g. {sorted(unknown)[:3]}"
        )
    ruc = ruc.select(
        pl.col("GEOGID").replace_strict(low).alias("lsoa21cd"),
        pl.col("UR_Category").alias("ruc21cd"),
        pl.col("UR_Category_Desc").alias("ruc21nm"),
    )
    pop = pl.read_parquet(data_dir("staged") / "population.parquet").select(
        "lsoa21cd", "population", "pop_year"
    )
    polygons = tier_polygons("low", nations=nations)
    homes = pl.read_parquet(data_dir("staged") / "homes.parquet").filter(pl.col("nation") == NATION)
    centroids = home_centroids(homes)
    without = polygons[~polygons.index.isin(centroids["lsoa21cd"].to_list())]
    if len(without):
        logger.warning(
            f"{len(without)} Small Areas have no home points: their centroid is a point "
            "inside the polygon"
        )
        centroids = pl.concat([centroids, representative_points(without)])

    df = (
        sa.join(ruc, on="lsoa21cd", how="left")
        .join(centroids, on="lsoa21cd", how="left")
        .join(pop, on="lsoa21cd", how="left")
        .join(bounds_table(polygons), on="lsoa21cd", how="left")
        .with_columns(nation_columns(nations))
        .with_columns(region_columns(nations))
        .with_columns(
            ruc_class_expr(nations),
            (pl.col("population") / pl.col("area_km2")).alias("density_per_km2"),
            pl.lit(0, pl.Int32).alias("n_postcodes"),
            pl.lit("sa22").alias("area_type"),
        )
        .with_columns((pl.col("ruc_class") == "urban").alias("urban"))
        .drop(SA_COLUMNS)
        .sort("lsoa21cd")
    )
    logger.info(
        f"geo_ie: {df.height:,} Small Areas in {df['msoa21cd'].n_unique():,} divisions and "
        f"{df['lad_cd'].n_unique()} councils"
    )
    return df


def upper_polygons() -> gpd.GeoDataFrame:
    """The 31 councils' polygons (``lad_cd``, ``lad_nm``, geometry) in the working CRS,
    from Tailte Éireann's administrative areas named through the curated list."""
    gdf = gpd.read_file(raw_file("te_admin_areas", "gpkg"))
    if gdf.crs is None or gdf.crs.to_epsg() != working_crs():
        gdf = gdf.set_crs(working_crs()) if gdf.crs is None else gdf.to_crs(working_crs())
    councils = read_councils().unique("lad_cd").to_pandas()
    gdf = gdf.merge(
        councils[["te_english", "lad_cd", "lad_nm"]],
        left_on="ENGLISH",
        right_on="te_english",
        how="left",
    )
    unnamed = gdf[gdf["lad_cd"].isna()]
    if len(unnamed):
        raise ValueError(f"Councils missing from the curated list: {unnamed['ENGLISH'].tolist()}")
    out = gdf[["lad_cd", "lad_nm", "geometry"]].dissolve(by="lad_cd", as_index=False)
    return gpd.GeoDataFrame(out, crs=working_crs())


def home_points() -> pl.DataFrame:
    """Home points from Tailte Éireann's building footprints: the centroid of every
    footprint between ``HOME_FOOTPRINT_M2`` in the working CRS, placed in its Small Area;
    columns like the postcode homes (``home_id`` is the building's GUID). A Small Area
    with no footprint in that range but larger ones (apartment blocks, which can house a
    thousand people) takes those instead, so no populated area is left without homes."""
    from lix_pipeline.geo.joins import points_to_lsoa

    path = raw_file("te_buildings", "gpkg")
    info = pyogrio.read_info(path)
    total, crs = int(info["features"]), int(info["crs"].split(":")[-1])
    lo, hi = HOME_FOOTPRINT_M2
    frames: dict[str, list[pl.DataFrame]] = {"home": [], "large": []}
    kept = 0
    for start in range(0, total, BUILDINGS_CHUNK):
        gdf = pyogrio.read_dataframe(
            path, columns=["GUID"], skip_features=start, max_features=BUILDINGS_CHUNK
        )
        area = gdf.geometry.area.to_numpy()
        for kind, keep in (("home", (area >= lo) & (area <= hi)), ("large", area > hi)):
            centroids = gdf.geometry[keep].centroid
            x, y = to_working(centroids.x.to_numpy(), centroids.y.to_numpy(), crs)
            frames[kind].append(
                pl.DataFrame(
                    {
                        "home_id": gdf["GUID"][keep].astype(str).to_list(),
                        "east1m": np.round(x).astype(np.int32),
                        "north1m": np.round(y).astype(np.int32),
                    }
                )
            )
        kept += int(((area >= lo) & (area <= hi)).sum())
        logger.info(
            f"te_buildings: {min(start + BUILDINGS_CHUNK, total):,}/{total:,} read, {kept:,} kept"
        )
    polygons = tier_polygons("low", nations=(NATION,)).reset_index()[["lsoa21cd", "geometry"]]
    points = points_to_lsoa(pl.concat(frames["home"]), x="east1m", y="north1m", polygons=polygons)
    placed = points.filter(pl.col("lsoa21cd").is_not_null())
    outside = points.height - placed.height
    placed = placed.with_columns(pl.lit("building").alias("kind"))
    large = points_to_lsoa(pl.concat(frames["large"]), x="east1m", y="north1m", polygons=polygons)
    filled = large.filter(
        pl.col("lsoa21cd").is_not_null() & ~pl.col("lsoa21cd").is_in(placed["lsoa21cd"].unique())
    ).with_columns(pl.lit("building").alias("kind"))
    if filled.height:
        logger.info(
            f"Ireland: {filled['lsoa21cd'].n_unique():,} Small Areas with no footprint under "
            f"{hi:.0f} m² take their {filled.height:,} larger buildings (apartment blocks)"
        )
        placed = pl.concat([placed, filled])
    # A Small Area with no building centroid inside it at all (a sliver whose buildings'
    # centroids fall over its edge) takes one home at a point inside its polygon, so no
    # populated area is left without homes (#142)
    empty = polygons[~polygons["lsoa21cd"].isin(placed["lsoa21cd"].unique().to_list())]
    if len(empty):
        rep = empty.geometry.representative_point()
        fallback = pl.DataFrame(
            {
                "home_id": [f"area:{c}" for c in empty["lsoa21cd"]],
                "east1m": np.round(rep.x.to_numpy()).astype(np.int32),
                "north1m": np.round(rep.y.to_numpy()).astype(np.int32),
                "lsoa21cd": empty["lsoa21cd"].to_list(),
                "kind": ["area"] * len(empty),
            }
        )
        logger.warning(
            f"Ireland: {len(empty)} Small Areas have no building centroid inside them and take "
            f"one home at a point inside the polygon: {empty['lsoa21cd'].tolist()[:10]}"
        )
        placed = pl.concat([placed, fallback.select(placed.columns)], how="vertical_relaxed")
    lon, lat = working_to_lonlat(placed["east1m"].to_numpy(), placed["north1m"].to_numpy())
    out = placed.with_columns(
        pl.Series("lat", lat), pl.Series("long", lon), pl.lit(NATION).alias("nation")
    )
    logger.info(
        f"Ireland: {out.height:,} home points from {total:,} buildings "
        f"({outside:,} footprints outside every Small Area; footprints {lo:.0f}–{hi:.0f} m²)"
    )
    return out
