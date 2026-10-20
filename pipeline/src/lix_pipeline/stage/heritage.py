"""Listed buildings as points, one list per nation: Historic England's, Cadw's, Historic
Environment Scotland's and the Department for Communities' statutory lists, and in
Ireland the National Inventory of Architectural Heritage at regional rating or above
(the councils' statutory records have no national open file)."""

import polars as pl

from lix_core.codes import active_nations, in_scope
from lix_core.geo import working_crs, working_to_lonlat
from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.heritage")

# Inventory ratings the Minister recommends for the councils' Records of Protected Structures
NIAH_KEPT = ("International", "National", "Regional")


def _parquet(slug: str) -> pl.DataFrame:
    return pl.read_parquet(data_dir("raw") / slug / f"{slug}.parquet")


def _nhle() -> pl.DataFrame:
    """Historic England's list: the grid reference travels as fields."""
    return _parquet("nhle_listed").select(
        pl.col("ListEntry").cast(pl.Utf8).alias("ref"),
        pl.col("Name").alias("name"),
        pl.col("Grade").cast(pl.Utf8).alias("grade"),
        pl.col("Easting").cast(pl.Float64).alias("x"),
        pl.col("Northing").cast(pl.Float64).alias("y"),
        pl.lit("Historic England").alias("source"),
    )


def _cadw() -> pl.DataFrame:
    """Cadw's list from the WFS pages (multipoints: one representative point each)."""
    import geopandas as gpd

    frames = []
    for path in sorted((data_dir("raw") / "cadw_listed").glob("*.gpkg")):
        gdf = gpd.read_file(path, columns=["RecordNumber", "Name", "Grade"]).to_crs(working_crs())
        point = gdf.geometry.representative_point()
        frames.append(
            pl.DataFrame(
                {
                    "ref": gdf["RecordNumber"].astype(str).to_numpy(),
                    "name": gdf["Name"].to_numpy(),
                    "grade": gdf["Grade"].astype(str).to_numpy(),
                    "x": point.x.to_numpy(),
                    "y": point.y.to_numpy(),
                }
            )
        )
    if not frames:
        raise FileNotFoundError(f"No GeoPackage under {data_dir('raw') / 'cadw_listed'}")
    return pl.concat(frames).with_columns(pl.lit("Cadw").alias("source"))


def _hes() -> pl.DataFrame:
    return _parquet("hes_listed").select(
        pl.col("ENT_REF").cast(pl.Utf8).alias("ref"),
        pl.col("ENT_TITLE").alias("name"),
        pl.col("CATEGORY").cast(pl.Utf8).alias("grade"),
        pl.col("x").cast(pl.Float64),
        pl.col("y").cast(pl.Float64),
        pl.lit("Historic Environment Scotland").alias("source"),
    )


# The Department for Communities' statutory grades; the file also carries delisted
# buildings ("Delist"), records never allocated a grade, "Record Only" entries and blanks
DFC_GRADES = ("A", "B+", "B1", "B2", "B")


def _dfc() -> pl.DataFrame:
    return (
        _parquet("dfc_listed")
        .select(
            pl.col("HB_ref").cast(pl.Utf8).alias("ref"),
            pl.col("Address").alias("name"),
            pl.col("CurrentGra").cast(pl.Utf8).str.strip_chars().alias("grade"),
            pl.col("x").cast(pl.Float64),
            pl.col("y").cast(pl.Float64),
            pl.lit("Department for Communities").alias("source"),
        )
        .filter(pl.col("grade").is_in(DFC_GRADES) & pl.col("ref").is_not_null())
        # Several points per building in the file: one listed building is one point
        .unique("ref", keep="first", maintain_order=True)
    )


def tidy_niah(raw: pl.DataFrame) -> pl.DataFrame:
    """The inventory's buildings at the kept ratings, in the heritage shape."""
    return raw.filter(pl.col("RATING").is_in(NIAH_KEPT)).select(
        pl.col("REG_NO").cast(pl.Utf8).alias("ref"),
        pl.col("NAME").alias("name"),
        pl.col("RATING").cast(pl.Utf8).alias("grade"),
        pl.col("x").cast(pl.Float64),
        pl.col("y").cast(pl.Float64),
        pl.lit("National Inventory of Architectural Heritage").alias("source"),
    )


READERS = {
    "E": _nhle,
    "W": _cadw,
    "S": _hes,
    "N": _dfc,
    "I": lambda: tidy_niah(_parquet("niah_buildings")),
}


def stage_heritage() -> pl.LazyFrame:
    """Listed buildings across the active nations, located to a small area."""
    from lix_pipeline.geo.joins import points_to_lsoa

    frames = []
    for nation in active_nations():
        df = READERS[nation]()
        if df.height == 0:
            logger.warning(f"{nation}: no listed buildings in the fetched list")
            continue
        logger.info(f"{nation}: {df.height:,} listed buildings ({df['source'][0]})")
        frames.append(df)
    if not frames:
        raise FileNotFoundError("No listed buildings for any active nation")
    df = pl.concat(frames)
    df = points_to_lsoa(df.filter(pl.col("x").is_not_null() & pl.col("y").is_not_null()))
    df = df.filter(in_scope("lsoa21cd"))
    lon, lat = working_to_lonlat(df["x"].to_numpy(), df["y"].to_numpy())
    df = df.with_columns(pl.Series("lon", lon), pl.Series("lat", lat))
    logger.info(f"{df.height:,} listed buildings located in {list(active_nations())}")
    return df.lazy()
