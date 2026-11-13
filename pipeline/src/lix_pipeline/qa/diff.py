"""``lix qa diff``: what moved between two packs, per indicator and nation.

A data release should move what its new editions and new code explain, and nothing else.
This reads two packs (``lsoa_features.parquet`` and ``manifest.json`` of each) and lists,
for every indicator and nation, how many areas have a value before and after, how many
of those moved, by how much, and where coverage was gained or lost. Two checks follow
that a reviewer otherwise makes by hand:

- an indicator that moved although none of its sources has a new edition (new code, or
  a fault);
- a source with a new edition that moved no indicator reading it (the edition changed
  nothing we use, or it is not being read).

Neither is a failure by itself: the report names them so each gets an explanation.
"""

import json
from pathlib import Path

import polars as pl

# A value moved when it differs by more than rounding: relative to its size, or absolutely
# for values near zero
TOLERANCE = 1e-9
NATIONS = ("E", "W", "S", "N", "I")


def load_pack(folder: Path) -> tuple[dict, pl.DataFrame]:
    manifest = json.loads((folder / "manifest.json").read_text())
    features = pl.read_parquet(folder / "lsoa_features.parquet")
    return manifest, features


def indicator_changes(old: pl.DataFrame, new: pl.DataFrame, ids: list[str]) -> pl.DataFrame:
    """One row per indicator and nation with any value in either pack: areas with a value
    before and after, in both, moved, the median and largest absolute move among those
    that moved, and areas that gained or lost a value."""
    rows = []
    for ind in ids:
        col = f"raw__{ind}"
        before = (
            old.select("lsoa21cd", pl.col(col).alias("old"))
            if col in old.columns
            else old.select("lsoa21cd", pl.lit(None, pl.Float64).alias("old"))
        )
        after = (
            new.select("lsoa21cd", pl.col(col).alias("new"))
            if col in new.columns
            else new.select("lsoa21cd", pl.lit(None, pl.Float64).alias("new"))
        )
        both = before.join(after, on="lsoa21cd", how="full", coalesce=True).with_columns(
            pl.col("lsoa21cd").str.slice(0, 1).alias("nation"),
            (pl.col("new") - pl.col("old")).abs().alias("move"),
        )
        moved = pl.col("move") > TOLERANCE * pl.max_horizontal(pl.lit(1.0), pl.col("old").abs())
        summary = both.group_by("nation").agg(
            pl.col("old").is_not_null().sum().alias("before"),
            pl.col("new").is_not_null().sum().alias("after"),
            (pl.col("old").is_not_null() & pl.col("new").is_not_null()).sum().alias("both"),
            moved.fill_null(False).sum().alias("moved"),
            pl.col("move").filter(moved).median().alias("median_move"),
            pl.col("move").filter(moved).max().alias("largest_move"),
            (pl.col("old").is_null() & pl.col("new").is_not_null()).sum().alias("gained"),
            (pl.col("old").is_not_null() & pl.col("new").is_null()).sum().alias("lost"),
        )
        rows.append(
            summary.filter((pl.col("before") > 0) | (pl.col("after") > 0)).with_columns(
                pl.lit(ind).alias("indicator")
            )
        )
    out = pl.concat(rows) if rows else pl.DataFrame()
    order = {n: i for i, n in enumerate(NATIONS)}
    return out.with_columns(
        pl.col("nation").replace_strict(order, default=9).alias("_order")
    ).sort("indicator", "_order").drop("_order").select(
        "indicator", "nation", "before", "after", "both", "moved",
        "median_move", "largest_move", "gained", "lost",
    )  # fmt: skip


def new_editions(
    old: dict, new: dict, old_lock: dict | None = None, new_lock: dict | None = None
) -> dict[str, str]:
    """Sources that differ between the packs, with why: ``new source``, ``new file`` or
    ``new edition``. With both lockfiles a source differs when its file's checksum does
    (a timetable republished under the same name is a new file; a list fetched again and
    found identical is not); a source with no checksum (an API, a WFS layer), and every
    source when the lockfiles are not given, is compared by its version."""

    def edition(entry: dict) -> str | None:
        return entry.get("version") or entry.get("fetched_version")

    before, after = old.get("sources") or {}, new.get("sources") or {}
    out = {}
    for slug, entry in after.items():
        if slug not in before:
            out[slug] = "new source"
            continue
        was_sum = ((old_lock or {}).get(slug) or {}).get("fetched", {}).get("sha256")
        now_sum = ((new_lock or {}).get(slug) or {}).get("fetched", {}).get("sha256")
        if was_sum and now_sum:
            if was_sum != now_sum:
                out[slug] = "new file"
        elif edition(entry) != edition(before[slug]):
            out[slug] = "new edition"
    return out


def definitions_changed(old: dict, new: dict) -> dict[str, str]:
    """Indicators that are new, or whose definition (sources, builder, params, coverage)
    differs between the manifests."""
    before = {i["id"]: i for i in old.get("indicators") or []}
    keys = ("sources", "builder", "params", "coverage", "unit", "direction")
    out = {}
    for ind in new.get("indicators") or []:
        was = before.get(ind["id"])
        if was is None:
            out[ind["id"]] = "new indicator"
        else:
            changed = [k for k in keys if was.get(k) != ind.get(k)]
            if changed:
                out[ind["id"]] = f"{', '.join(changed)} changed"
    return out


def review(
    old: dict,
    new: dict,
    changes: pl.DataFrame,
    old_lock: dict | None = None,
    new_lock: dict | None = None,
) -> dict:
    """The two checks: what moved without a new edition or a new definition, and which
    new editions moved nothing."""
    editions = new_editions(old, new, old_lock, new_lock)
    definitions = definitions_changed(old, new)
    sources = {i["id"]: i.get("sources") or [] for i in new.get("indicators") or []}
    touched = (
        changes.group_by("indicator")
        .agg((pl.col("moved") + pl.col("gained") + pl.col("lost")).sum().alias("n"))
        .filter(pl.col("n") > 0)
    )
    moved = dict(touched.rows())
    unexplained = sorted(
        ind
        for ind in moved
        if ind not in definitions and not any(s in editions for s in sources.get(ind, []))
    )
    readers: dict[str, list[str]] = {}
    for ind, slugs in sources.items():
        for slug in slugs:
            readers.setdefault(slug, []).append(ind)
    quiet = sorted(
        slug
        for slug in editions
        if readers.get(slug) and not any(ind in moved for ind in readers[slug])
    )
    return {
        "editions": editions,
        "definitions": definitions,
        "moved": moved,
        "moved_without_cause": unexplained,
        "editions_that_moved_nothing": quiet,
        "unread_editions": sorted(s for s in editions if not readers.get(s)),
    }


def _number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:.3g}"


def render(old: dict, new: dict, changes: pl.DataFrame, found: dict) -> str:
    def name(manifest: dict) -> str:
        return manifest.get("data_release") or f"built {str(manifest.get('generated_at'))[:10]}"

    lines = [
        f"# Pack comparison: {name(old)} → {name(new)}",
        "",
        f"{len(found['editions'])} sources have a new edition or are new; "
        f"{len(found['definitions'])} indicators are new or redefined; "
        f"{len(found['moved'])} of {changes['indicator'].n_unique()} indicators moved.",
        "",
        "## To explain",
        "",
    ]
    without = found["moved_without_cause"]
    lines.append(
        f"- **Moved without a new edition or a new definition ({len(without)}):** "
        + (", ".join(f"`{i}`" for i in without) or "none")
    )
    quiet = found["editions_that_moved_nothing"]
    lines.append(
        f"- **New editions that moved no indicator reading them ({len(quiet)}):** "
        + (", ".join(f"`{s}`" for s in quiet) or "none")
    )
    lines += ["", "## New or redefined indicators", ""]
    lines += [f"- `{ind}`: {why}" for ind, why in sorted(found["definitions"].items())] or ["none"]
    lines += ["", "## New sources, files and editions", ""]
    lines += [f"- `{slug}`: {why}" for slug, why in sorted(found["editions"].items())] or ["none"]
    lines += [
        "",
        "## Every indicator that moved",
        "",
        "| Indicator | Nation | Before | After | Moved | Median move | Largest | Gained | Lost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    shown = changes.filter((pl.col("moved") > 0) | (pl.col("gained") > 0) | (pl.col("lost") > 0))
    for row in shown.iter_rows(named=True):
        lines.append(
            f"| `{row['indicator']}` | {row['nation']} | {row['before']:,} | {row['after']:,} "
            f"| {row['moved']:,} | {_number(row['median_move'])} | {_number(row['largest_move'])} "
            f"| {row['gained']:,} | {row['lost']:,} |"
        )
    same = changes["indicator"].n_unique() - shown["indicator"].n_unique()
    lines += ["", f"{same} indicators are identical in every nation.", ""]
    return "\n".join(lines)


def diff_packs(
    old_dir: Path, new_dir: Path, old_lock: Path | None = None, new_lock: Path | None = None
) -> tuple[str, dict]:
    """The report and its findings. ``old_lock`` and ``new_lock`` are the lockfiles the
    two packs were built from (``git show <tag>:config/datasets.lock.json`` for the
    earlier one): with both, a new edition is a file whose checksum changed."""
    locks = [json.loads(p.read_text()) if p else None for p in (old_lock, new_lock)]
    old_manifest, old_features = load_pack(old_dir)
    new_manifest, new_features = load_pack(new_dir)
    ids = list(
        dict.fromkeys(
            [i["id"] for i in new_manifest.get("indicators") or []]
            + [i["id"] for i in old_manifest.get("indicators") or []]
        )
    )
    changes = indicator_changes(old_features, new_features, ids)
    found = review(old_manifest, new_manifest, changes, *locks)
    return render(old_manifest, new_manifest, changes, found), found
