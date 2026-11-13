"""``lix qa diff``: what moved between two packs, and what that movement has to explain."""

import polars as pl
import pytest

from lix_pipeline.qa.diff import (
    definitions_changed,
    indicator_changes,
    new_editions,
    render,
    review,
)


def _features(**columns) -> pl.DataFrame:
    codes = ["E01000001", "E01000002", "W01000001", "I01000001"]
    return pl.DataFrame({"lsoa21cd": codes, **{f"raw__{k}": v for k, v in columns.items()}})


OLD = _features(crime=[10.0, 20.0, 30.0, None], flood=[1.0, 2.0, 3.0, 4.0])
NEW = _features(
    crime=[10.0, 25.0, 30.0, 7.0], flood=[1.0, 2.0, 3.0, 4.0], rent=[900.0, 950.0, 700.0, None]
)


def test_changes_are_counted_per_indicator_and_nation():
    changes = indicator_changes(OLD, NEW, ["crime", "flood", "rent"])
    rows = {(r["indicator"], r["nation"]): r for r in changes.iter_rows(named=True)}
    england = rows[("crime", "E")]
    assert (england["before"], england["after"], england["both"], england["moved"]) == (2, 2, 2, 1)
    assert england["median_move"] == pytest.approx(5.0)
    assert rows[("crime", "I")]["gained"] == 1 and rows[("crime", "I")]["moved"] == 0
    assert rows[("flood", "W")]["moved"] == 0
    # A new indicator is all gains; a nation with no value either side has no row
    assert rows[("rent", "E")]["gained"] == 2 and ("rent", "I") not in rows


def test_a_rounding_difference_is_not_a_move():
    old = _features(price=[250_000.0, 1e-12, 3.0, 4.0])
    new = _features(price=[250_000.0 + 1e-6, 2e-12, 3.0, 4.0])
    assert indicator_changes(old, new, ["price"])["moved"].sum() == 0


def _manifest(indicators: dict[str, list[str]], versions: dict[str, str]) -> dict:
    return {
        "indicators": [
            {"id": i, "sources": s, "builder": "x:y", "params": {}} for i, s in indicators.items()
        ],
        "sources": {slug: {"version": v} for slug, v in versions.items()},
    }


def test_a_new_edition_is_a_changed_checksum_when_the_lockfiles_are_given():
    old = _manifest({}, {"gtfs": "feed.zip", "list": "abc", "api": "1 Nov"})
    new = _manifest({}, {"gtfs": "feed.zip", "list": "abc", "api": "12 Nov", "rents": "oct"})
    # By version alone the timetable looks unchanged
    assert new_editions(old, new) == {"api": "new edition", "rents": "new source"}

    def lock(gtfs: str) -> dict:
        return {"gtfs": {"fetched": {"sha256": gtfs}}, "list": {"fetched": {"sha256": "s"}}}

    assert new_editions(old, new, lock("one"), lock("two")) == {
        "gtfs": "new file",
        "api": "new edition",  # no checksum: compared by its version
        "rents": "new source",
    }


def test_the_review_names_what_moved_without_a_cause_and_editions_that_moved_nothing():
    indicators = {"crime": ["police"], "flood": ["ea"], "rent": ["rents"], "pubs": ["osm"]}
    old = _manifest(
        {k: v for k, v in indicators.items() if k != "rent"},
        {"police": "sep", "ea": "2025", "osm": "1 Nov"},
    )
    new = _manifest(indicators, {"police": "sep", "ea": "2025", "osm": "11 Nov", "rents": "oct"})
    old_f = _features(crime=[10.0, 20.0, 30.0, None], flood=[1.0] * 4, pubs=[5.0] * 4)
    new_f = _features(
        crime=[10.0, 25.0, 30.0, None], flood=[1.0] * 4, pubs=[5.0] * 4, rent=[900.0] * 4
    )
    changes = indicator_changes(old_f, new_f, list(indicators))
    found = review(old, new, changes)
    assert definitions_changed(old, new) == {"rent": "new indicator"}
    # Crime moved though police.uk published nothing new; OpenStreetMap's new extract
    # moved no indicator that reads it
    assert found["moved_without_cause"] == ["crime"]
    assert found["editions_that_moved_nothing"] == ["osm"]
    report = render(old, new, changes, found)
    assert "**Moved without a new edition or a new definition (1):** `crime`" in report
    assert "| `rent` | E | 0 | 2 | 0 |" in report
    assert "2 indicators are identical in every nation." in report


def test_a_typical_value_that_collapses_is_named_even_when_a_new_file_explains_it():
    """England's bus frequency, November 2026: a new timetable file, so "explained", and a
    median of 4.7 an hour that became 0.3."""
    codes = [f"E010000{i:02d}" for i in range(30)] + [f"W010000{i:02d}" for i in range(30)]
    before = [4.0 + i / 10 for i in range(30)] + [3.0] * 30
    after = [0.3] * 30 + [3.1] * 30
    old_f = pl.DataFrame({"lsoa21cd": codes, "raw__bus_frequency": before})
    new_f = pl.DataFrame({"lsoa21cd": codes, "raw__bus_frequency": after})
    manifests = [_manifest({"bus_frequency": ["bods_gtfs"]}, {"bods_gtfs": v}) for v in "ab"]
    changes = indicator_changes(old_f, new_f, ["bus_frequency"])
    found = review(*manifests, changes)
    assert found["moved_without_cause"] == [] and found["editions_that_moved_nothing"] == []
    (shift,) = found["large_shifts"]
    assert shift[:2] == ("bus_frequency", "E") and shift[3] == pytest.approx(0.3)
    report = render(*manifests, changes, found)
    assert "shifted by more than a quarter (1):** `bus_frequency` in E: median 5.45 → 0.3" in report
