"""Quality report on a full build: coverage, flags, overlaps and face validity.

Writes data/serve/qa.md. Scored indicators correlating above ``MAX_CORRELATION``
(Spearman) are listed: they may be measuring the same thing twice.
"""

import polars as pl

from lix_core.config import load_indicators
from lix_core.paths import data_dir
from lix_pipeline.qa.validate import COVERAGE_EXCEPTIONS, MIN_SCORED_COVERAGE

MAX_CORRELATION = 0.8


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
        theme_cols = [f"theme__{t}" for t in catalogue.themes if f"theme__{t}" in features.columns]
        by_nation = (
            features.group_by("nation")
            .agg(
                pl.len().alias("areas"),
                *[pl.col(c).median().round(1).alias(c.removeprefix("theme__")) for c in theme_cols],
                pl.col("overall_pct").median().round(1).alias("overall percentile"),
                ((pl.col("band") == 1).mean() * 100).round(1).alias("band 1 %"),
            )
            .sort("nation")
        )
        out += [
            "## Theme medians by nation\n",
            "Each nation's median theme score and overall percentile, and its share of areas "
            "in the bottom band. Offsets come from the indicators a nation lacks and the "
            "substitutes that stand in (the methodology's benchmarks section); the "
            "within-nation comparison removes them.\n",
            _table(by_nation),
            "",
        ]

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
