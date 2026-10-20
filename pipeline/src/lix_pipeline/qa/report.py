"""Quality report on a full build: coverage, flags, overlaps and face validity.

Writes data/serve/qa.md. Scored indicators correlating above ``MAX_CORRELATION``
(Spearman) are listed: they may be measuring the same thing twice.
"""

import polars as pl

from lix_core.config import load_indicators
from lix_core.paths import data_dir
from lix_pipeline.qa.validate import COVERAGE_EXCEPTIONS, MIN_SCORED_COVERAGE

MAX_CORRELATION = 0.8
# A nation holding more than this share of either 1% tail of the build-wide percentile
# while its headline still compares across the build is worth a look (#147)
MAX_TAIL_SHARE = 0.6
# A scored indicator with more than this share of its values at one number says little
MAX_CONSTANT_SHARE = 0.5
# An Irish sub-district this populous with no recorded crime at all is a reporting gap
MIN_POPULATION_FOR_ZERO_CRIME = 5000


def tail_warnings(features: pl.DataFrame, rules: dict[str, str]) -> list[str]:
    """Nations holding more than ``MAX_TAIL_SHARE`` of the build-wide percentile's top or
    bottom 1% without comparing within themselves."""
    column = "overall_pct_all" if "overall_pct_all" in features.columns else "overall_pct"
    if "nation" not in features.columns or features[column].null_count() == features.height:
        return []
    out = []
    for name, tail in (("top", pl.col(column) >= 99), ("bottom", pl.col(column) <= 1)):
        rows = features.filter(tail & pl.col(column).is_not_null())
        if rows.height == 0:
            continue
        for nation, count in rows.group_by("nation").len().iter_rows():
            share = count / rows.height
            if share > MAX_TAIL_SHARE and rules.get(nation, "all") == "all":
                out.append(
                    f"{nation} holds {share:.0%} of the {name} 1% of the build-wide percentile "
                    f"({count:,} of {rows.height:,}): set headline_within: nation for it or "
                    "check its substitutes"
                )
    return out


def constant_warnings(features: pl.DataFrame, scored_ids: list[str]) -> list[str]:
    """Scored indicators with more than ``MAX_CONSTANT_SHARE`` of their values at one
    number (a builder that misses most of what it should count, #180)."""
    out = []
    for ind in scored_ids:
        raw = features[f"raw__{ind}"].drop_nulls()
        if raw.len() == 0:
            continue
        top = raw.value_counts().sort("count", descending=True).row(0)
        share = top[1] / raw.len()
        if share > MAX_CONSTANT_SHARE:
            out.append(f"{ind} reads {top[0]} for {share:.0%} of the areas that have it")
    return out


def zero_crime_warnings(features: pl.DataFrame) -> list[str]:
    """Irish areas of populous sub-districts whose every crime rate is exactly 0 (#143)."""
    cols = [c for c in features.columns if c.startswith("raw__crime_")]
    if not cols or "nation" not in features.columns:
        return []
    zero = features.filter(
        (pl.col("nation") == "I") & pl.all_horizontal(pl.col(c).fill_null(-1) == 0 for c in cols)
    )
    if zero.height == 0:
        return []
    heavy = zero.group_by("msoa21cd").agg(pl.col("population").sum(), pl.len().alias("areas"))
    heavy = heavy.filter(pl.col("population") > MIN_POPULATION_FOR_ZERO_CRIME)
    return [
        f"{row['msoa21cd']}: {row['areas']} Irish areas with {row['population']:,} people and no "
        "recorded crime at all (a station that stopped reporting?)"
        for row in heavy.iter_rows(named=True)
    ]


def _table(df: pl.DataFrame) -> str:
    cols = df.columns
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.iter_rows():
        cells = [f"{v:.1f}" if isinstance(v, float) else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report() -> tuple[str, list[str]]:
    """Return (markdown, problems)."""
    catalogue = load_indicators()
    features = pl.read_parquet(data_dir("serve") / "lsoa_features.parquet")
    problems: list[str] = []
    out = ["# Build quality report\n"]

    rows = []
    for ind in catalogue.indicators:
        raw = features[f"raw__{ind.id}"]
        flags = features[f"q__{ind.id}"].value_counts()
        flagged = {
            r[f"q__{ind.id}"]: r["count"] for r in flags.to_dicts() if r[f"q__{ind.id}"] != "ok"
        }
        # Coverage is judged where the indicator is built: not_available areas (another
        # nation's) are expected gaps, and lix validate serve checks them separately
        built = features[f"q__{ind.id}"] != "not_available"
        if not built.any():
            # Built for no nation in this build (a Northern Ireland-only run has no
            # England-only indicators): nothing to measure
            continue
        coverage = raw.filter(built).is_not_null().mean() * 100
        nations = ", ".join(sorted(set(features.filter(built)["nation"].to_list())))
        minimum = COVERAGE_EXCEPTIONS.get(ind.id, MIN_SCORED_COVERAGE) * 100
        if ind.role == "scored" and coverage < minimum:
            problems.append(f"{ind.id} covers only {coverage:.1f}% of LSOAs in {nations}")
        rows.append({
            "indicator": ind.id, "role": ind.role, "theme": ind.theme, "nations": nations,
            "coverage %": coverage, "median": raw.median(), "flags": str(flagged or ""),
        })  # fmt: skip
    out += ["## Indicators\n", _table(pl.DataFrame(rows)), ""]

    # Offsets between nations: a theme built from fewer indicators, or from a substitute
    # with another distribution, sits higher or lower than England's (#112)
    if "nation" in features.columns and features["nation"].n_unique() > 1:
        from lix_core.scoring import band

        theme_cols = [f"theme__{t}" for t in catalogue.themes if f"theme__{t}" in features.columns]
        # The offsets are measured on the build-wide percentile even where a nation's
        # headline compares within itself
        pct_all = "overall_pct_all" if "overall_pct_all" in features.columns else "overall_pct"
        rules = (
            dict(features.select("nation", "headline_within").unique().iter_rows())
            if "headline_within" in features.columns
            else {}
        )
        by_nation = (
            features.with_columns(band(pl.col(pct_all)).alias("_band_all"))
            .group_by("nation")
            .agg(
                pl.len().alias("areas"),
                *[pl.col(c).median().round(1).alias(c.removeprefix("theme__")) for c in theme_cols],
                pl.col(pct_all).median().round(1).alias("overall percentile"),
                ((pl.col("_band_all") == 1).mean() * 100).round(1).alias("band 1 %"),
            )
            .sort("nation")
            .with_columns(
                pl.col("nation").replace_strict(rules, default="all").alias("headline within")
            )
        )
        out += [
            "## Theme medians by nation\n",
            "Each nation's median theme score and build-wide overall percentile, its share of "
            "areas in the bottom band of that percentile, and what its headline compares "
            "against. Offsets come from the indicators a nation lacks and the substitutes "
            "that stand in (the methodology's benchmarks section); the within-nation "
            "comparison removes them.\n",
            _table(by_nation),
            "",
        ]
        problems += tail_warnings(features, rules)
    problems += constant_warnings(features, [i.id for i in catalogue.scored()])
    problems += zero_crime_warnings(features)

    scored = catalogue.scored()
    ids = [i.id for i in scored]
    ranks = features.select([pl.col(f"n__{i}").rank() for i in ids]).fill_null(strategy="mean")
    corr = ranks.corr()
    pairs = []
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            rho = corr[a, b]
            if abs(rho) > MAX_CORRELATION:
                same = scored[a].theme == scored[b].theme
                pairs.append({"a": ids[a], "b": ids[b], "rho": rho, "same theme": same})
    out.append(f"## Scored indicators correlating above |ρ| = {MAX_CORRELATION}\n")
    out.append(
        "Within a theme this double-counts one thing; across themes it mostly reflects "
        "population density, which drives every access measure.\n"
    )
    out.append(_table(pl.DataFrame(pairs)) if pairs else "None.")
    for p in pairs:
        if p["same theme"]:
            problems.append(f"{p['a']} and {p['b']} (same theme) correlate at ρ={p['rho']:.2f}")

    import json

    manifest = json.loads((data_dir("serve") / "manifest.json").read_text())
    stale = {k: v for k, v in manifest.get("sources", {}).items() if v.get("stale")}
    reasons = {
        "newer_edition": "a newer edition is published",
        "older_than_cadence": "older than its publishing cadence",
    }

    def why(v: dict) -> str:
        reason = reasons.get(v.get("stale_reason"), "older than its publishing cadence")
        period = (v.get("period") or {}).get("label")
        return f"{reason}; data for {period}" if period else reason

    out.append("\n## Stale sources\n")
    out.append(
        "\n".join(f"- {k}: {why(v)} (fetched {v.get('fetched_at')})" for k, v in stale.items())
        if stale
        else "None: every source is at its latest edition and within its publishing cadence."
    )
    for k, v in stale.items():
        problems.append(f"{k} is stale: {why(v)}")

    themes = sorted(c for c in features.columns if c.startswith("theme__"))
    view = ["lsoa21nm", "msoa_name", "overall", "overall_pct", *themes]
    short = {t: t.removeprefix("theme__") for t in themes}
    for title, df in [
        ("Top 15 (default preset)", features.sort("overall", descending=True).head(15)),
        ("Bottom 15", features.sort("overall").head(15)),
    ]:
        out += [f"\n## {title}\n", _table(df.select(view).rename(short))]
    for col, title in [("rgn_nm", "region"), ("ruc21nm", "urban/rural class")]:
        summary = (
            features.group_by(col)
            .agg(
                pl.len().alias("LSOAs"), pl.col("overall").median(), pl.col("overall_pct").median()
            )
            .sort("overall", descending=True)
        )
        out += [f"\n## Median scores by {title}\n", _table(summary)]
    return "\n".join(out) + "\n", problems


def write_report() -> list[str]:
    text, problems = build_report()
    (data_dir("serve") / "qa.md").write_text(text)
    return problems
