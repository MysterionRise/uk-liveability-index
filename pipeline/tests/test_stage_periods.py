"""What period each source's data describes, read from tables, names, URLs or the registry."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from lix_core.config import DatasetSpec
from lix_pipeline.fetch.dates import find_date
from lix_pipeline.stage import periods as per


def _spec(**kw) -> DatasetSpec:
    base = {
        "title": "T", "theme": "community", "description": "d", "licence": "OGL-3.0",
        "attribution": "a", "cadence": "annual", "format": "csv",
        "access": {"type": "http", "url": "https://x.test/f.csv"},
    }  # fmt: skip
    return DatasetSpec.model_validate({**base, **kw})


@pytest.fixture
def data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    return tmp_path


class TestShapes:
    def test_month_day_range_and_text(self):
        assert per.month(date(2026, 8, 14)) == {
            "label": "August 2026", "start": "2026-08-01", "end": "2026-08-31", "kind": "month",
        }  # fmt: skip
        assert per.day(date(2026, 10, 11))["label"] == "11 October 2026"
        rng = per.month_range(date(2023, 9, 1), date(2026, 8, 1))
        assert (rng["label"], rng["start"], rng["end"]) == (
            "September 2023 to August 2026", "2023-09-01", "2026-08-31",
        )  # fmt: skip
        assert per.text("Round 4 (2021 traffic)") == {
            "label": "Round 4 (2021 traffic)",
            "start": "2021-01-01",
            "end": "2021-12-31",
            "kind": "text",
        }
        assert per.text("Census 2021 geography")["end"] == "2021-12-31"
        assert per.text("mid-2024")["kind"] == "text"

    def test_find_date_reads_year_month_file_names(self):
        assert find_date("202601_fixed_broadband_coverage.zip") == date(2026, 1, 1)
        assert find_date("edubasealldata20261011.csv") == date(2026, 10, 11)


class TestPeriodOf:
    def test_registry_period_wins_over_dates_in_the_lock(self):
        spec = _spec(period="2021")
        entry = {"resolved": {"url": "https://x.test/table_2026-01-01.zip"}}
        assert per.period_of("census_ts001", spec, entry)["label"] == "2021"

    def test_dates_in_file_names_and_urls(self):
        spec = _spec()
        nspl = {"fetched": {"extracted": ["Data/NSPL_AUG_2026_UK.csv", "Documents/x.csv"]}}
        assert per.period_of("nspl", spec, nspl)["label"] == "August 2026"
        gias = {"resolved": {"url": "https://x.test/edubasealldata20261011.csv"}}
        assert per.period_of("gias", spec, gias) == per.day(date(2026, 10, 11))
        workforce = {"resolved": {"url": "https://x.test/GPWPracticeCSV.082026.zip"}}
        assert per.period_of("gp_workforce", spec, workforce)["label"] == "August 2026"
        nomis = {"resolved": {"version": "August 2026", "url": "https://nomis/api?x=1"}}
        assert per.period_of("claimant_count", spec, nomis)["kind"] == "month"
        overture = {"resolved": {"version": "2026-09-23.1", "url": "s3://b/release/2026-09-23.1/*"}}
        assert per.period_of("overture_pubs", spec, overture)["label"] == "23 September 2026"

    def test_http_dates_and_etags_are_not_periods(self):
        spec = _spec()
        entry = {
            "resolved": {
                "version": "Tue, 30 Sep 2025 04:55:38 GMT",
                "url": "https://x.test/pp.csv",
            },
            "fetched": {"last_modified": "Tue, 30 Sep 2025 04:55:38 GMT"},
        }
        assert per.period_of("voa_ctsop", spec, entry)["label"] == "published September 2025"
        etag = {"resolved": {"version": '"abc"', "url": "https://x.test/epraccur"}}
        assert per.period_of("ods_gp", spec, etag) is None
        downloaded = {**etag, "fetched": {"fetched_at": "2026-10-07T15:32:18+00:00"}}
        assert per.period_of("ods_gp", spec, downloaded)["label"] == "downloaded 7 October 2026"

    def test_pcm_year_from_the_file_name(self):
        entry = {"resolved": {"url": "https://uk-air.defra.gov.uk/datastore/pcm/mapno22024.csv"}}
        assert per.period_of("defra_pcm_no2", _spec(), entry) == per.year(2024)

    def test_staged_tables_carry_their_own_dates(self, data):
        (data / "staged").mkdir()
        pl.DataFrame(
            {"first_month": ["2023-09", "2023-10"], "last_month": ["2026-08", "2026-07"]}
        ).write_parquet(data / "staged" / "police_crime.parquet")
        assert (
            per.period_of("police_crime", _spec(), {})["label"] == "September 2023 to August 2026"
        )

        pl.DataFrame({"as_of": [date(2026, 9, 28)]}).write_parquet(
            data / "staged" / "price_paid.parquet"
        )
        pp = per.period_of("price_paid", _spec(), {})
        assert (pp["label"], pp["end"]) == ("12 months to September 2026", "2026-09-30")

        pl.DataFrame({"reference_day": [date(2026, 10, 20)]}).write_parquet(
            data / "staged" / "bods_gtfs.parquet"
        )
        assert (
            per.period_of("bods_gtfs_wales", _spec(), {})["label"]
            == "timetable for 20 October 2026"
        )

        pl.DataFrame(
            {"rc_date": [date(2025, 5, 1), None], "ungraded_date": [date(2026, 6, 3), None]}
        ).write_parquet(data / "staged" / "ofsted_schools.parquet")
        assert per.period_of("ofsted_schools", _spec(), {})["label"] == "inspections to June 2026"

    def test_data_to(self):
        assert (
            per.data_to({"a": per.year(2021), "b": per.month(date(2026, 8, 1)), "c": None})
            == "2026-08-31"
        )
        assert per.data_to({}) is None


def test_iso_timestamps_in_versions_are_publish_dates_not_periods():
    entry = {
        "resolved": {
            "version": "2026-09-01T11:08:22+00:00",
            "modified": "2026-09-01T11:08:22+00:00",
        },
        "fetched": {"extracted": ["Data/NSPL_AUG_2026_UK.csv"]},
    }
    assert per.period_of("nspl", _spec(), entry)["label"] == "August 2026"


def test_data_to_ignores_labels_and_the_future():
    periods = {
        "council_tax": per.text("2026/27"),
        "gtfs": per.day(date(2026, 10, 27)),
        "police": per.month_range(date(2023, 9, 1), date(2026, 8, 1)),
    }
    assert per.data_to(periods, today=date(2026, 10, 11)) == "2026-10-11"
    del periods["gtfs"]
    assert per.data_to(periods, today=date(2026, 10, 11)) == "2026-08-31"


def test_underscored_dates_in_file_names_are_snapshots():
    assert find_date("CTSOP_4_1/CTSOP4_1_2026_03_31.csv") == date(2026, 3, 31)
    entry = {"fetched": {"extracted": ["CTSOP_4_1/CTSOP4_1_2026_03_31.csv"]}}
    assert per.period_of("voa_ctsop", _spec(), entry)["label"] == "31 March 2026"
