"""What a refresh has to do, decided from the lockfile and the sidecars.

Everything here is a pure function of its arguments, so ``--dry-run`` can print the
plan and the tests can exercise it without data. The sidecars are:

    data/staged/<slug>.meta.json       one per staged table (stage.graph)
    data/indicators/long.meta.json     one entry per indicator
    data/serve/.meta.json              the scores and manifest
    data/serve/tiles/.meta.json        the map tiles
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from lix_core.config import IndicatorSpec
from lix_core.paths import data_dir, get_project_root
from lix_pipeline.stage import graph

# Serve files depend on these configuration files as well as on the indicator table
SERVE_CONFIG = ("indicators.yaml", "weights.yaml", "nations.yaml")
TILE_INPUTS = ("lsoa_boundaries", "msoa_boundaries", "lad_boundaries")


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
    for slug in order:
        if not graph.covers(slug, registry, nations):
            skipped.append(Step(slug, "covers no active nation"))
            continue
        if not graph.has_inputs(slug, registry, nations):
            skipped.append(Step(slug, "inputs not fetched"))
            continue
        digest, parts = graph.fingerprint(graph.inputs_for(slug, registry, nations), lock)
        reason = "forced" if force else graph.stale_reason(graph.sidecar_path(slug), digest, parts)
        if reason:
            todo.append(Step(slug, reason))
    return todo, skipped


def indicators_sidecar() -> Path:
    return data_dir("indicators") / "long.meta.json"


def indicator_steps(
    lock: dict, indicators: Iterable[IndicatorSpec], force: bool = False
) -> list[Step]:
    """Indicators whose sources' checksums moved since they were built."""
    meta = graph.read_sidecar(indicators_sidecar())
    built = meta.get("indicators") or {}
    out = []
    for spec in indicators:
        digest, parts = graph.fingerprint(spec.sources, lock)
        before = built.get(spec.id)
        if force:
            out.append(Step(spec.id, "forced"))
        elif not before:
            out.append(Step(spec.id, "never built"))
        elif before.get("fingerprint") != digest:
            changed = sorted(
                s for s, c in parts.items() if (before.get("inputs") or {}).get(s) != c
            )
            out.append(Step(spec.id, f"sources changed: {', '.join(changed[:4])}"))
    return out


def indicator_fingerprints(lock: dict, indicators: Iterable[IndicatorSpec]) -> dict:
    return {
        spec.id: {"fingerprint": d, "inputs": p}
        for spec in indicators
        for d, p in [graph.fingerprint(spec.sources, lock)]
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
    for name in SERVE_CONFIG:
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
    digest, parts = graph.fingerprint(TILE_INPUTS, lock)
    parts = {**parts, "nations": ",".join(nations)}
    digest = hashlib.sha256((digest + parts["nations"]).encode()).hexdigest()
    reason = "forced" if force else graph.stale_reason(tiles_sidecar(), digest, parts)
    return Step("tiles", reason) if reason else None


def tiles_fingerprint(lock: dict, nations: Iterable[str]) -> tuple[str, dict]:
    digest, parts = graph.fingerprint(TILE_INPUTS, lock)
    parts = {**parts, "nations": ",".join(nations)}
    return hashlib.sha256((digest + parts["nations"]).encode()).hexdigest(), parts


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
    plan = RefreshPlan(nations=nations)
    plan.resolve = slugs_in_scope(registry, nations)
    plan.fetch = fetch_steps(lock, registry, plan.resolve)
    plan.stage, plan.skipped = stage_steps(lock, registry, nations, stager_order, force)
    plan.indicators = indicator_steps(lock, indicators, force)
    plan.serve = serve_step(force, root)
    plan.tiles = tiles_step(lock, nations, force)
    return plan
