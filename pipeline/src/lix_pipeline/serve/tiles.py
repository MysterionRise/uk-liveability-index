"""Vector map tiles (PMTiles) for the browser map, written with GDAL's PMTiles driver.

    data/serve/tiles/lsoa.pmtiles   small areas of the active nations (LSOAs, Data Zones),
                                    zoom 8–14 (lsoa21cd)
    data/serve/tiles/msoa.pmtiles   mid areas (MSOAs, Intermediate Zones), zoom 6–12 (msoa21cd)
    data/serve/tiles/lad.pmtiles    local authorities and councils, zoom 4–10 (lad_cd, name)

Polygons carry only their code (and a name for labels): the browser colours them from
scores.parquet via feature-state, so reweighting never needs new tiles. PMTiles is a
single static file read with HTTP range requests; no tile server is needed.
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import polars as pl
import pyogrio

from lix_core.codes import active_nations
from lix_core.geo import working_crs
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.geo.boundaries import tier_polygons, upper_polygons

logger = setup_logging("serve.tiles")


def _tier(level: str, code: str) -> gpd.GeoDataFrame:
    """One tier's polygons across the active nations (each nation's own file, simplified
    where the nation asks for it), with the code column named ``code``."""
    gdf = tier_polygons(level, simplify=True).reset_index().rename(columns={"lsoa21cd": code})
    return gdf[[code, "geometry"]]


def tier_names(features_path: Path | None = None) -> dict[str, dict[str, str]]:
    """Code → name for the low and mid tiers from the scored features (the hover names an
    area at every zoom); empty when the features are not built yet."""
    path = features_path or data_dir("serve") / "lsoa_features.parquet"
    if not path.exists():
        logger.warning(f"{path} not found: tiles carry codes only")
        return {"low": {}, "mid": {}}
    f = pl.read_parquet(path, columns=["lsoa21cd", "lsoa21nm", "msoa21cd", "msoa_name"])
    low = dict(zip(f["lsoa21cd"].to_list(), f["lsoa21nm"].to_list(), strict=True))
    mid = dict(
        f.filter(pl.col("msoa_name").is_not_null())
        .unique("msoa21cd", keep="first")
        .select("msoa21cd", "msoa_name")
        .iter_rows()
    )
    return {"low": {k: v for k, v in low.items() if v}, "mid": mid}


def write_layer(gdf: gpd.GeoDataFrame, out: Path, layer: str, minzoom: int, maxzoom: int) -> Path:
    if out.exists():
        out.unlink()
    pyogrio.write_dataframe(
        gdf.to_crs(4326),
        out,
        driver="PMTiles",
        layer=layer,
        dataset_options={
            "NAME": layer,
            "MINZOOM": str(minzoom),
            "MAXZOOM": str(maxzoom),
            # Simplify more at low zooms; keep shapes faithful when zoomed in
            "SIMPLIFICATION": "1",
            "SIMPLIFICATION_MAX_ZOOM": "2",
        },
    )
    logger.info(f"{out.name}: {len(gdf):,} features, {out.stat().st_size / 1e6:.1f} MB")
    return out


def build_tiles(
    out_dir: Path | None = None,
    lsoas: set[str] | None = None,
    lads: set[str] | None = None,
) -> list[Path]:
    """Write the three tilesets; ``lsoas``/``lads`` restrict them (for the demo dataset)."""
    out_dir = out_dir or data_dir("serve") / "tiles"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []

    names = tier_names()
    lsoa = _tier("low", "lsoa21cd")
    if lsoas is not None:
        lsoa = lsoa[lsoa["lsoa21cd"].isin(lsoas)]
    lsoa["name"] = lsoa["lsoa21cd"].map(names["low"])
    outputs.append(write_layer(lsoa, out_dir / "lsoa.pmtiles", "lsoa", 8, 14))

    msoa = _tier("mid", "msoa21cd")
    msoa["name"] = msoa["msoa21cd"].map(names["mid"])
    if lsoas is not None:
        # MSOAs nest in local authorities, so keep those overlapping the chosen LSOAs
        msoa = msoa[msoa.intersects(lsoa.to_crs(msoa.crs).union_all().buffer(-1))]
    outputs.append(write_layer(msoa, out_dir / "msoa.pmtiles", "msoa", 6, 12))

    # Each nation's upper tier from its own file (the UK's councils share one)
    frames = [upper_polygons(n) for n in active_nations()]
    lad = gpd.GeoDataFrame(pd.concat(frames), crs=working_crs()).rename(columns={"lad_nm": "name"})
    lad = lad[["lad_cd", "name", "geometry"]]
    if lads is not None:
        lad = lad[lad["lad_cd"].isin(lads)]
    outputs.append(write_layer(lad, out_dir / "lad.pmtiles", "lad", 4, 10))
    return outputs
