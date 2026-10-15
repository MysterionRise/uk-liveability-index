"""Weights from one small-area geography to another, for sources still published on an
older one: Scotland's Data Zones 2011 → 2022, Northern Ireland's Super Output Areas
2001 → Super Data Zones 2021.

Scotland redrew its Data Zones after the 2022 census (6,976 → 7,392) and SIMD 2020 is still
on the old ones; no official lookup between the two exists. The weights come from
postcodes: the SIMD postcode lookup says which 2011 zone each postcode was in, the NSPL
which 2022 zone and output area it is in now, and NRS's output-area populations say how
many people each postcode stands for (an output area's residents split evenly over its
live postcodes). ``w(dz22, dz11)`` is the share of a 2022 zone's residents who were in
each 2011 zone, so a value crosswalked with ``how="mean"`` is a population-weighted mean
and a count with ``how="sum"`` is apportioned.
"""

import polars as pl

DOMINANT = 0.8  # a 2022 zone mostly made of one 2011 zone
WEIGHT_COLUMNS = ["dz11cd", "dz22cd", "weight", "dominant"]


def build_dz11_dz22(
    simd_postcodes: pl.DataFrame, nspl: pl.LazyFrame, oa_population: pl.DataFrame
) -> pl.DataFrame:
    """Weights per (2022 zone, 2011 zone), summing to 1 for every 2022 zone with a match.

    ``simd_postcodes``: postcode_norm, dz11cd. ``nspl``: Scottish rows with postcode_norm,
    oa21cd, lsoa21cd (the 2022 zone) and live. ``oa_population``: oa21cd, population.
    Live postcodes carry their output area's residents; where a zone's postcodes carry no
    population at all (an unpopulated output area), they count one each instead.
    """
    live = nspl.filter(pl.col("live")).select("postcode_norm", "oa21cd", "lsoa21cd").collect()
    per_oa = live.group_by("oa21cd").agg(pl.len().alias("n_live"))
    matched = (
        live.join(simd_postcodes.unique("postcode_norm"), on="postcode_norm", how="inner")
        .join(per_oa, on="oa21cd", how="left")
        .join(oa_population, on="oa21cd", how="left")
        .with_columns((pl.col("population").fill_null(0) / pl.col("n_live")).alias("w"))
        .rename({"lsoa21cd": "dz22cd"})
    )
    zone_total = matched.group_by("dz22cd").agg(pl.col("w").sum().alias("total"))
    matched = matched.join(zone_total, on="dz22cd").with_columns(
        pl.when(pl.col("total") > 0).then(pl.col("w")).otherwise(1.0).alias("w")
    )
    pairs = (
        matched.group_by("dz22cd", "dz11cd")
        .agg(pl.col("w").sum().alias("w"))
        .with_columns((pl.col("w") / pl.col("w").sum().over("dz22cd")).alias("weight"))
        .with_columns((pl.col("weight").max().over("dz22cd") >= DOMINANT).alias("dominant"))
        .select(WEIGHT_COLUMNS)
        .sort("dz22cd", "dz11cd")
    )
    return pairs


def build_from_points(
    points: pl.DataFrame, old_polygons, new_key: str, old_key: str
) -> pl.DataFrame:
    """Weights per (new area, old area) from population points, where no postcode lookup
    exists (Northern Ireland's Central Postcode Directory is not open): each point is a
    Data Zone's population-weighted centroid carrying its residents and lies in one old
    area, so a new area's weights are the shares of its residents by old area.

    ``points``: new_key, x, y, population (in the polygons' CRS). ``old_polygons``: a
    GeoDataFrame indexed by the old area's code. A new area whose points carry no
    population counts each point once.
    """
    from lix_pipeline.geo.joins import points_to_lsoa

    polys = old_polygons[["geometry"]].reset_index()
    polys = polys.rename(columns={polys.columns[0]: "lsoa21cd"})
    located = (
        points_to_lsoa(points, x="x", y="y", polygons=polys)
        .rename({"lsoa21cd": old_key})
        .filter(pl.col(old_key).is_not_null())
    )
    total = located.group_by(new_key).agg(pl.col("population").fill_null(0).sum().alias("t"))
    located = located.join(total, on=new_key).with_columns(
        pl.when(pl.col("t") > 0).then(pl.col("population").fill_null(0)).otherwise(1.0).alias("w")
    )
    return (
        located.group_by(new_key, old_key)
        .agg(pl.col("w").sum().alias("w"))
        .with_columns((pl.col("w") / pl.col("w").sum().over(new_key)).alias("weight"))
        .with_columns((pl.col("weight").max().over(new_key) >= DOMINANT).alias("dominant"))
        .select(old_key, new_key, "weight", "dominant")
        .sort(new_key, old_key)
    )


def crosswalk(
    values: pl.DataFrame,
    weights: pl.DataFrame,
    columns: list[str],
    how: str = "mean",
    key: str = "dz11cd",
    out_key: str = "lsoa21cd",
    new_key: str = "dz22cd",
) -> pl.DataFrame:
    """Bring ``columns`` of a table keyed by the old area (``key``) onto the new areas
    (``new_key`` in the weights, written as ``out_key``).

    ``mean``: population-weighted mean over the old areas with a value (a rate, a score);
    ``sum``: the old areas' counts apportioned by weight.
    """
    joined = weights.join(values.select(key, *columns), on=key, how="inner")
    if how == "mean":
        aggs = [
            (
                (pl.col(c) * pl.col("weight")).sum()
                / pl.col("weight").filter(pl.col(c).is_not_null()).sum()
            )
            .fill_nan(None)
            .alias(c)
            for c in columns
        ]
    elif how == "sum":
        aggs = [(pl.col(c) * pl.col("weight")).sum().alias(c) for c in columns]
    else:
        raise ValueError(f"how must be mean or sum, not {how!r}")
    return joined.group_by(new_key).agg(*aggs).rename({new_key: out_key}).sort(out_key)


def crosswalk_problems(
    weights: pl.DataFrame, zones: set[str] | None = None, new_key: str = "dz22cd"
) -> list[str]:
    """What would make the weights unusable: a new area without weights, or weights that
    do not sum to one."""
    problems = []
    sums = weights.group_by(new_key).agg(pl.col("weight").sum().alias("s"))
    off = sums.filter((pl.col("s") - 1).abs() > 1e-6)
    if off.height:
        problems.append(f"{off.height} zones whose weights do not sum to 1")
    if zones is not None:
        missing = sorted(zones - set(sums[new_key].to_list()))
        if missing:
            problems.append(f"{len(missing)} zones without weights, e.g. {missing[:5]}")
    return problems


def summary(
    weights: pl.DataFrame,
    ranks: pl.DataFrame | None = None,
    key: str = "dz11cd",
    new_key: str = "dz22cd",
) -> dict:
    """The numbers the build reports: how clean the match is, and how little mixing
    moves the index's ranks.

    ``ranks``: key, rank (the overall rank on the old areas). The crosswalked rank of an
    area assembled from several old ones is compared with its largest part's rank; a
    Spearman correlation near 1 means the mixing changes little.
    """
    per_zone = weights.group_by(new_key).agg(
        pl.col("weight").max().alias("top"), pl.len().alias("parts")
    )
    out = {
        "zones": per_zone.height,
        "one_to_one": int((per_zone["parts"] == 1).sum()),
        "dominant": int((per_zone["top"] >= DOMINANT).sum()),
        "three_or_more_parts": int((per_zone["parts"] >= 3).sum()),
        "mean_top_weight": round(float(per_zone["top"].mean()), 4),
    }
    if ranks is not None:
        mixed = per_zone.filter(pl.col("parts") > 1).select(new_key)
        walked = crosswalk(
            ranks, weights, ["rank"], how="mean", key=key, out_key=new_key, new_key=new_key
        )
        top_part = (
            weights.sort("weight", descending=True)
            .unique(new_key, keep="first")
            .join(ranks, on=key)
            .select(new_key, pl.col("rank").alias("top_rank"))
        )
        both = mixed.join(walked, on=new_key).join(top_part, on=new_key)
        out["mixed_zones"] = both.height
        out["spearman_mixed"] = round(
            float(both.select(pl.corr("rank", "top_rank", method="spearman")).item()), 4
        )
    return out


def _main() -> None:
    """``python -m lix_pipeline.geo.crosswalk``: the summary for the staged weights."""
    import fastexcel

    from lix_core.paths import data_dir, raw_file

    weights = pl.read_parquet(data_dir("staged") / "dz11_dz22.parquet")
    ranks = (
        fastexcel.read_excel(raw_file("simd_2020_ranks", "xlsx"))
        .load_sheet_by_name("SIMD 2020v2 ranks")
        .to_polars()
        .select(
            pl.col("Data_Zone").alias("dz11cd"),
            pl.col("SIMD2020v2_Rank").cast(pl.Float64).alias("rank"),
        )
    )
    for k, v in summary(weights, ranks).items():
        print(f"{k}: {v}")
    print("problems:", crosswalk_problems(weights) or "none")


if __name__ == "__main__":
    _main()
