"""What each stager depends on, and whether its output is still current.

Two declarations in ``lix_pipeline.stage`` describe the graph: ``STAGE_INPUTS`` (raw
datasets a stager reads; globs such as ``osm_*`` stand for every registry slug they
match) and ``STAGE_READS`` (staged tables a stager reads, which carry their own raw
inputs with them). ``raw_inputs()`` closes over both, so the fingerprint of a stager is
the checksums of every raw file that can change its output, however indirectly.

Each staged table gets a sidecar, ``data/staged/<slug>.meta.json``, recording that
fingerprint; ``lix refresh`` re-stages a table only when the fingerprint moved or the
sidecar is missing, which makes a refresh resumable and idempotent.

``derived_reads()`` scans a stager's source for the reads the declarations should list,
and a test keeps the two in step.
"""

import ast
import fnmatch
import functools
import hashlib
import inspect
import json
import os
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from lix_core.config import boundary_slugs
from lix_core.paths import data_dir

STAGED_READ = re.compile(r'data_dir\("staged"\)\s*/\s*f?"([a-z0-9_]+)\.parquet"')
RAW_READ = re.compile(r'data_dir\("(?:raw|manual)"\)\s*/\s*"([a-z0-9_]+)"')
# Helpers that read a staged table on the caller's behalf
HELPER_STAGED = {
    "residential_postcodes": "homes",
    "geocode_postcodes": "nspl",
    "load_nspl": "nspl",
    "default_nspl_path": "nspl",
    "oa_to_lsoa": "oa_lookup",
    "broadcast": "geo_lsoa",
}
# Helpers that read a raw dataset on the caller's behalf
# (points_to_lsoa reads every active nation's boundaries: see config/nations.yaml geo:)
HELPER_RAW: dict[str, tuple[str, ...]] = {
    "points_to_lsoa": tuple(boundary_slugs("low")),
    "nspl_document": ("nspl",),
}


def _unwrap(fn: Callable) -> Callable:
    while isinstance(fn, functools.partial):
        fn = fn.func
    return fn


def _is_stage_code(obj) -> bool:
    module = getattr(obj, "__module__", "") or ""
    return inspect.isfunction(obj) and module.startswith("lix_pipeline.stage")


def _referenced_functions(fn: Callable, tree: ast.AST) -> list[Callable]:
    """Functions of the stage package that ``fn``'s body names, directly or as
    ``module.attr`` (so a dispatch table like ``{"geo_ew": geo_ew.build}`` is followed),
    including names the body imports itself."""
    import importlib

    out = []
    scope = dict(fn.__globals__)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            try:
                module = importlib.import_module(node.module)
            except ImportError:
                continue
            for alias in node.names:
                target = getattr(module, alias.name, None)
                if target is None:  # a submodule not imported yet
                    try:
                        target = importlib.import_module(f"{node.module}.{alias.name}")
                    except ImportError:
                        target = None
                scope[alias.asname or alias.name] = target
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Name) and node.id in scope:
            target = scope[node.id]
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = scope.get(node.value.id)
            target = getattr(base, node.attr, None) if inspect.ismodule(base) else None
        if target is not None and _is_stage_code(target) and target is not fn:
            out.append(target)
    return out


def derived_reads(stager: Callable) -> tuple[set[str], set[str]]:
    """(staged tables, raw datasets) a stager reads, found by scanning its source and
    the source of every stage-package function it names, recursively."""
    staged: set[str] = set()
    raw: set[str] = set()
    seen: set[Callable] = set()
    todo = [_unwrap(stager)]
    while todo:
        fn = todo.pop()
        if fn in seen:
            continue
        seen.add(fn)
        try:
            source = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        source = inspect.cleandoc(source) if source[:1].isspace() else source
        staged |= set(STAGED_READ.findall(source))
        raw |= set(RAW_READ.findall(source))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else None
                )
                if name in HELPER_STAGED:
                    staged.add(HELPER_STAGED[name])
                if name in HELPER_RAW:
                    raw.update(HELPER_RAW[name])
        todo.extend(_referenced_functions(fn, tree))
    return staged, raw


def expand_inputs(patterns: Iterable[str], registry: dict) -> list[str]:
    """Registry slugs matching the patterns (a plain slug matches itself even when the
    registry lacks it, so a test registry can be small)."""
    out: list[str] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            out.extend(s for s in registry if fnmatch.fnmatch(s, pattern))
        else:
            out.append(pattern)
    return sorted(dict.fromkeys(out))


def raw_inputs(stager: str, registry: dict) -> list[str]:
    """Every raw dataset that can change a stager's output: its own inputs and, through
    ``STAGE_READS``, those of the staged tables it reads."""
    from lix_pipeline.stage import STAGE_INPUTS, STAGE_READS

    out: set[str] = set()
    seen: set[str] = set()
    todo = [stager]
    while todo:
        s = todo.pop()
        if s in seen:
            continue
        seen.add(s)
        out.update(expand_inputs(STAGE_INPUTS.get(s, [s]), registry))
        todo.extend(STAGE_READS.get(s, []))
    return sorted(out)


def inputs_for(stager: str, registry: dict, nations: Iterable[str]) -> list[str]:
    """``raw_inputs`` narrowed to the datasets covering any of ``nations`` (a Welsh
    noise map is no input of an England-only build)."""
    active = set(nations)
    return [
        s
        for s in raw_inputs(stager, registry)
        if s not in registry or active & set(registry[s].coverage)
    ]


def has_inputs(stager: str, registry: dict, nations: Iterable[str]) -> bool:
    """Every raw input the stager needs for these nations is on disk (a manual source
    counts when its files sit in data/manual/)."""

    def present(slug: str) -> bool:
        spec = registry.get(slug)
        if spec is not None and spec.access.type == "manual":
            return any(p.is_file() for p in (data_dir("manual") / slug).glob("**/*"))
        if (data_dir("raw") / slug / ".meta.json").exists():
            return True
        # A keyed source without its key is skipped at fetch time, and its stager reads
        # what it has (chargers without Open Charge Map, Logainm as an empty table): it
        # does not hold the stage back. With the key set, a missing file is a failed
        # fetch, which does
        return (
            spec is not None
            and spec.access.type == "api_key"
            and not os.environ.get(spec.access.env, "").strip()
        )

    return all(present(s) for s in inputs_for(stager, registry, nations))


def covers(stager: str, registry: dict, nations: Iterable[str]) -> bool:
    """False when none of the stager's own registered inputs covers any of the nations
    (the Welsh council-tax stager in an England-only build)."""
    from lix_pipeline.stage import STAGE_INPUTS

    own = [s for s in expand_inputs(STAGE_INPUTS.get(stager, [stager]), registry) if s in registry]
    active = set(nations)
    return not own or any(active & set(registry[s].coverage) for s in own)


def source_checksum(slug: str, lock: dict) -> str | None:
    """The lockfile's checksum of a raw dataset: ``fetched.sha256``, or for a manual
    source the checksums of its files."""
    fetched = lock.get(slug, {}).get("fetched") or {}
    if fetched.get("sha256"):
        return fetched["sha256"]
    if fetched.get("files"):
        joined = "\n".join(f"{k} {v}" for k, v in sorted(fetched["files"].items()))
        return hashlib.sha256(joined.encode()).hexdigest()
    return None


def fingerprint(inputs: Iterable[str], lock: dict) -> tuple[str, dict[str, str | None]]:
    """A digest over the inputs' lockfile checksums, and the checksums themselves."""
    parts = {s: source_checksum(s, lock) for s in sorted(inputs)}
    digest = hashlib.sha256(
        "\n".join(f"{s} {c or 'missing'}" for s, c in parts.items()).encode()
    ).hexdigest()
    return digest, parts


def sidecar_path(slug: str) -> Path:
    return data_dir("staged") / f"{slug}.meta.json"


def read_sidecar(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def write_sidecar(path: Path, name: str, digest: str, inputs: dict, **extra) -> dict:
    meta = {
        "name": name,
        "fingerprint": digest,
        "inputs": inputs,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def record_staged(
    slug: str, registry: dict, nations: Iterable[str], lock: dict | None = None
) -> dict:
    """Write the sidecar of a table that has just been staged."""
    import polars as pl

    if lock is None:
        from lix_pipeline.fetch.lock import read_lock

        lock = read_lock()
    nations = list(nations)
    digest, parts = fingerprint(inputs_for(slug, registry, nations), lock)
    table = data_dir("staged") / f"{slug}.parquet"
    rows = pl.scan_parquet(table).select(pl.len()).collect().item() if table.exists() else None
    return write_sidecar(sidecar_path(slug), slug, digest, parts, nations=nations, rows=rows)


def stale_reason(path: Path, digest: str, parts: dict[str, str | None]) -> str | None:
    """Why an output must be rebuilt, or None when its sidecar matches the inputs."""
    meta = read_sidecar(path)
    if not meta:
        return "never built"
    if meta.get("fingerprint") == digest:
        return None
    before = meta.get("inputs") or {}
    changed = sorted(s for s, c in parts.items() if before.get(s) != c)
    return f"inputs changed: {', '.join(changed[:4])}" + (" …" if len(changed) > 4 else "")
