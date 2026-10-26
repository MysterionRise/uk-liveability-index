"""The nightly source check: what counts as a new edition, a changed file, a warning
and a failure (#192, #196)."""

import pytest
import requests

import lix_pipeline.fetch as fetch_mod
from lix_core.config import DatasetSpec
from lix_pipeline.fetch import check_source

LM1 = "Thu, 30 Nov 2023 08:50:43 GMT"
LM2 = "Fri, 24 Oct 2026 09:00:00 GMT"


def _spec(url: str) -> DatasetSpec:
    return DatasetSpec(
        title="Plain file",
        theme="geography",
        coverage=["N"],
        access={"type": "http", "url": url},
        format="csv",
        description="d",
        licence="OGL-3.0",
        attribution="a",
        cadence="monthly",
    )


@pytest.fixture
def lock(monkeypatch):
    entry = {
        "plain": {
            "resolved": {"version": LM1},
            "fetched": {"last_modified": LM1, "bytes": 100, "fetched_at": "2026-10-20T00:00:00"},
        }
    }
    monkeypatch.setattr(fetch_mod, "read_lock", lambda: entry)
    return entry


def _serve(httpserver, head_headers: dict, get_headers: dict, status: int = 206):
    httpserver.expect_request("/a.csv", method="HEAD").respond_with_data("", headers=head_headers)
    httpserver.expect_request("/a.csv", method="GET").respond_with_data(
        "x", status=status, headers=get_headers
    )


def test_a_206_with_the_same_validator_is_unchanged(httpserver, lock):
    """NISRA, NRS and PHS ignore conditional headers on a Range request and answer 206:
    the file is unchanged when the validator they state is the one in the lock."""
    _serve(
        httpserver, {"Last-Modified": LM1}, {"Last-Modified": LM1, "Content-Range": "bytes 0-0/100"}
    )
    out = check_source("plain", _spec(httpserver.url_for("/a.csv")), requests.Session())
    assert out["status"] == "ok" and not out["changed"]
    assert out["note"].startswith("unchanged")


def test_a_206_with_another_validator_is_a_changed_file(httpserver, lock):
    _serve(
        httpserver, {"Last-Modified": LM1}, {"Last-Modified": LM2, "Content-Range": "bytes 0-0/100"}
    )
    out = check_source("plain", _spec(httpserver.url_for("/a.csv")), requests.Session())
    assert out["changed"] and "file changed" in out["note"]


def test_a_server_that_states_no_edition_today_is_judged_on_size(httpserver, lock):
    """A HEAD without Last-Modified resolves to a null version, which is no new edition
    against the locked one (sg_council_tax and simd_* in the 2026-10-26 report)."""
    _serve(httpserver, {}, {"Content-Range": "bytes 0-0/100"})
    out = check_source("plain", _spec(httpserver.url_for("/a.csv")), requests.Session())
    assert out["version"] is None and not out["changed"]
    assert out["note"].startswith("ok, size unchanged")

    httpserver.clear()
    _serve(httpserver, {}, {"Content-Range": "bytes 0-0/120"})
    out = check_source("plain", _spec(httpserver.url_for("/a.csv")), requests.Session())
    assert out["changed"] and "size 100 → 120" in out["note"]


def test_a_missing_file_stays_a_failure(httpserver, lock):
    _serve(httpserver, {"Last-Modified": LM1}, {}, status=404)
    out = check_source("plain", _spec(httpserver.url_for("/a.csv")), requests.Session())
    assert out["status"] == "fail" and out["note"] == "HTTP 404"


def test_a_connection_failure_is_a_warning_after_one_retry(monkeypatch, lock):
    """Pobal dropped GitHub's runner addresses with a connect timeout: the runner's
    network, not the file, so a warning (#192); a 404 above is still a failure."""
    calls = []

    def unreachable(slug, spec, session):
        calls.append(slug)
        raise requests.ConnectTimeout("timed out")

    monkeypatch.setattr(fetch_mod, "resolve", unreachable)
    monkeypatch.setattr(fetch_mod, "PROBE_RETRY_S", 0)
    out = check_source("plain", _spec("https://maps.pobal.ie/a.csv"), requests.Session())
    assert out["status"] == "warn" and "could not connect to maps.pobal.ie" in out["note"]
    assert calls == ["plain", "plain"]


def _serve_compressed(httpserver, name_header: str):
    httpserver.expect_request("/a.csv", method="HEAD").respond_with_data("", status=404)
    httpserver.expect_request("/a.csv", method="GET").respond_with_data(
        "xxxxxxxx", headers={"Content-Encoding": "gzip", "Content-Disposition": name_header}
    )


def test_a_compressed_transfer_is_versioned_by_its_file_name(httpserver, monkeypatch):
    """The DfE statistics API answers HEAD with 404, states no validator, ignores Range
    and gzips every CSV, so its Content-Length is the wire size and the size comparison
    flagged the key stage files every night; its Content-Disposition names the edition,
    which a renamed file changes (#203)."""
    entry = {
        "plain": {
            "resolved": {"version": "abc_v1.0.1.csv"},
            "fetched": {"bytes": 100, "fetched_at": "2026-10-20T00:00:00"},
        }
    }
    monkeypatch.setattr(fetch_mod, "read_lock", lambda: entry)
    _serve_compressed(httpserver, "attachment; filename=abc_v1.0.1.csv")
    out = check_source("plain", _spec(httpserver.url_for("/a.csv")), requests.Session())
    assert out["version"] == "abc_v1.0.1.csv"
    assert out["status"] == "ok" and not out["changed"] and "size" not in out["note"]

    httpserver.clear()
    _serve_compressed(httpserver, "attachment; filename*=UTF-8''abc_v1.0.2.csv")
    out = check_source("plain", _spec(httpserver.url_for("/a.csv")), requests.Session())
    assert out["version"] == "abc_v1.0.2.csv"
    assert out["changed"] and out["note"].startswith("new upstream version")
