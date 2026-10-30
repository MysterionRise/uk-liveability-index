"""``lix refresh`` around a source that cannot be resolved tonight (#204)."""

from types import SimpleNamespace

import pytest

from lix_pipeline.refresh import run as run_mod
from lix_pipeline.refresh.plan import RefreshPlan
from lix_pipeline.refresh.run import RefreshFailed, refresh


@pytest.fixture
def two_sources(monkeypatch, tmp_path):
    """Two http sources, nothing stale, no validators: only the resolve and fetch phases
    do anything."""
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    spec = SimpleNamespace(access=SimpleNamespace(type="http"))
    monkeypatch.setattr(run_mod, "load_indicators", lambda: SimpleNamespace(indicators={}))
    monkeypatch.setattr(run_mod, "load_registry", lambda: {"a": spec, "b": spec})
    monkeypatch.setattr(run_mod, "active_nations", lambda: ["E"])
    monkeypatch.setattr(run_mod, "make_session", lambda: None)
    monkeypatch.setattr(run_mod, "read_lock", lambda: {})
    monkeypatch.setattr(run_mod, "stagers", lambda: {})
    monkeypatch.setattr(run_mod.planning, "slugs_in_scope", lambda registry, nations: ["a", "b"])
    monkeypatch.setattr(run_mod.planning, "make_plan", lambda *a, **k: RefreshPlan(nations=["E"]))
    monkeypatch.setattr(run_mod.planning, "serve_step", lambda force: None)
    monkeypatch.setattr(run_mod.planning, "tiles_step", lambda lock, nations, force: None)
    monkeypatch.setattr(
        "lix_pipeline.qa.validate.VALIDATORS", {"serve": lambda: [], "places": lambda: []}
    )
    fetched: list[str] = []

    def resolve_and_lock(slug, spec, session):
        if slug == "a":
            raise RuntimeError("302 to /maintenance/")
        return {"version": "v1"}

    monkeypatch.setattr(run_mod, "resolve_and_lock", resolve_and_lock)
    monkeypatch.setattr(
        run_mod, "fetch", lambda slug, **kw: fetched.append(slug) or {"changed": False}
    )
    return fetched


def test_a_source_that_fails_to_resolve_is_not_fetched_and_stops_the_run(two_sources):
    with pytest.raises(RefreshFailed, match="1 source\\(s\\) failed.*'a'"):
        refresh()
    assert two_sources == ["b"]  # its edition is unknown: the raw file and lock entry stand


def test_keep_going_builds_from_the_locked_editions(two_sources):
    log = refresh(keep_going=True)
    assert two_sources == ["b"]
    assert log["kept"] == ["a"] and log["failures"][0]["phase"] == "resolve"
    assert "validate" in log["phases"]  # the run went on to the end


def test_a_publisher_in_maintenance_keeps_its_edition_without_keep_going(two_sources, monkeypatch):
    """uk-air.defra.gov.uk redirected everything to /maintenance/ for days (#219): an
    outage is not a reshaped file, so the run goes on without --keep-going."""
    from lix_pipeline.fetch.outage import InMaintenance

    def resolve_and_lock(slug, spec, session):
        if slug == "a":
            raise InMaintenance(
                "uk-air.defra.gov.uk is in maintenance (redirected to /maintenance/)"
            )
        return {"version": "v1"}

    monkeypatch.setattr(run_mod, "resolve_and_lock", resolve_and_lock)
    log = refresh()
    assert two_sources == ["b"]
    assert log["failures"] == [] and log["outages"] == ["a"] and log["kept"] == ["a"]
    assert "validate" in log["phases"]


def test_a_kept_source_keeps_the_edition_its_file_is(two_sources, monkeypatch):
    """A source resolved to a new edition whose download then failed keeps the old raw
    file, so the lock's resolved edition goes back to the old one (#215)."""
    import copy

    lock = {"a": {"resolved": {"version": "old"}}, "b": {"resolved": {"version": "v1"}}}
    monkeypatch.setattr(run_mod, "read_lock", lambda: copy.deepcopy(lock))  # read from disk
    monkeypatch.setattr(run_mod, "resolve_and_lock", lambda slug, spec, session: {"version": "new"})

    def fetch(slug, **kw):
        if slug == "a":
            lock["a"] = {"resolved": {"version": "new"}}  # what resolve_and_lock wrote
            raise RuntimeError("the download stalled")
        return {"changed": False}

    monkeypatch.setattr(run_mod, "fetch", fetch)
    written = []
    monkeypatch.setattr(run_mod, "update_entry", lambda *a: written.append(a))
    log = refresh(keep_going=True)
    assert log["kept"] == ["a"]
    assert written == [("a", "resolved", {"version": "old"})]


def test_a_server_that_keeps_answering_503_is_an_outage(two_sources, monkeypatch):
    """The fetch session retries 503s and then raises RetryError, so the 503 itself never
    reaches the maintenance check (v0.7.0 release pass)."""
    import requests

    def resolve_and_lock(slug, spec, session):
        if slug == "a":
            raise requests.exceptions.RetryError(
                "Max retries exceeded (too many 503 error responses)"
            )
        return {"version": "v1"}

    monkeypatch.setattr(run_mod, "resolve_and_lock", resolve_and_lock)
    log = refresh()
    assert log["failures"] == [] and log["outages"] == ["a"]


def test_outage_tells_a_503_give_up_from_other_failures():
    import requests

    from lix_pipeline.fetch.outage import InMaintenance, outage

    assert isinstance(
        outage(requests.exceptions.RetryError("too many 503 error responses")), InMaintenance
    )
    assert outage(requests.exceptions.RetryError("too many 500 error responses")) is None
    assert outage(RuntimeError("No link matching")) is None
