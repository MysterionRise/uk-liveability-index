"""``lix`` command line.

lix resolve --all              # pin every dataset's current URL in datasets.lock.json
lix resolve --check --all      # is every source still reachable? (nightly link check)
lix fetch --theme geography    # download what the lockfile pins
lix stage --all                # every stager whose inputs are fetched
lix validate config            # every theme has enough indicators in each active nation
lix validate geo               # check the geography backbone
lix validate serve             # check data/serve (or --dir another serve directory)
lix validate places            # anchor checks of the face-validity review
lix qa places                  # write docs/validation.md
lix refresh --dry-run          # what a refresh would rebuild; lix refresh does it
lix demo-data --out fixtures/demo   # small dataset for CI/demos (nations.yaml demo_upper)
"""

import argparse
import sys

from lix_core.config import load_registry
from lix_core.log import setup_logging
from lix_core.paths import data_dir, ensure_dirs
from lix_pipeline.fetch import (
    ManualDownloadRequired,
    check_source,
    fetch,
    resolve_and_lock,
    select,
)
from lix_pipeline.fetch.session import make_session
from lix_pipeline.stage import save_staged, stagers

logger = setup_logging("cli")


def _selected(args: argparse.Namespace) -> list[str]:
    return select(
        load_registry(),
        slugs=[args.slug] if args.slug else None,
        theme=args.theme,
        priority=args.priority,
    )


def _resolve(args: argparse.Namespace) -> int:
    registry, session = load_registry(), make_session()
    failures = warnings = 0
    results = []
    for slug in _selected(args):
        if args.check:
            result = check_source(slug, registry[slug], session)
            results.append(result)
            status, note = result["status"], result["note"]
            print(f"{'FAIL' if status == 'fail' else status:4} {slug:24} {note}")
            failures += status == "fail"
            warnings += status == "warn"
        else:
            try:
                resolved = resolve_and_lock(slug, registry[slug], session)
            except Exception as e:  # report and keep going; exit status says it failed
                print(f"FAIL {slug:24} {e}")
                failures += 1
                continue
            print(f"{slug:24} {resolved.get('title') or ''} {resolved.get('version') or ''}")
    if args.check:
        print(f"{failures} failed, {warnings} not verified from this network")
        if args.json:
            import json
            from datetime import datetime, timezone

            with open(args.json, "w") as f:
                json.dump(
                    {
                        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "results": results,
                    },
                    f,
                    indent=2,
                )
            print(f"wrote {args.json}")
    return 1 if failures else 0


def _fetch(args: argparse.Namespace) -> int:
    registry, session = load_registry(), make_session()
    missing_manual, failed, changed = [], [], []
    for slug in _selected(args):
        try:
            result = fetch(
                slug,
                force=args.force,
                strict=args.strict,
                session=session,
                registry=registry,
                revalidate=args.revalidate,
            )
            if result.get("changed"):
                changed.append(slug)
        except ManualDownloadRequired as e:
            missing_manual.append(str(e))
        except Exception:
            logger.exception(f"[{slug}] Fetch failed")
            failed.append(slug)
    for msg in missing_manual:
        logger.warning(msg)
    if args.revalidate:
        logger.info(f"Changed since the lockfile: {changed or 'nothing'}")
    if failed:
        logger.error(f"Failed: {failed}")
    return 1 if failed else 0


def _stage(args: argparse.Namespace) -> int:
    from lix_core.codes import active_nations
    from lix_pipeline.stage import graph

    available = stagers()
    if args.slug and args.slug not in available:
        raise SystemExit(f"Unknown stager: {args.slug!r}. Available: {list(available)}")

    registry, nations = load_registry(), list(active_nations())
    failed = []
    for slug in [args.slug] if args.slug else list(available):
        if args.all and not graph.has_inputs(slug, registry, nations):
            logger.info(f"[{slug}] Inputs not fetched — skipping")
            continue
        if args.all and not graph.covers(slug, registry, nations):
            logger.info(f"[{slug}] Its sources cover none of the active nations — skipping")
            continue
        try:
            save_staged(available[slug](), slug)
        except Exception as e:  # noqa: BLE001 (one stager's failure must not stop the rest)
            if not args.all:
                raise
            logger.exception(f"[{slug}] Stage failed: {e}")
            failed.append(slug)
            continue
        graph.record_staged(slug, registry, nations)
    if failed:
        logger.error(f"{len(failed)} stager(s) failed: {failed}")
        return 1
    return 0


def _validate(args: argparse.Namespace) -> int:
    from pathlib import Path

    from lix_pipeline.qa.validate import VALIDATORS

    if args.target == "serve" and args.dir:
        problems = VALIDATORS["serve"](Path(args.dir))
    else:
        problems = VALIDATORS[args.target]()
    for p in problems:
        print(f"FAIL {p}")
    print(f"{args.target}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


def _demo_data(args: argparse.Namespace) -> int:
    from pathlib import Path

    from lix_core.codes import active_nations
    from lix_core.config import load_nations
    from lix_pipeline.qa.validate import validate_serve
    from lix_pipeline.serve.demo import build_demo

    cfg = load_nations().nations
    lads = args.lad or [c for n in active_nations() for c in cfg[n].demo_upper]
    out = Path(args.out)
    build_demo(lads, out)
    problems = validate_serve(out / "serve")
    for p in problems:
        print(f"FAIL {p}")
    return 1 if problems else 0


def _qa(args: argparse.Namespace) -> int:
    from lix_pipeline.qa.places import anchor_problems, review, write_review

    path = write_review()
    results = review()
    checks = [c for r in results for c in r.checks]
    print(f"{sum(c.passed for c in checks)}/{len(checks)} checks pass; wrote {path}")
    for p in anchor_problems(results):
        print(f"ANCHOR FAIL {p}")
    return 0


def _indicators(args: argparse.Namespace) -> int:
    from lix_pipeline.indicators import build_all

    build_all(only=args.only.split(",") if args.only else None)
    return 0


def _score(args: argparse.Namespace) -> int:
    from lix_pipeline.qa.report import write_report
    from lix_pipeline.refresh import plan as planning
    from lix_pipeline.serve.scores import build_serve
    from lix_pipeline.stage import graph

    build_serve()
    problems = write_report()
    for p in problems:
        logger.warning(f"QA: {p}")
    logger.info(f"QA report: {data_dir('serve') / 'qa.md'} ({len(problems)} warning(s))")
    # The sidecar says what the scores were built from, as `lix refresh` records it, so
    # the next plan knows they are current
    digest, parts = planning.serve_fingerprint()
    graph.write_sidecar(planning.serve_sidecar(), "serve", digest, parts)
    return 0


def _tiles(args: argparse.Namespace) -> int:
    from lix_core.codes import active_nations
    from lix_pipeline.fetch.lock import read_lock
    from lix_pipeline.refresh import plan as planning
    from lix_pipeline.serve.tiles import build_tiles
    from lix_pipeline.stage import graph

    build_tiles()
    digest, parts = planning.tiles_fingerprint(read_lock(), list(active_nations()))
    graph.write_sidecar(planning.tiles_sidecar(), "tiles", digest, parts)
    return 0


def _refresh(args: argparse.Namespace) -> int:
    from lix_pipeline.refresh.run import RefreshFailed, refresh

    try:
        refresh(dry_run=args.dry_run, force=args.force, skip_fetch=args.skip_fetch)
    except RefreshFailed as e:
        logger.error(str(e))
        return 1
    return 0


def _refresh_issue(args: argparse.Namespace) -> int:
    """Write the nightly "Data refresh due" issue body; print pending / nothing."""
    import json
    from pathlib import Path

    from lix_pipeline.fetch.lock import read_lock
    from lix_pipeline.refresh.report import issue_body

    checks = json.loads(Path(args.check).read_text())
    body, pending = issue_body(checks, read_lock(), load_registry())
    Path(args.out).write_text(body)
    print("pending" if pending else "nothing")
    return 0


def _data_release(args: argparse.Namespace) -> int:
    """Stamp the data release's tag into the manifest and write its release notes."""
    import json
    from pathlib import Path

    from lix_pipeline.refresh.notes import release_notes

    manifest_path = data_dir("serve") / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["data_release"] = args.tag
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    previous = json.loads(Path(args.previous).read_text()) if args.previous else None
    out = Path(args.notes)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(release_notes(manifest, previous, args.tag))
    print(f"stamped {manifest_path} with {args.tag}; wrote {out}")
    return 0


def _docs(args: argparse.Namespace) -> int:
    from lix_pipeline.docs import write_docs

    for path in write_docs():
        print(f"wrote {path}")
    return 0


def _add_selection(parser: argparse.ArgumentParser) -> None:
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--slug", help="One dataset")
    which.add_argument("--theme", help="Every dataset in a theme (e.g. geography)")
    which.add_argument("--priority", choices=["P0", "P1", "P2"], help="Every dataset of a priority")
    which.add_argument("--all", action="store_true", help="Every dataset")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lix", description="UK Liveability Index pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve", help="Pin each dataset's current URL in the lockfile")
    _add_selection(resolve)
    resolve.add_argument(
        "--check", action="store_true", help="Only check sources are reachable; exit 1 if not"
    )
    resolve.add_argument("--json", help="--check: also write the results to this file")
    resolve.set_defaults(func=_resolve)

    fetch_p = sub.add_parser("fetch", help="Download datasets into data/raw/")
    _add_selection(fetch_p)
    fetch_p.add_argument("--force", action="store_true", help="Re-download even if cached")
    fetch_p.add_argument(
        "--strict", action="store_true", help="Fail if a file differs from the lockfile checksum"
    )
    fetch_p.add_argument(
        "--revalidate",
        action="store_true",
        help="Check files already on disk against the server (conditional requests)",
    )
    fetch_p.set_defaults(func=_fetch)

    stage = sub.add_parser("stage", help="Stage raw datasets to data/staged/*.parquet")
    which = stage.add_mutually_exclusive_group(required=True)
    which.add_argument("--slug", help="One stager")
    which.add_argument("--all", action="store_true", help="Every stager whose inputs are fetched")
    stage.set_defaults(func=_stage)

    validate = sub.add_parser("validate", help="Check staged and serve outputs")
    validate.add_argument("target", choices=["config", "geo", "serve", "places"])
    validate.add_argument("--dir", help="serve: the directory to check (default data/serve)")
    validate.set_defaults(func=_validate)

    qa = sub.add_parser("qa", help="Quality reviews of the built data")
    qa.add_argument("target", choices=["places"])
    qa.set_defaults(func=_qa)

    demo = sub.add_parser("demo-data", help="Cut a small serve dataset from a full build")
    demo.add_argument(
        "--lad",
        nargs="+",
        help="Upper-tier area codes to keep (default: each active nation's demo_upper)",
    )
    demo.add_argument("--out", default="fixtures/demo", help="Writes <out>/serve/")
    demo.set_defaults(func=_demo_data)

    indicators = sub.add_parser("indicators", help="Build data/indicators/long.parquet")
    indicators.add_argument("--only", help="Comma-separated indicator ids to rebuild")
    indicators.set_defaults(func=_indicators)

    score = sub.add_parser("score", help="Write data/serve/ (features, scores, manifest)")
    score.set_defaults(func=_score)

    tiles = sub.add_parser("tiles", help="Write map tiles to data/serve/tiles/*.pmtiles")
    tiles.set_defaults(func=_tiles)

    refresh = sub.add_parser(
        "refresh", help="Resolve, fetch and rebuild only what changed (see refresh/)"
    )
    refresh.add_argument("--dry-run", action="store_true", help="Resolve, then print the plan")
    refresh.add_argument("--force", action="store_true", help="Rebuild every stage")
    refresh.add_argument(
        "--skip-fetch", action="store_true", help="Neither resolve nor fetch; rebuild from data/raw"
    )
    refresh.set_defaults(func=_refresh)

    refresh_issue = sub.add_parser(
        "refresh-issue", help='Write the nightly "Data refresh due" issue from a --check file'
    )
    refresh_issue.add_argument("--check", required=True, help="lix resolve --check --json output")
    refresh_issue.add_argument("--out", default="body.md")
    refresh_issue.set_defaults(func=_refresh_issue)

    data_release = sub.add_parser(
        "data-release", help="Stamp a data release tag into the manifest; write its notes"
    )
    data_release.add_argument("--tag", required=True, help="e.g. data-2026-10")
    data_release.add_argument("--previous", help="The previous data release's manifest.json")
    data_release.add_argument("--notes", default="dist/data-release-notes.md")
    data_release.set_defaults(func=_data_release)

    docs = sub.add_parser("docs", help="Regenerate docs/data-sources.md and ATTRIBUTION.md")
    docs.set_defaults(func=_docs)

    args = parser.parse_args(argv)
    ensure_dirs()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
