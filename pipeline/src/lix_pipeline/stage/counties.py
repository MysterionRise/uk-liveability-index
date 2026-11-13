"""Each small area's county in Great Britain: the wider name people give an area ("Devon"
takes in Plymouth and Torbay, "Kent" takes in Medway), so a county works as a search
scope and as a ranking's "within" (#233). Ireland's counties come from its curated
council list at serve time (``serve/scores.py``).

The counties are OS Boundary-Line's ceremonial ones: England's lieutenancies, the Welsh
preserved counties and the Scottish lieutenancy areas. Each small area takes the county
its population-weighted centre lies in. A county is therefore a set of small areas, not
of councils: the Tees divides Stockton-on-Tees between County Durham and North Yorkshire
(Yarm and Thornaby are in the second), and a Scottish lieutenancy is often a part of a
council (Caithness in Highland). A first version gave each council one county by where
two thirds of its residents live, and put Yarm in County Durham (the v0.7.3 test pass).
"""

import polars as pl

from lix_core.codes import active_nations
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.counties")

LAYER = "boundary_line_ceremonial_counties"
# The layer's names → the names people say. The City of London is a county of its own
# to the Crown, and "Greater London" to everyone asking for homes there; "Durham" and
# "Clackmannan" are also a city and a town, which the bare name should keep meaning
RENAMES = {
    "City and County of the City of London": "Greater London",
    "Durham": "County Durham",
    "Tyne & Wear": "Tyne and Wear",
    "Clackmannan": "Clackmannanshire",
}

SCHEMA = {"lsoa21cd": pl.Utf8, "county": pl.Utf8}


def area_counties(areas: pl.DataFrame, counties) -> pl.DataFrame:
    """The county each small area's centre lies in, under the name people say. ``areas``:
    ``lsoa21cd``, ``x``, ``y`` in the counties' CRS; ``counties``: a GeoDataFrame with
    ``Name``. A centre in no county (just offshore) takes the nearest."""
    import geopandas as gpd

    points = gpd.GeoDataFrame(
        areas.select("lsoa21cd").to_pandas(),
        geometry=gpd.points_from_xy(areas["x"].to_numpy(), areas["y"].to_numpy()),
        crs=counties.crs,
    )
    joined = gpd.sjoin_nearest(points, counties[["Name", "geometry"]], how="left")
    # A centre on a shared border is nearest to both: one county each
    joined = joined[~joined.index.duplicated(keep="first")]
    return (
        pl.from_pandas(joined[["lsoa21cd", "Name"]])
        .select("lsoa21cd", pl.col("Name").replace(RENAMES).alias("county"))
        .sort("lsoa21cd")
    )


def stage_area_counties() -> pl.LazyFrame:
    """Small area → ceremonial county for the active nations of Great Britain."""
    import pyogrio

    nations = [n for n in active_nations() if n in ("E", "W", "S")]
    if not nations:
        return pl.DataFrame(schema=SCHEMA).lazy()
    geo = pl.read_parquet(data_dir("staged") / "geo_lsoa.parquet").filter(
        pl.col("lsoa21cd").str.slice(0, 1).is_in(nations)
    )
    areas = geo.select(
        "lsoa21cd", pl.col("pwc_x").alias("x"), pl.col("pwc_y").alias("y")
    ).drop_nulls()
    counties = pyogrio.read_dataframe(
        data_dir("raw") / "os_boundary_line" / "Data" / "bdline_gb.gpkg", layer=LAYER
    )
    out = area_counties(areas, counties)
    split = (
        out.join(geo.select("lsoa21cd", "lad_nm"), on="lsoa21cd")
        .group_by("lad_nm")
        .agg(pl.col("county").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
    )
    logger.info(
        f"{out.height:,} small areas in {out['county'].n_unique()} counties; "
        f"{split.height} councils lie in more than one"
    )
    return out.lazy()
