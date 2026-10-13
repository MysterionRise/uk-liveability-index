"""Data Zone 2011 → Data Zone 2022 weights, for sources still published on the 2011 zones.

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


def crosswalk(
    values: pl.DataFrame,
    weights: pl.DataFrame,
    columns: list[str],
    how: str = "mean",
    key: str = "dz11cd",
    out_key: str = "lsoa21cd",
) -> pl.DataFrame:
    """Bring ``columns`` of a table keyed by 2011 zone onto the 2022 zones.

    ``mean``: population-weighted mean over the 2011 zones with a value (a rate, a score);
    ``sum``: the 2011 zones' counts apportioned by weight.
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
    return joined.group_by("dz22cd").agg(*aggs).rename({"dz22cd": out_key}).sort(out_key)


def crosswalk_problems(weights: pl.DataFrame, zones: set[str] | None = None) -> list[str]:
    """What would make the weights unusable: a 2022 zone without weights, or weights that
    do not sum to one."""
    problems = []
    sums = weights.group_by("dz22cd").agg(pl.col("weight").sum().alias("s"))
    off = sums.filter((pl.col("s") - 1).abs() > 1e-6)
    if off.height:
        problems.append(f"{off.height} zones whose weights do not sum to 1")
    if zones is not None:
        missing = sorted(zones - set(sums["dz22cd"].to_list()))
        if missing:
            problems.append(f"{len(missing)} zones without weights, e.g. {missing[:5]}")
    return problems


def summary(weights: pl.DataFrame, ranks: pl.DataFrame | None = None) -> dict:
    """The numbers the build reports: how clean the match is, and how little mixing
    moves SIMD's ranks.

    ``ranks``: dz11cd, rank (SIMD 2020v2 overall rank). The crosswalked rank of a zone
    assembled from several 2011 zones is compared with its largest part's rank; a
    Spearman correlation near 1 means the mixing changes little.
    """
    per_zone = weights.group_by("dz22cd").agg(
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
        mixed = per_zone.filter(pl.col("parts") > 1).select("dz22cd")
        walked = crosswalk(ranks, weights, ["rank"], how="mean", out_key="dz22cd")
        top_part = (
            weights.sort("weight", descending=True)
            .unique("dz22cd", keep="first")
            .join(ranks, on="dz11cd")
            .select("dz22cd", pl.col("rank").alias("top_rank"))
        )
        both = mixed.join(walked, on="dz22cd").join(top_part, on="dz22cd")
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
