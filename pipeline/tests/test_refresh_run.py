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
