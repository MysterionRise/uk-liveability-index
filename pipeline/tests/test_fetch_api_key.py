"""Keyed sources: the key comes from the environment, travels on the request and never
reaches the lockfile, the meta or an error message; without it the fetch is skipped."""

import json

import pytest
from werkzeug import Response

from lix_core.config import DatasetSpec
from lix_pipeline.fetch import KeyMissing, ManualDownloadRequired, check_source, fetch, resolve
from lix_pipeline.fetch.keyed import redact
from lix_pipeline.refresh.plan import Step, fetch_steps


def _spec(url: str, **access) -> DatasetSpec:
    return DatasetSpec(
        title="Keyed",
        theme="geography",
        coverage=["N"],
        access={"type": "api_key", "url": url, "env": "TEST_SOURCE_KEY", **access},
        format="csv",
        description="d",
        licence="CC-BY-4.0",
        attribution="a",
        cadence="monthly",
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("lix_pipeline.fetch.lock.lock_path", lambda: tmp_path / "lock.json")
    monkeypatch.delenv("TEST_SOURCE_KEY", raising=False)
    return tmp_path


def test_access_needs_exactly_one_way_in():
    with pytest.raises(ValueError, match="exactly one of header or query"):
        _spec("https://x/a.csv")
    with pytest.raises(ValueError, match="exactly one of header or query"):
        _spec("https://x/a.csv", header="X-Api-Key", query="apiKey")


def test_resolve_makes_no_request_and_keeps_the_key_out(project):
    spec = _spec("https://x/a.csv", header="X-Api-Key")
    resolved = resolve("keyed", spec, session=None)
    assert resolved["url"] == "https://x/a.csv" and resolved["version"] == "https://x/a.csv"


def test_missing_key_is_skipped_like_a_manual_download(project):
    spec = _spec("https://x/a.csv", header="X-Api-Key", signup="https://x/signup")
    with pytest.raises(KeyMissing, match="TEST_SOURCE_KEY") as exc:
        fetch("keyed", registry={"keyed": spec})
    assert isinstance(exc.value, ManualDownloadRequired)
    assert "https://x/signup" in str(exc.value)
    assert check_source("keyed", spec, session=None)["status"] == "warn"
    assert "key not set: TEST_SOURCE_KEY" in check_source("keyed", spec, session=None)["note"]
    assert fetch_steps({}, {"keyed": spec}, ["keyed"]) == [
        Step("keyed", "api_key: TEST_SOURCE_KEY not set, skipped")
    ]


def test_header_key_downloads_and_never_lands_in_lock_or_meta(project, httpserver, monkeypatch):
    monkeypatch.setenv("TEST_SOURCE_KEY", "s3cret")
    httpserver.expect_request("/a.csv", headers={"X-Api-Key": "s3cret"}).respond_with_data(
        "a,b\n1,2\n", content_type="text/csv"
    )
    httpserver.expect_request("/a.csv").respond_with_data("no", status=401)
    spec = _spec(httpserver.url_for("/a.csv"), header="X-Api-Key")
    out = fetch("keyed", registry={"keyed": spec})
    raw = project / "data" / "raw" / "keyed" / "keyed.csv"
    assert raw.read_text() == "a,b\n1,2\n" and out["changed"]
    meta = json.loads((raw.parent / ".meta.json").read_text())
    lock = (project / "lock.json").read_text()
    assert "s3cret" not in meta["url"] and "s3cret" not in json.dumps(meta)
    assert "s3cret" not in lock
    assert fetch_steps(json.loads(lock), {"keyed": spec}, ["keyed"]) == [
        Step("keyed", "revalidate")
    ]


def test_query_key_is_sent_as_a_parameter(project, httpserver, monkeypatch):
    monkeypatch.setenv("TEST_SOURCE_KEY", "s3cret")
    httpserver.expect_request("/a.csv", query_string={"apiKey": "s3cret"}).respond_with_data(
        "a,b\n3,4\n", content_type="text/csv"
    )
    httpserver.expect_request("/a.csv").respond_with_data("no", status=401)
    spec = _spec(httpserver.url_for("/a.csv"), query="apiKey")
    fetch("keyed", registry={"keyed": spec})
    raw = project / "data" / "raw" / "keyed" / "keyed.csv"
    assert raw.read_text() == "a,b\n3,4\n"
    assert "s3cret" not in json.loads((raw.parent / ".meta.json").read_text())["url"]


def test_paged_json_is_read_to_the_last_short_page(project, httpserver, monkeypatch):
    monkeypatch.setenv("TEST_SOURCE_KEY", "s3cret")

    def page(request):
        n = int(request.args["Page"])
        assert request.headers["X-Api-Key"] == "s3cret" and request.args["PerPage"] == "2"
        items = {1: [{"id": 1}, {"id": 2}], 2: [{"id": 3}]}.get(n, [])
        return Response(json.dumps({"total": 3, "results": items}), content_type="application/json")

    httpserver.expect_request("/api").respond_with_handler(page)
    spec = DatasetSpec(
        title="Paged",
        theme="geography",
        coverage=["N"],
        access={
            "type": "api_key",
            "url": httpserver.url_for("/api"),
            "env": "TEST_SOURCE_KEY",
            "header": "X-Api-Key",
            "page_param": "Page",
            "per_page_param": "PerPage",
            "per_page": 2,
            "items_key": "results",
        },
        format="json",
        description="d",
        licence="CC-BY-4.0",
        attribution="a",
        cadence="monthly",
    )
    out = fetch("paged", registry={"paged": spec})
    raw = project / "data" / "raw" / "paged" / "paged.json"
    assert json.loads(raw.read_text()) == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert out["rows"] == 3


def test_errors_quote_no_key(project, httpserver, monkeypatch):
    monkeypatch.setenv("TEST_SOURCE_KEY", "s3cret")
    httpserver.expect_request("/a.csv").respond_with_data("forbidden", status=403)
    spec = _spec(httpserver.url_for("/a.csv"), query="apiKey")
    with pytest.raises(Exception) as exc:
        fetch("keyed", registry={"keyed": spec})
    assert "s3cret" not in str(exc.value)
    assert redact("https://x/a?apiKey=s3cret", "s3cret") == "https://x/a?apiKey=***"
