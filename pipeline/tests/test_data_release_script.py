"""scripts/data_release.py: pure decisions, no network (the script is stdlib-only)."""

import importlib.util
from datetime import date
from pathlib import Path

from lix_core.paths import get_project_root

spec = importlib.util.spec_from_file_location(
    "data_release", Path(get_project_root()) / "scripts" / "data_release.py"
)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def _release(tag: str, schema: int | None, draft: bool = False) -> dict:
    body = f"Monthly data.\n\n<!-- lix-data schema={schema} -->" if schema else "code release"
    return {"tag_name": tag, "body": body, "draft": draft, "prerelease": tag.startswith("data-")}


def test_schema_marker_and_tag_keys():
    assert dr.parse_schema("x <!-- lix-data schema=2 --> y") == 2
    assert dr.parse_schema("no marker") is None
    assert dr.tag_key("data-2026-10") == (2026, 10, 0)
    assert dr.tag_key("data-2026-10.2") == (2026, 10, 2)
    assert dr.tag_key("v0.3.3") is None


def test_latest_compatible_release_by_tag_not_date():
    releases = [
        _release("v0.3.2", None),
        _release("data-2026-10", 2),
        _release("data-2026-11", 3),  # a newer schema the code cannot read
        _release("data-2026-10.1", 2),
        _release("data-2026-12", 2, draft=True),  # not published yet
        _release("data-2026-09", 2),
    ]
    assert dr.pick_latest(releases, 2)["tag_name"] == "data-2026-10.1"
    assert dr.pick_latest(releases, 3)["tag_name"] == "data-2026-11"
    assert dr.pick_latest(releases, 4) is None


def test_next_tag_adds_a_suffix_within_the_month():
    today = date(2026, 10, 11)
    assert dr.next_tag(["v0.3.2"], today) == "data-2026-10"
    assert dr.next_tag(["data-2026-10"], today) == "data-2026-10.1"
    assert dr.next_tag(["data-2026-10", "data-2026-10.1"], today) == "data-2026-10.2"
    assert dr.next_tag(["data-2026-09"], today) == "data-2026-10"


def test_a_pack_built_before_the_last_build_commit_is_refused():
    built = "2026-11-13T19:38:13+00:00"
    earlier = ("8e759f9aaaa", "2026-11-13T19:10:00+00:00", "A county is all of its councils")
    later = ("1234567bbbb", "2026-11-13T21:02:11+01:00", "GP names in title case")
    assert dr.provenance_problems(built, earlier, []) == []
    # 21:02 in +01:00 is 20:02 UTC: after the build
    (problem,) = dr.provenance_problems(built, later, [])
    assert "before commit 1234567" in problem and "GP names in title case" in problem
    # git writes a UTC commit date with a Z
    zulu = ("7654321cccc", "2026-11-13T19:40:00Z", "Welsh school names")
    assert len(dr.provenance_problems(built, zulu, [])) == 1
    # A repository with no commit under the build paths has nothing to be older than
    assert dr.provenance_problems(built, None, []) == []


def test_uncommitted_build_code_and_a_manifest_without_a_date_are_refused():
    dirty = [f"pipeline/src/lix_pipeline/serve/file{i}.py" for i in range(7)]
    problems = dr.provenance_problems(None, None, dirty)
    assert problems[0].startswith("uncommitted changes to what builds the pack: ")
    assert (
        problems[0].endswith(" …") and "file4.py" in problems[0] and "file5.py" not in problems[0]
    )
    assert "does not say when the pack was built" in problems[1]


def test_the_lockfile_is_not_build_code():
    # The refresh that builds a pack writes the lockfile, which is committed after it
    assert ":(exclude)config/datasets.lock.json" in dr.BUILD_PATHS
