"""Face-validity review: how well-known places score (config/validation_places.yaml).

Each place is found in OS Open Names (data/serve/places.parquet) and scored as the
neighbourhood (MSOA) its point sits in: population-weighted theme and overall scores
(default preset) and raw indicator values. ``write_review`` writes docs/validation.md;
``anchor_problems`` returns the failing ``anchor: true`` checks for ``lix validate``.
"""

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from lix_core.config import get_config, load_indicators
from lix_core.paths import data_dir, get_project_root

# Bigger settlements first when a name is ambiguous within a local authority
PLACE_PRIORITY = ["City", "Town", "Suburban Area", "Village", "Other Settlement", "Hamlet"]


@dataclass
class CheckResult:
    label: str
    passed: bool
    anchor: bool
    actual: str


@dataclass
class PlaceResult:
    name: str
    why: str
    found: bool
    neighbourhood: str = ""
    local_authority: str = ""
    overall: float | None = None
    overall_pct: float | None = None
    themes: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)


LETTERS = set("abcdefghijklmnopqrstuvwxyz ")


def _resolve(places: pl.DataFrame, name: str, local_authority: str | None) -> str | None:
    hits = places.filter(pl.col("name").str.to_lowercase() == name.lower())
    if local_authority:
        # "St. Albans" in OS Open Names, "St Albans" elsewhere; both sides keep ASCII letters
        # only, so "Dún Laoghaire-Rathdown" matches however the fada is written
        plain = pl.col("local_authority").str.to_lowercase().str.replace_all(r"[^a-z ]", "")
        wanted = "".join(ch for ch in local_authority.lower() if ch in LETTERS)
        hits = hits.filter(plain.str.contains(wanted, literal=True))
    if hits.is_empty():
        return None
    rank = {t: i for i, t in enumerate(PLACE_PRIORITY)}
    hits = hits.with_columns(
        pl.col("place_type").replace_strict(rank, default=len(rank)).alias("_rank")
    )
    return hits.sort("_rank").row(0, named=True)["lsoa21cd"]


def _wmean(rows: pl.DataFrame, col: str) -> float | None:
    ok = rows.filter(pl.col(col).is_not_null())
    if ok.is_empty():
        return None
    w = ok["population"].cast(pl.Float64)
    return float((ok[col].cast(pl.Float64) * w).sum() / w.sum())


def _check(check: dict, area: pl.DataFrame) -> CheckResult:
    anchor = bool(check.get("anchor"))
    if "flag" in check:
        flagged = sorted(
            {
                c[3:]
                for c in area.columns
                if c.startswith("q__") and (area[c] == check["flag"]).any()
            }
        )
        return CheckResult(
            f"flag {check['flag']}", bool(flagged), anchor, ", ".join(flagged) or "none"
        )
    if "theme" in check:
        col, label = f"theme__{check['theme']}", f"{check['theme']} score"
    elif "overall" in check:
        col, label = "overall", "overall score"
    else:
        col, label = f"raw__{check['indicator']}", check["indicator"]
    actual = _wmean(area, col) if col in area.columns else None
    op, value = check["op"], check["value"]
    passed = actual is not None and (actual >= value if op == "ge" else actual <= value)
    shown = "–" if actual is None else f"{actual:,.1f}"
    return CheckResult(f"{label} {'≥' if op == 'ge' else '≤'} {value:,}", passed, anchor, shown)


def review(serve_dir: Path | None = None) -> list[PlaceResult]:
    serve = serve_dir or data_dir("serve")
    features = pl.read_parquet(serve / "lsoa_features.parquet")
    places = pl.read_parquet(serve / "places.parquet")
    themes = sorted(load_indicators().themes)
    out = []
    for spec in get_config("validation_places")["places"]:
        lsoa = spec.get("lsoa") or _resolve(places, spec["name"], spec.get("local_authority"))
        # A pinned code outside this build (another nation's anchor in a one-nation run)
        # is not found either
        hit = features.filter(pl.col("lsoa21cd") == lsoa) if lsoa else features.clear()
        result = PlaceResult(spec["name"], spec.get("why", ""), found=not hit.is_empty())
        if hit.is_empty():
            out.append(result)
            continue
        home = hit.row(0, named=True)
        if spec.get("level") == "lsoa":
            area = features.filter(pl.col("lsoa21cd") == lsoa)
            result.neighbourhood = f"{home['lsoa21nm']} ({home['msoa_name']})"
        else:
            area = features.filter(pl.col("msoa21cd") == home["msoa21cd"])
            result.neighbourhood = home["msoa_name"]
        result.local_authority = home["lad_nm"]
        result.overall = _wmean(area, "overall")
        result.overall_pct = _wmean(area, "overall_pct")
        result.themes = {t: _wmean(area, f"theme__{t}") for t in themes}
        result.flags = sorted(
            {
                str(v)
                for c in area.columns
                if c.startswith("q__")
                for v in area[c].unique().to_list()
                if v not in (None, "ok", "missing")
            }
        )
        result.checks = [_check(c, area) for c in spec.get("checks", [])]
        out.append(result)
    return out


def anchor_problems(results: list[PlaceResult] | None = None) -> list[str]:
    results = results if results is not None else review()
    problems = []
    for r in results:
        if not r.found:
            problems.append(f"{r.name}: not found in OS Open Names")
            continue
        for c in r.checks:
            if c.anchor and not c.passed:
                problems.append(f"{r.name} ({r.neighbourhood}): {c.label}, but it is {c.actual}")
    return problems


def write_review(path: Path | None = None) -> Path:
    results = review()
    themes = sorted(load_indicators().themes)
    path = path or get_project_root() / "docs" / "validation.md"
    checks = [c for r in results for c in r.checks]
    passed = sum(c.passed for c in checks)
    anchors = [c for c in checks if c.anchor]
    lines = [
        "# Face-validity review",
        "",
        "How well-known places score, against what someone who knows them would expect.",
        "Each place is scored as the neighbourhood (MSOA) its OS Open Names point sits in,",
        "population-weighted, with the default (balanced) weights. Expectations are in",
        "[config/validation_places.yaml](../config/validation_places.yaml); regenerate this",
        "page with `uv run lix qa places`. Checks marked ⚓ also run in `make validate`.",
        "",
        f"**{passed} of {len(checks)} checks pass** ({sum(c.passed for c in anchors)} of "
        f"{len(anchors)} anchors) across {len(results)} places.",
        "",
        "## Scores",
        "",
        "| Place | Neighbourhood | Overall | " + " | ".join(t.capitalize() for t in themes) + " |",
        "|---|---|---|" + "---|" * len(themes),
    ]
    for r in results:
        if not r.found:
            lines.append(f"| {r.name} | not found | | " + " | " * (len(themes) - 1) + "|")
            continue
        cells = [f"{r.themes[t]:.0f}" if r.themes.get(t) is not None else "–" for t in themes]
        lines.append(
            f"| {r.name} | {r.neighbourhood}, {r.local_authority} | "
            f"{r.overall:.0f} | " + " | ".join(cells) + " |"
        )
    observations = get_config("validation_places").get("observations") or []
    if observations:
        lines += ["", "## Observations", ""] + [f"- {o}" for o in observations]
    lines += ["", "## Checks", ""]
    for r in results:
        if not r.found:
            lines.append(f"- **{r.name}**: not found")
            continue
        flags = f" Flags: {', '.join(r.flags)}." if r.flags else ""
        lines.append(f"**{r.name}** ({r.neighbourhood}). {r.why}.{flags}")
        lines.append("")
        for c in r.checks:
            mark = "✅" if c.passed else "❌"
            lines.append(f"- {mark} {c.label}: {c.actual}{' ⚓' if c.anchor else ''}")
        lines.append("")
    path.write_text("\n".join(lines))
    return path
