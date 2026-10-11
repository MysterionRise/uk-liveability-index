from datetime import date

from lix_core.config import DatasetSpec
from lix_pipeline.refresh.report import issue_body


def _spec(cadence: str) -> DatasetSpec:
    return DatasetSpec.model_validate(
        {
            "title": "T",
            "theme": "community",
            "description": "d",
            "licence": "OGL-3.0",
            "attribution": "a",
            "cadence": cadence,
            "format": "csv",
            "access": {"type": "http", "url": "https://x.test/f.csv"},
        }  # fmt: skip
    )


REGISTRY = {"gias": _spec("daily"), "nspl": _spec("quarterly"), "iod_2025": _spec("static")}
LOCK = {
    "gias": {"fetched": {"fetched_at": "2026-10-07T15:00:00+00:00"}},
    "nspl": {"fetched": {"fetched_at": "2026-01-01T15:00:00+00:00"}},
    "iod_2025": {"fetched": {"fetched_at": "2025-01-01T15:00:00+00:00"}},
}


def test_sections_and_pending():
    checks = {
        "checked_at": "2026-10-11T04:17:00+00:00",
        "results": [
            {"slug": "gias", "status": "ok", "note": "new upstream version (x)", "changed": True,
             "locked_version": "old", "fetched_at": "2026-10-07T15:00:00+00:00"},
            {"slug": "nspl", "status": "ok", "note": "unchanged", "changed": False},
            {"slug": "iod_2025", "status": "warn", "note": "not verified: 403 from https://x"},
        ],
    }  # fmt: skip
    body, pending = issue_body(checks, LOCK, REGISTRY, today=date(2026, 10, 11))
    assert pending
    assert "- `gias`: new upstream version (x) (built from old, fetched 2026-10-07)" in body
    assert "- `nspl`: fetched 2026-01-01, published quarterly" in body  # older than 190 days
    assert "iod_2025" in body.split("## Not verified")[1].split("## Failures")[0]
    assert "## Failures\n\nNone." in body


def test_nothing_pending_when_only_warnings():
    checks = {"results": [{"slug": "iod_2025", "status": "warn", "note": "not verified"}]}
    lock = {"nspl": {"fetched": {"fetched_at": "2026-10-01T00:00:00+00:00"}}}
    body, pending = issue_body(checks, lock, {"nspl": _spec("quarterly")}, today=date(2026, 10, 11))
    assert not pending and "None." in body


def test_failures_are_pending():
    checks = {"results": [{"slug": "gias", "status": "fail", "note": "HTTP 404"}]}
    body, pending = issue_body(checks, {}, {}, today=date(2026, 10, 11))
    assert pending and "- `gias`: HTTP 404" in body
