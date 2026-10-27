"""Run a refresh: resolve, fetch, stage, indicators, scores, tiles, validators.

Each phase records its sidecars as it goes, so a run killed half way resumes where it
stopped and a second run after a complete one finds nothing to do. A source that fails
to resolve or fetch stops the run before anything is rebuilt: a publisher that moved or
reshaped a file should be looked at, not built around. ``keep_going`` is for the other
case, a publisher's maintenance window: the run builds from the editions the lock names
and lists the sources that kept them (#204).
"""

import json
import time
from datetime import datetime, timezone

from lix_core.codes import active_nations
from lix_core.config import load_indicators, load_registry
from lix_core.log import setup_logging
from lix_core.paths import data_dir
from lix_pipeline.fetch import ManualDownloadRequired, fetch, resolve_and_lock
from lix_pipeline.fetch.lock import read_lock
from lix_pipeline.fetch.session import make_session
from lix_pipeline.refresh import plan as planning
from lix_pipeline.refresh.plan import RefreshPlan, Step
from lix_pipeline.stage import graph, save_staged, stagers

logger = setup_logging("refresh")


class RefreshFailed(Exception):
    """A phase failed; the log says which sources."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _log_path() -> str:
    path = data_dir("logs") / f"refresh-{_stamp()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def current_plan(force: bool = False) -> RefreshPlan:
    """The plan from the lockfile and sidecars as they are now (no network)."""
    catalogue, registry = load_indicators(), load_registry()
    return planning.make_plan(
        read_lock(), registry, active_nations(), list(stagers()), catalogue.indicators, force
    )


def refresh(
    dry_run: bool = False, force: bool = False, skip_fetch: bool = False, keep_going: bool = False
) -> dict:
    """Bring data/ up to date. Returns the run log (also written to data/logs/)."""
    started = time.monotonic()
    catalogue, registry = load_indicators(), load_registry()
    nations = list(active_nations())
    log: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nations": nations,
        "dry_run": dry_run,
        "keep_going": keep_going,
        "phases": {},
        "changed_sources": [],
        "failures": [],
    }

    def phase(name: str, t0: float, **extra) -> None:
        log["phases"][name] = {"seconds": round(time.monotonic() - t0, 1), **extra}

    session = make_session()
    slugs = planning.slugs_in_scope(registry, nations)

    # 1. Resolve: which edition of each source exists upstream now
    t0 = time.monotonic()
    new_editions = []
    unresolved: set[str] = set()
    if not skip_fetch:
        before = read_lock()
        for slug in slugs:
            if registry[slug].access.type == "manual":
                continue
            try:
                resolved = resolve_and_lock(slug, registry[slug], session)
            except Exception as e:  # keep going, report at the end
                log["failures"].append({"phase": "resolve", "slug": slug, "error": str(e)})
                unresolved.add(slug)
                continue
            old = before.get(slug, {}).get("resolved", {}).get("version")
            if old != resolved.get("version"):
                new_editions.append(slug)
    phase("resolve", t0, new_editions=new_editions)

    plan = planning.make_plan(
        read_lock(), registry, nations, list(stagers()), catalogue.indicators, force
    )
    log["plan"] = plan.to_dict()
    if dry_run:
        print(plan.render())
        _finish(log, started)
        return log

    # 2. Fetch: new editions download, the rest are revalidated
    t0 = time.monotonic()
    if not skip_fetch:
        for slug in slugs:
            if slug in unresolved:
                continue  # its edition is unknown tonight: the raw file and the lock entry stand
            try:
                result = fetch(slug, session=session, registry=registry, revalidate=True)
            except ManualDownloadRequired as e:
                logger.warning(str(e))
                continue
            except Exception as e:
                logger.exception(f"[{slug}] Fetch failed")
                log["failures"].append({"phase": "fetch", "slug": slug, "error": str(e)})
                continue
            if result.get("changed"):
                log["changed_sources"].append(slug)
    phase("fetch", t0, changed=list(log["changed_sources"]))
    if log["failures"]:
        names = sorted({f["slug"] for f in log["failures"]})
        if not keep_going:
            _finish(log, started)
            raise RefreshFailed(f"{len(names)} source(s) failed to resolve or fetch: {names}")
        # A publisher's maintenance window should not block the monthly rebuild: these
        # sources keep the editions the lock names, which their staged tables were built
        # from, and the run says so at the end (#204)
        log["kept"] = names
        logger.warning(f"{len(names)} source(s) keep their locked editions: {names}")

    # The lock now holds the checksums the rest of the run is fingerprinted against
    lock = read_lock()
    plan = planning.make_plan(lock, registry, nations, list(stagers()), catalogue.indicators, force)
    log["plan"] = plan.to_dict()
    print(plan.render())

    # 3. Stage
    t0 = time.monotonic()
    available = stagers()
    staged = []
    for step in plan.stage:
        logger.info(f"[{step.name}] staging: {step.reason}")
        save_staged(available[step.name](), step.name)
        graph.record_staged(step.name, registry, nations, lock)
        staged.append(step.name)
    phase("stage", t0, staged=staged)

    # 4. Indicators (only the stale ones; the rest are kept from the existing table)
    t0 = time.monotonic()
    rebuilt = [s.name for s in plan.indicators]
    if rebuilt:
        from lix_pipeline.indicators import build_all

        build_all(only=rebuilt if len(rebuilt) < len(catalogue.indicators) else None)
        graph.write_sidecar(
            planning.indicators_sidecar(),
            "indicators",
            "",
            {},
            indicators=planning.indicator_fingerprints(lock, catalogue.indicators),
            nations=list(nations),
        )
    phase("indicators", t0, rebuilt=rebuilt)

    # 5. Scores and the manifest, when the indicator table or the configuration moved
    t0 = time.monotonic()
    serve = planning.serve_step(force)
    if serve:
        from lix_pipeline.qa.report import write_report
        from lix_pipeline.serve.scores import build_serve

        logger.info(f"[score] {serve.reason}")
        build_serve()
        for problem in write_report():
            logger.warning(f"QA: {problem}")
        digest, parts = planning.serve_fingerprint()
        graph.write_sidecar(planning.serve_sidecar(), "serve", digest, parts)
    phase("score", t0, rebuilt=bool(serve))

    # 6. Tiles, when the boundaries or the nations changed
    t0 = time.monotonic()
    tiles = planning.tiles_step(lock, nations, force)
    if tiles:
        from lix_pipeline.serve.tiles import build_tiles

        logger.info(f"[tiles] {tiles.reason}")
        build_tiles()
        digest, parts = planning.tiles_fingerprint(lock, nations)
        graph.write_sidecar(planning.tiles_sidecar(), "tiles", digest, parts)
    phase("tiles", t0, rebuilt=bool(tiles))

    # 7. Validators
    t0 = time.monotonic()
    from lix_pipeline.qa.validate import VALIDATORS

    problems = {}
    checks = ["serve", "places"] + (["geo"] if "geo_lsoa" in staged else [])
    for target in checks:
        found = VALIDATORS[target]()
        if found:
            problems[target] = found
            for p in found:
                logger.error(f"validate {target}: {p}")
    phase("validate", t0, problems=problems)
    if problems:
        log["failures"].append({"phase": "validate", "problems": problems})

    if log.get("kept"):
        logger.warning(
            f"Built around {len(log['kept'])} source(s) that kept their locked editions: "
            f"{log['kept']}; see the refresh log"
        )
    _finish(log, started)
    if problems:
        raise RefreshFailed(f"validation failed: {sorted(problems)}")
    return log


def _finish(log: dict, started: float) -> None:
    log["seconds"] = round(time.monotonic() - started, 1)
    log["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = _log_path()
    with open(path, "w") as f:
        json.dump(log, f, indent=2, default=str)
    logger.info(f"Refresh log: {path} ({log['seconds']}s)")


__all__ = ["refresh", "current_plan", "RefreshFailed", "Step"]
