"""Irish place names from Logainm, the Placenames Database of Ireland (CC BY 4.0).

The API lists every place in Northern Ireland with its names by language, so each
settlement gets its Irish form beside the English one. The source needs a free key
(``LOGAINM_API_KEY``); without it the stage is skipped and places keep the Irish forms
OpenStreetMap carries. The names are matched to our places at serve time by English
name and distance (``serve.lookups.with_irish_names``).
"""

import json
import os
import re

import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.crs import lonlat_to_bng

logger = setup_logging("stage.logainm")

SLUG = "logainm"


def _main_name(record: dict, language: str) -> str | None:
    names = [n for n in record.get("placenames") or [] if (n.get("language") or "") == language]
    chosen = next((n for n in names if n.get("main")), names[0] if names else None)
    return (chosen or {}).get("wording") or None


def parse_places(records: list[dict]) -> pl.DataFrame:
    """Logainm records → place_id, name_en, name_ga, category, lat, lon, extent.

    The API's fields are camel-cased (``placenames``, ``geography.coordinates``,
    ``northernIreland.extent``); a place can carry several English spellings, and the
    one marked main is taken.
    """
    rows = []
    for r in records:
        coords = (r.get("geography") or {}).get("coordinates") or []
        point = coords[0] if coords else {}
        categories = r.get("categories") or []
        rows.append(
            {
                "place_id": r.get("id"),
                "name_en": _main_name(r, "en"),
                "name_ga": _main_name(r, "ga"),
                "category": (categories[0] or {}).get("nameEN") if categories else None,
                "lat": point.get("latitude"),
                "lon": point.get("longitude"),
                "extent": (r.get("northernIreland") or {}).get("extent"),
            }
        )
    schema = {
        "place_id": pl.Int64,
        "name_en": pl.Utf8,
        "name_ga": pl.Utf8,
        "category": pl.Utf8,
        "lat": pl.Float64,
        "lon": pl.Float64,
        "extent": pl.Utf8,
    }
    return pl.DataFrame(rows, schema=schema)


def plain(expr: pl.Expr) -> pl.Expr:
    """A name as a match key: lower case, letters only."""
    return expr.str.to_lowercase().str.replace_all(r"[^a-z]+", "")


def stage_logainm() -> pl.LazyFrame:
    path = data_dir("raw") / SLUG / f"{SLUG}.json"
    if not path.exists():
        if os.environ.get("LOGAINM_API_KEY", "").strip():
            raise FileNotFoundError(f"{path} not fetched: run lix fetch --slug {SLUG}")
        # Without the key the source is skipped at fetch time: an empty table keeps the
        # stage going, and places take their Irish names from OpenStreetMap alone
        logger.warning(f"No LOGAINM_API_KEY and no {path.name}: no Logainm names this build")
        return parse_places([]).with_columns(
            pl.lit(None, pl.Float64).alias("x"), pl.lit(None, pl.Float64).alias("y"),
            pl.lit(None, pl.Utf8).alias("key"),
        ).lazy()  # fmt: skip
    records = json.loads(path.read_text(encoding="utf-8"))
    df = parse_places(records).filter(
        pl.col("name_ga").is_not_null() & pl.col("lat").is_not_null() & pl.col("lon").is_not_null()
    )
    x, y = lonlat_to_bng(df["lon"], df["lat"])
    df = df.with_columns(x=x, y=y, key=plain(pl.col("name_en").fill_null("")))
    logger.info(
        f"{df.height:,} Logainm places with an Irish name and a location "
        f"({df['name_en'].null_count():,} without an English form)"
    )
    return df.lazy()


def with_irish_names(
    places: pl.DataFrame, logainm: pl.DataFrame, within_m: float = 3000
) -> pl.DataFrame:
    """``places`` with ``name_ga`` filled from Logainm where a place of the same English
    name lies within ``within_m`` (OpenStreetMap's own name:ga stays where set)."""
    if "name_ga" not in places.columns:
        places = places.with_columns(pl.lit(None, pl.Utf8).alias("name_ga"))
    if "x" not in places.columns or logainm.height == 0:
        return places
    keyed = places.with_row_index("_row").with_columns(key=plain(pl.col("name").fill_null("")))
    candidates = keyed.filter(pl.col("name_ga").is_null()).join(
        logainm.select(
            "key", pl.col("name_ga").alias("_ga"), pl.col("x").alias("_x"), pl.col("y").alias("_y")
        ),
        on="key",
        how="inner",
    )
    candidates = (
        candidates.with_columns(
            ((pl.col("x") - pl.col("_x")) ** 2 + (pl.col("y") - pl.col("_y")) ** 2)
            .sqrt()
            .alias("_d")
        )
        .filter(pl.col("_d") <= within_m)
        .sort("_d")
        .unique("_row", keep="first")
        .select("_row", "_ga")
    )
    out = (
        keyed.join(candidates, on="_row", how="left")
        .with_columns(pl.coalesce("name_ga", "_ga").alias("name_ga"))
        .drop("_row", "key", "_ga")
    )
    logger.info(f"Irish names: {candidates.height:,} places named from Logainm")
    return out


_WORD = re.compile(r"\s+")
