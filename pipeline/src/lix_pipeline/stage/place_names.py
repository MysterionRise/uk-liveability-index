"""The largest cities' and towns' names in Welsh, Gaelic and Irish (curated, Wikidata)."""

import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("stage.place_names")


def with_curated_names(places: pl.DataFrame, curated: pl.DataFrame | None = None) -> pl.DataFrame:
    """Fill the Welsh, Gaelic and Irish names of the largest cities and towns from the
    curated list (``place_names_i18n``, Wikidata) where the place has none: a city or
    town is matched by its English name (or its alternative name) and council. OS Open
    Names carries the Welsh forms of Welsh places and the Gaelic forms of some Scottish
    ones; this list adds the exonyms a search in those languages expects ("Llundain",
    "Dùn Èideann")."""
    if curated is None:
        files = sorted((data_dir("raw") / "place_names_i18n").glob("*.csv"))
        if not files:
            return places
        curated = pl.read_csv(files[0], infer_schema_length=0)
    languages = [c for c in ("name_cy", "name_gd", "name_ga") if c in curated.columns]
    for column in languages:
        if column not in places.columns:
            places = places.with_columns(pl.lit(None, pl.Utf8).alias(column))
    keyed = curated.select(
        "name", "local_authority", *[pl.col(c).alias(f"curated_{c}") for c in languages]
    ).with_columns(pl.col(f"curated_{c}").replace("", None) for c in languages)
    big = pl.col("place_type").is_in(["City", "Town"])
    out = places.join(keyed, on=["name", "local_authority"], how="left")
    if "name_alt" in places.columns:
        # A place whose alternative name is the curated one ("Londonderry" for Derry)
        by_alt = keyed.rename(
            {"name": "name_alt", **{f"curated_{c}": f"alt_{c}" for c in languages}}
        )
        out = (
            out.join(by_alt, on=["name_alt", "local_authority"], how="left")
            .with_columns(
                pl.coalesce(pl.col(f"curated_{c}"), pl.col(f"alt_{c}")).alias(f"curated_{c}")
                for c in languages
            )
            .drop([f"alt_{c}" for c in languages])
        )
    out = out.with_columns(
        pl.when(big & pl.col(c).is_null())
        .then(pl.col(f"curated_{c}"))
        .otherwise(pl.col(c))
        .alias(c)
        for c in languages
    )
    filled = sum(
        out.filter(big & pl.col(f"curated_{c}").is_not_null())
        .filter(pl.col(c) == pl.col(f"curated_{c}"))
        .height
        for c in languages
    )
    logger.info(f"Curated names: {filled} city and town names filled from the Wikidata list")
    return out.drop([f"curated_{c}" for c in languages])
