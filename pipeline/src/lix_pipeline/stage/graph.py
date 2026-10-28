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


def _stage_functions(stager: Callable) -> list[tuple[Callable, str]]:
    """The stager and every stage-package function it names, recursively, each with its
    source: the code a staged table is built by."""
    out: list[tuple[Callable, str]] = []
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
        out.append((fn, source))
        todo.extend(_referenced_functions(fn, ast.parse(source)))
    return out


def derived_reads(stager: Callable) -> tuple[set[str], set[str]]:
    """(staged tables, raw datasets) a stager reads, found by scanning its source and
    the source of every stage-package function it names, recursively."""
    staged: set[str] = set()
    raw: set[str] = set()
    for _fn, source in _stage_functions(stager):
        staged |= set(STAGED_READ.findall(source))
        raw |= set(RAW_READ.findall(source))
        for node in ast.walk(ast.parse(source)):
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
    return staged, raw


@functools.lru_cache(maxsize=1)
def _stager_table() -> dict[str, Callable]:
    from lix_pipeline.stage import stagers

    return stagers()


def _stable_repr(value) -> str | None:
    """A module constant as text, the same in every process: dicts in their order, sets
    sorted, nested values alike; None for anything that is not plain data."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        parts = [_stable_repr(v) for v in value]
        return None if None in parts else f"[{', '.join(parts)}]"
    if isinstance(value, (set, frozenset)):
        parts = [_stable_repr(v) for v in value]
        return None if None in parts else f"{{{', '.join(sorted(parts))}}}"
    if isinstance(value, dict):
        parts = [(_stable_repr(k), _stable_repr(v)) for k, v in value.items()]
        if any(k is None or v is None for k, v in parts):
            return None
        return "{" + ", ".join(f"{k}: {v}" for k, v in parts) + "}"
    return None


def _constants_named(fn: Callable, source: str) -> list[tuple[str, str]]:
    """The module-level constants (plain data: NOISE_SOURCES, BORDER_M, the contour
    bands) that ``fn``'s body names, with their values as text: a table built under
    other constants is built by other code."""
    out = []
    names = {n.id for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Name)}
    for name in sorted(names):
        if not name.isupper() or name not in fn.__globals__:
            continue
        text = _stable_repr(fn.__globals__[name])
        if text is not None:
            out.append((f"{fn.__module__}.{name}", text))
    return out


@functools.lru_cache(maxsize=None)
def _code_hash(slug: str) -> str:
    fn = _stager_table().get(slug)
    if fn is None:
        return "missing"
    h = hashlib.sha256()
    for f, source in sorted(
        _stage_functions(fn), key=lambda x: (x[0].__module__, x[0].__qualname__)
    ):
        h.update(f"{f.__module__}.{f.__qualname__}\n{source}\n".encode())
        for name, text in _constants_named(f, source):
            h.update(f"{name} = {text}\n".encode())
    return h.hexdigest()


def stager_code_hash(slug: str) -> str:
    """A digest of the stager's source, of every stage-package function it calls and of
    the module constants those name (a file pattern or a band table is code too: the
    Belfast International pattern fix did not cascade until it was), so a table built
    by older code reads as stale (#190). Helpers outside the stage package (geo/*.py)
    are left out on purpose: a fix there would re-stage every table for hours, and
    ``lix stage --slug X --with-dependents`` or ``lix refresh --force`` is the
    developer's call."""
    return _code_hash(slug)


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


def direct_inputs(stager: str, registry: dict, nations: Iterable[str]) -> list[str]:
    """The stager's own raw inputs (``STAGE_INPUTS``), not those of the tables it reads,
    narrowed to ``nations`` (#205)."""
    from lix_pipeline.stage import STAGE_INPUTS

    active = set(nations)
    return [
        s
        for s in expand_inputs(STAGE_INPUTS.get(stager, [stager]), registry)
        if s not in registry or active & set(registry[s].coverage)
    ]


# The tables every home-based table is built on: their raw inputs (NSPL, the Irish
# buildings, the low boundaries) sit in nearly every indicator's sources, so they say
# nothing about which indicator reads a re-staged table (#205)
SHARED_TABLES = ("homes", "nspl")


def shared_inputs(registry: dict, nations: Iterable[str]) -> set[str]:
    nations = list(nations)
    return {s for t in SHARED_TABLES for s in direct_inputs(t, registry, nations)}


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


def stage_fingerprint(
    slug: str, registry: dict, nations: Iterable[str], lock: dict
) -> tuple[str, dict[str, str | None]]:
    """A stager's digest: the lockfile checksums of its raw inputs (``fingerprint``), the
    sidecar fingerprint of each staged table it reads directly, and the hash of its code.
    A table re-staged upstream, or a stager rewritten, cascades through its readers; a
    table re-staged by hand from the same inputs and code yields the same digest and
    cascades nowhere."""
    from lix_pipeline.stage import STAGE_READS

    _, parts = fingerprint(inputs_for(slug, registry, nations), lock)
    for table in STAGE_READS.get(slug, []):
        parts[f"staged:{table}"] = read_sidecar(sidecar_path(table)).get("fingerprint") or "missing"
    parts["code"] = stager_code_hash(slug)
    digest = hashlib.sha256(
        "\n".join(f"{k} {v or 'missing'}" for k, v in sorted(parts.items())).encode()
    ).hexdigest()
    return digest, parts


def dependents(slug: str, order: Iterable[str]) -> list[str]:
    """Every stager that reads ``slug``'s table, directly or through another staged
    table, in run order."""
    from lix_pipeline.stage import STAGE_READS

    out: set[str] = set()
    todo = [slug]
    while todo:
        s = todo.pop()
        for reader, reads in STAGE_READS.items():
            if s in reads and reader not in out:
                out.add(reader)
                todo.append(reader)
    return [s for s in order if s in out]


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
    digest, parts = stage_fingerprint(slug, registry, nations, lock)
    table = data_dir("staged") / f"{slug}.parquet"
    rows = pl.scan_parquet(table).select(pl.len()).collect().item() if table.exists() else None
    return write_sidecar(sidecar_path(slug), slug, digest, parts, nations=nations, rows=rows)


def rehash_sidecars(
    order: Iterable[str], registry: dict, nations: Iterable[str], lock: dict
) -> tuple[list[str], dict[str, str]]:
    """Rewrite every existing sidecar's fingerprint under the current scheme without
    re-staging, for a planner upgrade: the tables on disk are taken as built by the
    current code from the inputs the lockfile names. In run order, so a reader sees its
    inputs' new fingerprints.

    A sidecar that records a raw checksum or a code hash other than today's is left
    alone: its table really is stale, and rewriting it would hide the cascade (#215; on
    27 October 2026 a rehash after a stager edit made the re-stage that followed plan
    nothing downstream). Returns the slugs rewritten and, per slug left alone, what moved."""
    nations = list(nations)
    done: list[str] = []
    refused: dict[str, str] = {}
    for slug in order:
        path = sidecar_path(slug)
        meta = read_sidecar(path)
        if not meta:
            continue
        digest, parts = stage_fingerprint(slug, registry, nations, lock)
        before = meta.get("inputs") or {}
        moved = sorted(
            k for k, v in before.items()
            if not k.startswith("staged:") and k in parts and parts[k] != v
        )  # fmt: skip
        if moved:
            refused[slug] = ", ".join(moved[:4])
            continue
        extra = {k: v for k, v in meta.items() if k not in ("name", "fingerprint", "inputs")}
        write_sidecar(path, slug, digest, parts, **extra)
        done.append(slug)
    return done, refused


def stale_reason(path: Path, digest: str, parts: dict[str, str | None]) -> str | None:
    """Why an output must be rebuilt, or None when its sidecar matches the inputs."""
    meta = read_sidecar(path)
    if not meta:
        return "never built"
    if meta.get("fingerprint") == digest:
        return None
    before = meta.get("inputs") or {}
    changed = sorted(s for s, c in parts.items() if before.get(s) != c)
    raw = [s for s in changed if s != "code" and not s.startswith("staged:")]
    staged = [s[len("staged:") :] for s in changed if s.startswith("staged:")]
    reasons = []
    if raw:
        reasons.append(f"inputs changed: {', '.join(raw[:4])}" + (" …" if len(raw) > 4 else ""))
    if staged:
        reasons.append(
            f"staged inputs changed: {', '.join(staged[:4])}" + (" …" if len(staged) > 4 else "")
        )
    if "code" in changed:
        reasons.append("code changed")
    return "; ".join(reasons) or "fingerprint changed"
