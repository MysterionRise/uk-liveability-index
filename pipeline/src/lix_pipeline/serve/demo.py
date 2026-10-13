"""A small demo dataset cut from a full build, for CI end-to-end tests and quick starts.

    lix demo-data --lad E08000035 E06000043 --out fixtures/demo

writes ``<out>/serve/`` with the same files as ``data/serve/`` (so ``LIX_DATA_DIR=<out>``
works for the API and the web app) restricted to the chosen local authorities. Scores
and percentiles are England-wide, copied from the full build; only the browser's
on-the-fly percentiles are relative to the subset, so the manifest says ``demo: true``.
"""

import json
import shutil
from pathlib import Path

import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.serve.scores import _sha256
from lix_pipeline.serve.tiles import build_tiles

logger = setup_logging("serve.demo")

# Places within this distance (BNG metres) of the subset are kept, so nearest-place
# searches near its edge still find the closest pub or GP across the boundary
POI_BUFFER_M = 2_000


def build_demo(lads: list[str], out: Path, source: Path | None = None) -> dict[str, Path]:
    source = source or data_dir("serve")
    serve = out / "serve"
    if serve.exists():
        shutil.rmtree(serve)
    (serve / "tiles").mkdir(parents=True)

    features = pl.read_parquet(source / "lsoa_features.parquet").filter(
        pl.col("lad_cd").is_in(lads)
    )
    if features.is_empty():
        raise ValueError(f"No LSOAs in local authorities {lads}")
    lsoas = set(features["lsoa21cd"])
    msoas = set(features["msoa21cd"])
    in_subset = pl.col("lsoa21cd").is_in(list(lsoas))

    # One box per local authority: the chosen areas can be far apart
    boxes = features.group_by("lad_cd").agg(
        (pl.col("pwc_x").min() - POI_BUFFER_M).alias("x0"),
        (pl.col("pwc_x").max() + POI_BUFFER_M).alias("x1"),
        (pl.col("pwc_y").min() - POI_BUFFER_M).alias("y0"),
        (pl.col("pwc_y").max() + POI_BUFFER_M).alias("y1"),
    )
    near = pl.any_horizontal(
        pl.col("x").is_between(b["x0"], b["x1"]) & pl.col("y").is_between(b["y0"], b["y1"])
        for b in boxes.iter_rows(named=True)
    )
    areas = pl.read_parquet(source / "areas.parquet")
    regions = set(features["rgn_cd"])
    nations = set(features["ctry_cd"])
    tables = {
        "lsoa_features.parquet": features,
        "scores.parquet": pl.read_parquet(source / "scores.parquet").filter(in_subset),
        "postcodes.parquet": pl.read_parquet(source / "postcodes.parquet").filter(in_subset),
        "places.parquet": pl.read_parquet(source / "places.parquet").filter(in_subset),
        "areas.parquet": areas.filter(
            ((pl.col("level") == "msoa") & pl.col("code").is_in(list(msoas)))
            | ((pl.col("level") == "lad") & pl.col("code").is_in(lads))
            | ((pl.col("level") == "region") & pl.col("code").is_in(list(regions)))
            | ((pl.col("level") == "nation") & pl.col("code").is_in(list(nations)))
        ),
        "pois.parquet": pl.read_parquet(source / "pois.parquet").filter(near),
    }
    files = {}
    for name, df in tables.items():
        path = serve / name
        # scores.parquet must stay Snappy for the browser's reader
        df.write_parquet(path, compression="snappy")
        files[name] = path

    build_tiles(out_dir=serve / "tiles", lsoas=lsoas, lads=set(lads))

    manifest = json.loads((source / "manifest.json").read_text())
    area_counts = dict(features.group_by("nation").len().sort("nation").iter_rows())
    manifest["geography"] = {**manifest["geography"], "area_counts": area_counts}
    manifest.update(
        demo=True,
        demo_local_authorities=lads,
        lsoa_count=features.height,
        files={
            name: {"sha256": _sha256(p), "bytes": p.stat().st_size} for name, p in files.items()
        },
    )
    (serve / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False))
    total = sum(p.stat().st_size for p in serve.rglob("*") if p.is_file())
    logger.info(f"Demo dataset: {features.height} LSOAs in {lads}, {total / 1e6:.1f} MB at {serve}")
    return files
