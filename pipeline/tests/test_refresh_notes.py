from lix_pipeline.refresh.notes import release_notes


def _manifest(**sources) -> dict:
    return {
        "schema_version": 2,
        "generated_at": "2026-10-11T20:00:00+00:00",
        "data_to": "2026-08-31",
        "lsoa_count": 35672,
        "geography": {
            "nations": {"E": {"name": "England"}, "W": {"name": "Wales"}},
            "area_counts": {"E": 33755, "W": 1917},
        },
        "sources": sources,
    }


def test_notes_carry_the_marker_the_counts_and_the_changes():
    previous = _manifest(
        nspl={"fetched_version": "v1", "period": {"label": "August 2026"}},
        gias={"fetched_version": "a", "period": {"label": "7 October 2026"}},
    )
    current = _manifest(
        nspl={"fetched_version": "v1", "period": {"label": "August 2026"}},
        gias={"fetched_version": "b", "period": {"label": "11 October 2026"}, "stale": True},
        viirs_vnl={"version": None, "period": {"label": "2025"}},
    )
    notes = release_notes(current, previous, "data-2026-10")
    assert "<!-- lix-data schema=2 -->" in notes
    assert "35,672 areas (England 33,755, Wales 1,917)" in notes
    assert "data to 2026-08-31" in notes
    assert "| `gias` | 11 October 2026 | b | new edition |" in notes
    assert "| `viirs_vnl` | 2025 |  | new source |" in notes
    assert "| `nspl` | August 2026 | v1 |  |" in notes
    assert "Stale: gias" in notes and "2 changed since" in notes
    assert "make data-download DATA_TAG=data-2026-10" in notes


def test_first_release_has_no_change_column_values():
    notes = release_notes(_manifest(nspl={"version": "v1"}), None, "data-2026-10")
    assert "| `nspl` |  | v1 |  |" in notes and "changed since" not in notes
    assert "No source is behind" in notes
