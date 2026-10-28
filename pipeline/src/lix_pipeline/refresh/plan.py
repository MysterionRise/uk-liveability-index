"""What a refresh has to do, decided from the lockfile and the sidecars.

Everything here is a pure function of its arguments, so ``--dry-run`` can print the
plan and the tests can exercise it without data. The sidecars are:

    data/staged/<slug>.meta.json       one per staged table (stage.graph)
    data/indicators/long.meta.json     one entry per indicator
    data/serve/.meta.json              the scores and manifest
    data/serve/tiles/.meta.json        the map tiles
"""

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from lix_core.codes import active_nations
from lix_core.config import IndicatorSpec, boundary_slugs
from lix_core.paths import data_dir, get_project_root
from lix_pipeline.stage import STAGE_READS, graph

# Serve files depend on these configuration files as well as on the indicator table (the
# translation catalogues ride the manifest, so a corrected label re-scores: #197)
# The registry is in it because the manifest publishes each source's title, licence,
# attribution and coverage (#215); the catalogues are globbed, so a fifth one counts
SERVE_CONFIG = ("indicators.yaml", "weights.yaml", "nations.yaml", "datasets.yaml")


def serve_config_files(root: Path) -> list[str]:
    return [
        *SERVE_CONFIG,
        *sorted(f"i18n/{p.name}" for p in (root / "config" / "i18n").glob("*.yaml")),
    ]


def tile_inputs() -> tuple[str, ...]:
    """The boundary files the tiles are cut from: every active nation's low and mid tiers
    and the UK local-authority file."""
    nations = active_nations()
    return tuple(
        boundary_slugs("low", nations) + boundary_slugs("mid", nations) + ["lad_boundaries"]
    )


@dataclass
class Step:
    name: str
    reason: str


@dataclass
class RefreshPlan:
    nations: list[str]
    resolve: list[str] = field(default_factory=list)
    fetch: list[Step] = field(default_factory=list)
    stage: list[Step] = field(default_factory=list)
    skipped: list[Step] = field(default_factory=list)
    indicators: list[Step] = field(default_factory=list)
    serve: Step | None = None
    tiles: Step | None = None

    def empty(self) -> bool:
        """Nothing to build (fetching is always a check, never work in itself)."""
        return not (self.stage or self.indicators or self.serve or self.tiles)

    def render(self) -> str:
        lines = [f"Refresh plan for nations {', '.join(self.nations)}"]
        lines.append(f"  resolve and revalidate {len(self.resolve)} sources")
        editions = [s for s in self.fetch if s.reason.startswith("new edition")]
        for s in editions:
            lines.append(f"    fetch  {s.name:24} {s.reason}")
        for section, steps in (("stage", self.stage), ("indicator", self.indicators)):
            if steps:
                lines.append(f"  {section} ({len(steps)})")
                lines.extend(f"    {section:9} {s.name:24} {s.reason}" for s in steps)
        for s in (self.serve, self.tiles):
            if s:
                lines.append(f"  {s.name:9} {s.reason}")
        if self.skipped:
            lines.append(f"  skipped ({len(self.skipped)})")
            lines.extend(f"    skip      {s.name:24} {s.reason}" for s in self.skipped)
        if self.empty():
            lines.append("  nothing to rebuild")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        def steps(items: list[Step]) -> list[dict]:
            return [{"name": s.name, "reason": s.reason} for s in items]

        return {
            "nations": self.nations,
            "resolve": self.resolve,
            "fetch": steps(self.fetch),
            "stage": steps(self.stage),
            "skipped": steps(self.skipped),
            "indicators": steps(self.indicators),
            "serve": self.serve.__dict__ if self.serve else None,
            "tiles": self.tiles.__dict__ if self.tiles else None,
        }


def slugs_in_scope(registry: dict, nations: Iterable[str]) -> list[str]:
    active = set(nations)
    return [s for s, spec in registry.items() if active & set(spec.coverage)]


def fetch_steps(lock: dict, registry: dict, slugs: Iterable[str]) -> list[Step]:
    """Why each source is fetched: a new edition the lock resolved, never fetched, or
    just a revalidation (a conditional request, usually answered 304)."""
    out = []
    for slug in slugs:
        spec = registry[slug]
        entry = lock.get(slug, {})
        resolved, fetched = entry.get("resolved") or {}, entry.get("fetched") or {}
        if spec.access.type == "manual":
            out.append(Step(slug, "manual: files checked"))
        elif spec.access.type == "curated":
            out.append(Step(slug, "curated: copied from the repository"))
        elif spec.access.type == "api_key" and not os.environ.get(spec.access.env, "").strip():
            out.append(Step(slug, f"api_key: {spec.access.env} not set, skipped"))
        elif not fetched:
            out.append(Step(slug, "never fetched"))
        elif resolved.get("version") and fetched.get("version") not in (
            None,
            resolved["version"],
        ):
            out.append(Step(slug, f"new edition {fetched['version']} → {resolved['version']}"))
        else:
            out.append(Step(slug, "revalidate"))
    return out


def stage_steps(
    lock: dict, registry: dict, nations: Iterable[str], order: Iterable[str], force: bool = False
) -> tuple[list[Step], list[Step]]:
    """(stagers to run, in order; stagers skipped and why)."""
    nations = list(nations)
    todo, skipped = [], []
    planned: set[str] = set()
    for slug in order:
        if not graph.covers(slug, registry, nations):
            skipped.append(Step(slug, "covers no active nation"))
            continue
        if not graph.has_inputs(slug, registry, nations):
            skipped.append(Step(slug, "inputs not fetched"))
            continue
        digest, parts = graph.stage_fingerprint(slug, registry, nations, lock)
        reason = "forced" if force else graph.stale_reason(graph.sidecar_path(slug), digest, parts)
        if not reason:
            reason = nations_changed(graph.read_sidecar(graph.sidecar_path(slug)), nations)
        if not reason:
            # A table this stager reads is re-staged earlier in this run: its sidecar
            # has not moved yet, but the reader is stale all the same (#190)
            upstream = [t for t in STAGE_READS.get(slug, []) if t in planned]
            if upstream:
                reason = f"inputs restaged: {', '.join(upstream[:4])}"
        if reason:
            todo.append(Step(slug, reason))
            planned.add(slug)
    return todo, skipped


def nations_changed(meta: dict, nations: Iterable[str]) -> str | None:
    """A table or indicator set built for another set of nations is stale even when its
    inputs did not move: a stager filters to the nations active when it ran (STATS19
    staged for England and Wales has no Scottish rows). Sidecars without the field
    (built before it was recorded) are left alone."""
    before = meta.get("nations")
    if before is None:
        return None
    now = list(nations)
    if list(before) == now:
        return None
    return f"nations changed: {','.join(before)} → {','.join(now)}"


def indicators_sidecar() -> Path:
    return data_dir("indicators") / "long.meta.json"


def table_feeds(registry: dict, nations: Iterable[str]) -> dict[str, set[str]]:
    """Each stager's own raw inputs, the shared geography left out except for the shared
    tables themselves: the sources by which an indicator is known to read a table."""
    from lix_pipeline.stage import stagers

    nations = list(nations)
    shared = graph.shared_inputs(registry, nations)
    out = {}
    for s in stagers():
        ins = set(graph.direct_inputs(s, registry, nations))
        out[s] = ins if s in graph.SHARED_TABLES else ins - shared
    return out


def indicator_tables(spec: IndicatorSpec, feeds: dict[str, set[str]]) -> list[str]:
    """The staged tables an indicator is built from: the table its builder names, and
    every table fed by one of its sources (``homes`` and ``nspl`` for anything sampled
    at homes)."""
    sources = set(spec.sources)
    slug = (spec.params or {}).get("slug")
    return sorted(t for t, ins in feeds.items() if t == slug or t in sources or ins & sources)


def indicator_fingerprint(
    spec: IndicatorSpec,
    lock: dict,
    feeds: dict[str, set[str]] | None = None,
    sidecars: dict[str, str | None] | None = None,
) -> tuple[str, dict[str, str | None]]:
    """The raw sources' checksums and, with ``feeds``, the fingerprint of each staged
    table the indicator reads: a table re-staged by hand (``lix stage --slug noise``)
    makes its indicators stale whatever the raw checksums say (#215)."""
    digest, parts = graph.fingerprint(spec.sources, lock)
    if feeds is None:
        return digest, parts
    sidecars = {} if sidecars is None else sidecars
    for t in indicator_tables(spec, feeds):
        if t not in sidecars:
            sidecars[t] = graph.read_sidecar(graph.sidecar_path(t)).get("fingerprint")
        parts[f"staged:{t}"] = sidecars[t]
    digest = hashlib.sha256(
        "\n".join(f"{k} {v or 'missing'}" for k, v in sorted(parts.items())).encode()
    ).hexdigest()
    return digest, parts


def indicator_steps(
    lock: dict,
    indicators: Iterable[IndicatorSpec],
    force: bool = False,
    nations: Iterable[str] | None = None,
    registry: dict | None = None,
) -> list[Step]:
    """Indicators whose sources' checksums, or whose staged tables, moved since they
    were built, or every indicator when the table was built for another set of nations."""
    meta = graph.read_sidecar(indicators_sidecar())
    built = meta.get("indicators") or {}
    stale_nations = nations_changed(meta, nations) if nations is not None else None
    feeds = table_feeds(registry, nations) if registry is not None and nations is not None else None
    sidecars: dict[str, str | None] = {}
    out = []
    for spec in indicators:
        digest, parts = indicator_fingerprint(spec, lock, feeds, sidecars)
        before = built.get(spec.id)
        if force:
            out.append(Step(spec.id, "forced"))
        elif not before:
            out.append(Step(spec.id, "never built"))
        elif stale_nations:
            out.append(Step(spec.id, stale_nations))
        elif before.get("fingerprint") != digest:
            was = before.get("inputs") or {}
            changed = sorted(s for s, c in parts.items() if was.get(s) != c)
            raw = [s for s in changed if not s.startswith("staged:")]
            if raw:
                out.append(Step(spec.id, f"sources changed: {', '.join(raw[:4])}"))
            else:
                tables = [s.removeprefix("staged:") for s in changed]
                out.append(Step(spec.id, f"staged inputs changed: {', '.join(tables[:4])}"))
    return out


def restaged_indicator_steps(
    stage: list[Step],
    registry: dict,
    nations: Iterable[str],
    indicators: Iterable[IndicatorSpec],
    planned: set[str],
) -> list[Step]:
    """Indicators that read a table re-staged in this run. A stager runs again when its
    boundaries, its nations or its code moved, not only when its raw file did, and the
    indicator built from its table is then stale whatever the raw checksums say (every
    Northern Irish crime, air and broadband value was missing after v0.5.0's first
    refresh, because police.uk's and Defra's files had not changed). An indicator is
    touched when one of its sources is the stager itself or one of the stager's own raw
    inputs. Not the closure through the tables it reads: every stager that samples at
    homes reads ``homes``, and NSPL, the Irish buildings and the boundaries are in nearly
    every indicator's sources, so one re-staged flood table rebuilt 50 indicators that
    never read it (#205). Those shared inputs count only when ``homes`` or ``nspl``
    itself is re-staged; the readers of a re-staged ``homes`` are planned by
    ``stage_steps`` and cascade through their own inputs here."""
    nations = list(nations)
    shared = graph.shared_inputs(registry, nations)
    touched: dict[str, str] = {}
    for step in stage:
        touched.setdefault(step.name, step.name)
        for slug in graph.direct_inputs(step.name, registry, nations):
            if step.name in graph.SHARED_TABLES or slug not in shared:
                touched.setdefault(slug, step.name)
    out = []
    for spec in indicators:
        if spec.id in planned:
            continue
        stagers = sorted({touched[s] for s in spec.sources if s in touched})
        if stagers:
            out.append(Step(spec.id, f"inputs restaged: {', '.join(stagers[:4])}"))
    return out


def indicator_fingerprints(
    lock: dict,
    indicators: Iterable[IndicatorSpec],
    registry: dict | None = None,
    nations: Iterable[str] | None = None,
) -> dict:
    feeds = table_feeds(registry, nations) if registry is not None and nations is not None else None
    sidecars: dict[str, str | None] = {}
    return {
        spec.id: {"fingerprint": d, "inputs": p}
        for spec in indicators
        for d, p in [indicator_fingerprint(spec, lock, feeds, sidecars)]
    }


def _file_sha(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def serve_sidecar() -> Path:
    return data_dir("serve") / ".meta.json"


def serve_fingerprint(root: Path | None = None) -> tuple[str, dict[str, str | None]]:
    """The indicator table plus the configuration the scores are computed from."""
    root = root or get_project_root()
    parts = {"indicators/long.parquet": _file_sha(data_dir("indicators") / "long.parquet")}
    for name in serve_config_files(root):
        parts[f"config/{name}"] = _file_sha(root / "config" / name)
    digest = hashlib.sha256(
        "\n".join(f"{k} {v or 'missing'}" for k, v in sorted(parts.items())).encode()
    ).hexdigest()
    return digest, parts


def serve_step(force: bool = False, root: Path | None = None) -> Step | None:
    digest, parts = serve_fingerprint(root)
    reason = "forced" if force else graph.stale_reason(serve_sidecar(), digest, parts)
    return Step("score", reason) if reason else None


def tiles_sidecar() -> Path:
    return data_dir("serve") / "tiles" / ".meta.json"


def tiles_step(lock: dict, nations: Iterable[str], force: bool = False) -> Step | None:
    digest, parts = tiles_fingerprint(lock, nations)
    reason = "forced" if force else graph.stale_reason(tiles_sidecar(), digest, parts)
    return Step("tiles", reason) if reason else None


def tiles_fingerprint(lock: dict, nations: Iterable[str]) -> tuple[str, dict]:
    """The boundary files, the nations, and the names: the tiles carry each area's name
    for the hover, read from the scored features, whose names come from the geo_lsoa
    table (the #194 renames shipped in a pack whose tiles still said "Cong/Ross 001")."""
    digest, parts = graph.fingerprint(tile_inputs(), lock)
    parts = {
        **parts,
        "nations": ",".join(nations),
        "staged:geo_lsoa": graph.read_sidecar(graph.sidecar_path("geo_lsoa")).get("fingerprint")
        or "missing",
    }
    digest = hashlib.sha256(
        "\n".join(f"{k} {v or 'missing'}" for k, v in sorted(parts.items())).encode()
    ).hexdigest()
    return digest, parts


def make_plan(
    lock: dict,
    registry: dict,
    nations: Iterable[str],
    stager_order: Iterable[str],
    indicators: Iterable[IndicatorSpec],
    force: bool = False,
    root: Path | None = None,
) -> RefreshPlan:
    nations = list(nations)
    indicators = list(indicators)
    plan = RefreshPlan(nations=nations)
    plan.resolve = slugs_in_scope(registry, nations)
    plan.fetch = fetch_steps(lock, registry, plan.resolve)
    plan.stage, plan.skipped = stage_steps(lock, registry, nations, stager_order, force)
    plan.indicators = indicator_steps(lock, indicators, force, nations=nations, registry=registry)
    plan.indicators += restaged_indicator_steps(
        plan.stage, registry, nations, indicators, {s.name for s in plan.indicators}
    )
    plan.serve = serve_step(force, root)
    plan.tiles = tiles_step(lock, nations, force)
    return plan
