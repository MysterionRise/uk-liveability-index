"""Each council's county in Great Britain: the wider name people give an area ("Devon"
takes in Plymouth and Torbay, "Kent" takes in Medway), so a county works as a search
scope and as a ranking's "within" (#233). Ireland's counties come from its curated
council list at serve time (``serve/lookups.py``).

The counties are OS Boundary-Line's ceremonial ones: England's lieutenancies, the Welsh
preserved counties and the Scottish lieutenancy areas. Each small area is placed by its
population-weighted centre, and a council takes the county where at least two thirds of
its residents live. Every English and Welsh council passes but one that the Tees
divides (Stockton-on-Tees, 70% in County Durham); the Scottish councils that fail are
the ones a lieutenancy names only a part of (Highland is not "Inverness").
"""

import polars as pl

from lix_core.codes import active_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.counties")

LAYER = "boundary_line_ceremonial_counties"
MIN_SHARE = 2 / 3
# The layer's names → the names people say. The City of London is a county of its own
# to the Crown, and "Greater London" to everyone asking for homes there; "Durham" and
# "Clackmannan" are also a city and a town, which the bare name should keep meaning
RENAMES = {
    "City and County of the City of London": "Greater London",
    "Durham": "County Durham",
    "Tyne & Wear": "Tyne and Wear",
    "Clackmannan": "Clackmannanshire",
}
# Where the ceremonial county is everyday geography even when a council has taken its
# name: Somerset is still Bath and Weston-super-Mare to the people who live there. In
# Wales and Scotland a county that shares a council's name means that council (nobody
# on Anglesey lives in Gwynedd), so the county is kept for that council alone
EVERYDAY_OVER_A_COUNCIL = ("E",)

SCHEMA = {"lad_cd": pl.Utf8, "county": pl.Utf8, "share": pl.Float64}


def county_shares(areas: pl.DataFrame, counties) -> pl.DataFrame:
    """Each council's share of residents by county: ``areas`` has one row per small area
    (``lad_cd``, ``x``, ``y`` in the counties' CRS, ``population``); ``counties`` is a
    GeoDataFrame with ``Name``. An area whose centre is in no county (a centre just
    offshore) takes the nearest."""
    import geopandas as gpd

    points = gpd.GeoDataFrame(
        areas.select("lad_cd", "population").to_pandas(),
        geometry=gpd.points_from_xy(areas["x"].to_numpy(), areas["y"].to_numpy()),
        crs=counties.crs,
    )
    joined = gpd.sjoin_nearest(points, counties[["Name", "geometry"]], how="left")
    # A centre on a shared border is nearest to both: one county each
    joined = joined[~joined.index.duplicated(keep="first")]
    placed = pl.from_pandas(joined[["lad_cd", "population", "Name"]])
    return (
        placed.group_by("lad_cd", "Name")
        .agg(pl.col("population").sum())
        .with_columns(
            (pl.col("population") / pl.col("population").sum().over("lad_cd")).alias("share")
        )
        .select("lad_cd", pl.col("Name").alias("county"), "share")
    )


def council_counties(shares: pl.DataFrame, council_names: pl.DataFrame) -> pl.DataFrame:
    """One county per council from its shares (see the module's rules), under the names
    people say. ``council_names``: ``lad_cd``, ``lad_nm``."""
    main = (
        shares.sort("lad_cd", "share", descending=[False, True])
        .group_by("lad_cd", maintain_order=True)
        .first()
        .filter(pl.col("share") >= MIN_SHARE)
        .with_columns(pl.col("county").replace(RENAMES))
        .join(council_names.unique("lad_cd"), on="lad_cd", how="left")
    )
    # Outside England a county named like a council is that council
    namesakes = main.filter(
        ~pl.col("lad_cd").str.slice(0, 1).is_in(EVERYDAY_OVER_A_COUNCIL)
        & (pl.col("county") == pl.col("lad_nm"))
    )["county"].to_list()
    main = main.filter(
        pl.col("lad_cd").str.slice(0, 1).is_in(EVERYDAY_OVER_A_COUNCIL)
        | ~pl.col("county").is_in(namesakes)
        | (pl.col("county") == pl.col("lad_nm"))
    )
    return main.select(*SCHEMA).sort("lad_cd")


def stage_council_counties() -> pl.LazyFrame:
    """Council code → ceremonial county for the active nations of Great Britain."""
    import pyogrio

    nations = [n for n in active_nations() if n in ("E", "W", "S")]
    if not nations:
        return pl.DataFrame(schema=SCHEMA).lazy()
    geo = pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet").filter(
        pl.col("lad_cd").str.slice(0, 1).is_in(nations)
    )
    areas = geo.select(
        "lad_cd", pl.col("pwc_x").alias("x"), pl.col("pwc_y").alias("y"), "population"
    ).drop_nulls()
    counties = pyogrio.read_dataframe(
        data_dir("raw") / "os_boundary_line" / "Data" / "bdline_gb.gpkg", layer=LAYER
    )
    out = council_counties(county_shares(areas, counties), geo.select("lad_cd", "lad_nm"))
    councils = geo["lad_cd"].n_unique()
    logger.info(
        f"{out.height} of {councils} councils in {out['county'].n_unique()} counties; "
        f"{councils - out.height} are divided between counties and have none"
    )
    return out.lazy()
