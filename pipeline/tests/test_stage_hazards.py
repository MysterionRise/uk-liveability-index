"""Two things a refreshed archive must not do: mix NSPL editions, or stretch the police
window with month folders left behind."""

from pathlib import Path

import polars as pl
import pytest

from lix_pipeline.geo.nspl import find_nspl_csv, nspl_document
from lix_pipeline.stage.police import police_months, stage_police


class TestNsplEdition:
    def test_newest_edition_wins_over_alphabetical_order(self, tmp_path: Path):
        (tmp_path / "Data").mkdir()
        (tmp_path / "Data" / "NSPL_NOV_2026_UK.csv").write_text("pcds\n")
        (tmp_path / "Data" / "NSPL_AUG_2026_UK.csv").write_text("pcds\n")
        assert find_nspl_csv(tmp_path).name == "NSPL_NOV_2026_UK.csv"

    def test_documents_follow_the_same_rule(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
        docs = tmp_path / "raw" / "nspl" / "Documents"
        docs.mkdir(parents=True)
        (docs / "PFA names and codes UK as at 12_20.csv").write_text("x\n")
        (docs / "PFA names and codes UK as at 06_26.csv").write_text("x\n")
        assert nspl_document("PFA*names and codes*.csv").name.endswith("06_26.csv")

    def test_missing_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            find_nspl_csv(tmp_path)


HEADER = (
    "Crime ID,Month,Reported by,Falls within,Longitude,Latitude,Location,LSOA code,"
    "LSOA name,Crime type,Last outcome category,Context\n"
)


def _month(root: Path, month: str, rows: int) -> None:
    folder = root / month
    folder.mkdir(parents=True)
    body = "".join(
        f"id{i},{month},Test Force,Test Force,-1.5,53.8,Near,E01000001,Leeds 001A,Burglary,,\n"
        for i in range(rows)
    )
    (folder / f"{month}-test-force-street.csv").write_text(HEADER + body)


class TestPoliceWindow:
    def test_only_the_latest_36_months_are_read(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
        root = tmp_path / "raw" / "police_crime"
        months = [f"{2023 + (i // 12)}-{(i % 12) + 1:02d}" for i in range(38)]  # 2023-01 … 2026-02
        for m in months:
            _month(root, m, rows=1)
        (root / "notes").mkdir()  # not a month folder

        assert [p.name for p in police_months(root)] == months[2:]

        df = stage_police().collect()
        assert df["n"].sum() == 36
        assert df["window_start"][0] == "2023-03" and df["window_end"][0] == "2026-02"
        assert df["force_months"][0] == 36

    def test_no_month_folders(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
        (tmp_path / "raw" / "police_crime").mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            stage_police()


def test_window_columns_are_constant(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    root = tmp_path / "raw" / "police_crime"
    _month(root, "2026-07", rows=2)
    _month(root, "2026-08", rows=3)
    df = stage_police().collect()
    assert df.select(pl.col("window_start").n_unique(), pl.col("window_end").n_unique()).row(0) == (
        1,
        1,
    )
    assert df["n"].sum() == 5
