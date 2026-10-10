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
lix demo-data --lad E08000035 E06000043 --out fixtures/demo   # small dataset for CI/demos
"""

import argparse
import sys

from lix_core.config import load_registry
from lix_core.log import setup_logging
from lix_core.paths import data_dir, ensure_dirs
from lix_pipeline.fetch import (
    ManualDownloadRequired,
    check_link,
    fetch,
    resolve_and_lock,
    select,
)
from lix_pipeline.fetch.session import make_session
from lix_pipeline.stage import STAGE_INPUTS, save_staged, stagers

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
    for slug in _selected(args):
        if args.check:
            status, note = check_link(slug, registry[slug], session)
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
    return 1 if failures else 0


def _fetch(args: argparse.Namespace) -> int:
    registry, session = load_registry(), make_session()
    missing_manual, failed = [], []
    for slug in _selected(args):
        try:
            fetch(slug, force=args.force, strict=args.strict, session=session, registry=registry)
        except ManualDownloadRequired as e:
            missing_manual.append(str(e))
        except Exception:
            logger.exception(f"[{slug}] Fetch failed")
            failed.append(slug)
    for msg in missing_manual:
        logger.warning(msg)
    if failed:
        logger.error(f"Failed: {failed}")
    return 1 if failed else 0


def _has_inputs(slug: str, registry: dict) -> bool:
    """Every input the stager needs for the active nations is on disk.

    Inputs covering none of the active nations are not needed (Welsh noise maps in an
    England-only build); a manual source counts when its files sit in data/manual/.
    """
    from lix_core.codes import active_nations

    active = set(active_nations())

    def present(s: str) -> bool:
        spec = registry.get(s)
        if spec is not None and not (active & set(spec.coverage)):
            return True
        if spec is not None and spec.access.type == "manual":
            return any(p.is_file() for p in (data_dir("manual") / s).glob("**/*"))
        return (data_dir("raw") / s / ".meta.json").exists()

    return all(present(s) for s in STAGE_INPUTS.get(slug, [slug]))


def _covers_active(slug: str, registry: dict) -> bool:
    """False when none of the stager's registered inputs covers any active nation."""
    from lix_core.codes import active_nations

    inputs = [s for s in STAGE_INPUTS.get(slug, [slug]) if s in registry]
    active = set(active_nations())
    return not inputs or any(active & set(registry[s].coverage) for s in inputs)


def _stage(args: argparse.Namespace) -> int:
    available = stagers()
    if args.slug and args.slug not in available:
        raise SystemExit(f"Unknown stager: {args.slug!r}. Available: {list(available)}")

    registry = load_registry()
    for slug in [args.slug] if args.slug else list(available):
        if args.all and not _has_inputs(slug, registry):
            logger.info(f"[{slug}] Inputs not fetched — skipping")
            continue
        if args.all and not _covers_active(slug, registry):
            logger.info(f"[{slug}] Its sources cover none of the active nations — skipping")
            continue
        save_staged(available[slug](), slug)
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

    from lix_pipeline.qa.validate import validate_serve
    from lix_pipeline.serve.demo import build_demo

    out = Path(args.out)
    build_demo(args.lad, out)
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
    from lix_pipeline.serve.scores import build_serve

    build_serve()
    problems = write_report()
    for p in problems:
        logger.warning(f"QA: {p}")
    logger.info(f"QA report: {data_dir('serve') / 'qa.md'} ({len(problems)} warning(s))")
    return 0


def _tiles(args: argparse.Namespace) -> int:
    from lix_pipeline.serve.tiles import build_tiles

    build_tiles()
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
    resolve.set_defaults(func=_resolve)

    fetch_p = sub.add_parser("fetch", help="Download datasets into data/raw/")
    _add_selection(fetch_p)
    fetch_p.add_argument("--force", action="store_true", help="Re-download even if cached")
    fetch_p.add_argument(
        "--strict", action="store_true", help="Fail if a file differs from the lockfile checksum"
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
    demo.add_argument("--lad", nargs="+", required=True, help="Local authority codes to keep")
    demo.add_argument("--out", default="fixtures/demo", help="Writes <out>/serve/")
    demo.set_defaults(func=_demo_data)

    indicators = sub.add_parser("indicators", help="Build data/indicators/long.parquet")
    indicators.add_argument("--only", help="Comma-separated indicator ids to rebuild")
    indicators.set_defaults(func=_indicators)

    score = sub.add_parser("score", help="Write data/serve/ (features, scores, manifest)")
    score.set_defaults(func=_score)

    tiles = sub.add_parser("tiles", help="Write map tiles to data/serve/tiles/*.pmtiles")
    tiles.set_defaults(func=_tiles)

    docs = sub.add_parser("docs", help="Regenerate docs/data-sources.md and ATTRIBUTION.md")
    docs.set_defaults(func=_docs)

    args = parser.parse_args(argv)
    ensure_dirs()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
