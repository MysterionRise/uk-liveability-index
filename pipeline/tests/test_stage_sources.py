"""Tests for the per-source stagers, on small files shaped like the real ones."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from lix_pipeline.fetch.dates import find_date
from lix_pipeline.stage.census import census_column_name, stage_census_table
from lix_pipeline.stage.health import stage_gp_registrations, stage_gp_workforce
from lix_pipeline.stage.pcm import read_pcm
from lix_pipeline.stage.police import stage_police
from lix_pipeline.stage.schools import NEUTRAL, school_quality
from lix_pipeline.stage.transport import tcm_column


@pytest.fixture
def data(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    return tmp_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestFindDate:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("latest_inspections_as_at_31_August_2026.csv", date(2026, 8, 31)),
            ("/patients-registered-at-a-gp-practice/july-2026", date(2026, 7, 1)),
            ("gp-reg-pat-prac-lsoa-male-female-July-26.zip", date(2026, 7, 1)),
            ("GPWPracticeCSV.072026.zip", date(2026, 7, 1)),
            ("edubasealldata20261007.csv", date(2026, 10, 7)),
            # "%20" must not read as day 20
            ("GPW%20Bulletin%20Tables%20-%20July%202026.xlsx", date(2026, 7, 1)),
            ("EYR_inspections_10_November_2025_to_30_June_2026.csv", date(2026, 6, 30)),
            ("connectivity_metrics_2025.ods", None),
        ],
    )
    def test_formats(self, text, expected):
        assert find_date(text) == expected


class TestCensus:
    @pytest.mark.parametrize(
        ("label", "name"),
        [
            ("Residence type: Total; measures: Value", "total"),
            ("Number of cars or vans: Total: All households", "total"),
            ("Tenure of household: Owned: Owns outright", "owned_owns_outright"),
            ("Population Density: Persons per square kilometre; measures: Value",
             "persons_per_square_kilometre"),
            ("Distance travelled to work: 2km to less than 5km", "2km_to_less_than_5km"),
            # A rating of +1 and of -1 are opposite things (#78)
            ("Occupancy rating for bedrooms: Occupancy rating of bedrooms: +1",
             "occupancy_rating_of_bedrooms_plus_1"),
            ("Occupancy rating for bedrooms: Occupancy rating of bedrooms: -1",
             "occupancy_rating_of_bedrooms_minus_1"),
            ("Occupancy rating for bedrooms: Occupancy rating of bedrooms: -2 or less",
             "occupancy_rating_of_bedrooms_minus_2_or_less"),
            ("Economic activity status: Economically inactive: Long-term sick or disabled",
             "economically_inactive_long_term_sick_or_disabled"),
        ],
    )  # fmt: skip
    def test_column_names(self, label, name):
        assert census_column_name(label) == name

    def test_stage_table(self, data):
        _write(
            data / "raw" / "census_ts045" / "census2021-ts045-lsoa.csv",
            "date,geography,geography code,Number of cars or vans: Total: All households,"
            "Number of cars or vans: No cars or vans in household\n"
            "2021,City of London 001A,E01000001,837,555\n"
            "2021,Cardiff 001A,W01001700,500,100\n",
        )
        df = stage_census_table("census_ts045").collect()
        assert df.to_dicts() == [
            {"lsoa21cd": "E01000001", "total": 837.0, "no_cars_or_vans_in_household": 555.0}
        ]

    def test_a_concept_column_sums_the_parts_the_census_splits(self, data):
        topic = "Occupancy rating for bedrooms"
        ratings = ["+2 or more", "+1", "0", "-1", "-2 or less"]
        _write(
            data / "raw" / "census_ts052" / "census2021-ts052-lsoa.csv",
            f"date,geography,geography code,{topic}: Total: All households,"
            + ",".join(f"{topic}: Occupancy rating of bedrooms: {r}" for r in ratings)
            + "\n2021,City of London 001A,E01000001,1000,400,300,250,40,10\n",
        )
        row = stage_census_table("census_ts052").collect().to_dicts()[0]
        assert row["overcrowded"] == 50.0
        assert row["occupancy_rating_of_bedrooms_plus_1"] == 300.0
        assert row["occupancy_rating_of_bedrooms_minus_1"] == 40.0


class TestPrivateRents:
    def table(self) -> pl.DataFrame:
        rows = [
            # month, code, name, rent, one bed, two bed, three bed, annual change
            ("2026-08-01 00:00:00", "E08000035", "Leeds", "1140", "780", "970", "1135", "4.6"),
            ("2026-09-01 00:00:00", "E08000035", "Leeds", "1148", "783", "977", "1142", "4.476682"),
            # No rent yet for the latest month: the month before stands
            ("2026-08-01 00:00:00", "E06000001", "Hartlepool", "560", "[x]", "510", "610", "1.2"),
            ("2026-09-01 00:00:00", "E06000001", "Hartlepool", "[x]", "[x]", "[x]", "[x]", "[x]"),
            # A country, a region, a Scottish market area and a Northern Irish one
            ("2026-09-01 00:00:00", "E92000001", "England", "1460", "1180", "1300", "1470", "3.9"),
            ("2026-09-01 00:00:00", "E12000003", "Yorkshire", "900", "650", "790", "920", "4.1"),
            ("2026-09-01 00:00:00", "S33000001", "Aberdeen", "800", "560", "780", "1000", "2"),
            ("2026-09-01 00:00:00", "[z]", "Belfast BRMA", "[x]", "[x]", "[x]", "[x]", "[x]"),
        ]  # fmt: skip
        names = ["Time period", "Area code", "Area name", "Rental price", "Rental price one bed",
                 "Rental price two bed", "Rental price three bed", "Annual change"]  # fmt: skip
        return pl.DataFrame(rows, schema=names, orient="row")

    def test_each_council_keeps_its_latest_month_with_a_rent(self, data):
        from datetime import date as day

        from lix_pipeline.stage.housing import tidy_private_rents

        out = tidy_private_rents(self.table())
        assert out["lad_cd"].to_list() == ["E06000001", "E08000035"]
        assert out["rent"].to_list() == [560.0, 1148.0]
        assert out["month"].to_list() == [day(2026, 8, 1), day(2026, 9, 1)]
        assert out["rent_one_bed"].to_list() == [None, 783.0]
        assert out["rent_annual_change"].to_list() == pytest.approx([1.2, 4.476682])

    def test_the_period_is_the_month_of_the_rents_not_the_day_they_were_published(self, data):
        from lix_pipeline.stage.housing import tidy_private_rents
        from lix_pipeline.stage.periods import STAGED_EXTRACTORS

        (data / "staged").mkdir()
        tidy_private_rents(self.table()).write_parquet(
            data / "staged" / "ons_private_rents.parquet"
        )
        period = STAGED_EXTRACTORS["ons_private_rents"]("ons_private_rents")
        assert period["label"] == "September 2026" and period["end"] == "2026-09-30"


class TestPolice:
    HEADER = (
        "Crime ID,Month,Reported by,Falls within,Longitude,Latitude,Location,LSOA code,"
        "LSOA name,Crime type,Last outcome category,Context\n"
    )

    def _month(self, data, month: str, force_slug: str, force: str, rows: list[tuple]):
        lines = [f",{month},{force},{force},0,0,x,{lsoa},n,{ctype},," for lsoa, ctype in rows]
        _write(
            data / "raw" / "police_crime" / month / f"{month}-{force_slug}-street.csv",
            self.HEADER + "\n".join(lines) + "\n",
        )

    def test_counts_and_force_months(self, data):
        self._month(data, "2026-07", "kent", "Kent Police", [("E01000001", "Burglary")] * 2)
        self._month(data, "2026-08", "kent", "Kent Police", [("E01000001", "Burglary")])
        self._month(data, "2026-08", "glos", "Gloucestershire Constabulary",
                    [("E01000002", "Robbery"), ("W01000001", "Robbery")])  # fmt: skip
        self._month(data, "2026-08", "btp", "British Transport Police", [("E01000001", "Robbery")])

        df = stage_police().collect().sort("lsoa21cd")

        assert df.select("lsoa21cd", "crime_type", "force", "n", "force_months").to_dicts() == [
            {"lsoa21cd": "E01000001", "crime_type": "Burglary", "force": "Kent Police",
             "n": 3, "force_months": 2},
            {"lsoa21cd": "E01000002", "crime_type": "Robbery",
             "force": "Gloucestershire Constabulary", "n": 1, "force_months": 1},
        ]  # fmt: skip


class TestPcm:
    def test_read_pcm(self, tmp_path):
        path = _write(
            tmp_path / "mapno22024.csv",
            "no2,,,\n2024,,,\nannual mean,,,\nug m-3,,,\n,,,\n"
            "gridcode,x,y,no22024\n1,500,500,9.5\n2,1500,500,MISSING\n",
        )
        cells, year = read_pcm(path)
        assert year == 2024  # not 2202 from the "2" in "no2"
        assert cells.to_dicts() == [{"x": 500, "y": 500, "value": 9.5}]


class TestHealth:
    def test_registrations_keep_england_lsoas(self, data):
        _write(
            data / "raw" / "gp_registrations" / "gp-reg-pat-prac-lsoa-all.csv",
            "PUBLICATION,EXTRACT_DATE,PRACTICE_CODE,PRACTICE_NAME,LSOA_CODE,SEX,NUMBER_OF_PATIENTS\n"
            "X,2026-07-01,A81001,P,E01000001,ALL,120\n"
            "X,2026-07-01,A81001,P,CLOSED,ALL,1\n"
            "X,2026-07-01,A81001,P,W01000001,ALL,4\n",
        )
        df = stage_gp_registrations().collect()
        assert df.select("practice_code", "lsoa21cd", "patients").rows() == [
            ("A81001", "E01000001", 120)
        ]

    def test_workforce_sums_roles_not_published_total(self, data):
        rows = [
            ("GP", "Partner/Provider", "FTE", "2.0"),
            ("GP", "GP in Training Grade ST3", "FTE", "1.0"),
            ("GP", "Total", "FTE", "0"),  # published total is wrong for some practices
            ("GP", "Partner/Provider", "Headcount", "3"),
            ("Nurses", "Practice Nurse", "FTE", "NA"),
            ("Nurses", "Advanced Nurse Practitioner", "FTE", "0.5"),
        ]
        body = "\n".join(f"A81001,P,{g},{r},{m},{v}" for g, r, m, v in rows)
        _write(
            data
            / "raw"
            / "gp_workforce"
            / "3 General Practice – August 2026 Practice Level - High level.csv",
            "PRAC_CODE,PRAC_NAME,STAFF_GROUP,DETAILED_STAFF_ROLE,MEASURE,VALUE\n" + body + "\n",
        )
        row = stage_gp_workforce().collect().row(0, named=True)
        assert row["gp_fte"] == 3.0
        assert row["qualified_gp_fte"] == 2.0
        assert row["nurse_fte"] == 0.5


def _ofsted(**kw) -> dict:
    row = {
        "rc_inclusion": None, "rc_curriculum_teaching": None, "rc_achievement": None,
        "rc_attendance_behaviour": None, "rc_personal_development": None,
        "rc_leadership": None, "rc_safeguarding_met": None, "rc_date": None,
        "oeif_overall": None, "oeif_quality_of_education": None, "oeif_behaviour": None,
        "oeif_personal_development": None, "oeif_leadership": None, "oeif_date": None,
        "ungraded_outcome": None, "ungraded_date": None,
    }  # fmt: skip
    return {**row, **kw}


class TestSchoolQuality:
    AS_OF = date(2026, 10, 1)

    def _q(self, **kw) -> dict:
        df = pl.DataFrame([_ofsted(**kw)], schema_overrides={
            "rc_date": pl.Date, "oeif_date": pl.Date, "ungraded_date": pl.Date,
            "rc_safeguarding_met": pl.Boolean, "ungraded_outcome": pl.Utf8,
        } | {c: pl.Float64 for c in _ofsted() if c.startswith(("rc_", "oeif_"))
             and not c.endswith(("date", "met"))})  # fmt: skip
        return school_quality(df, self.AS_OF).row(0, named=True)

    def test_recent_report_card(self):
        r = self._q(rc_achievement=0.8, rc_inclusion=1.0, rc_date=date(2026, 10, 1))
        assert r["framework"] == "report_card"
        assert r["quality"] == pytest.approx(0.9)

    def test_failed_safeguarding_caps_score(self):
        r = self._q(rc_achievement=1.0, rc_safeguarding_met=False, rc_date=date(2026, 10, 1))
        assert r["quality"] == pytest.approx(0.2)

    def test_old_outstanding_fades_towards_neutral(self):
        fresh = self._q(oeif_overall=0.95, oeif_date=date(2026, 10, 1))
        old = self._q(oeif_overall=0.95, oeif_date=date(2014, 10, 1))  # 12 years = 2 half-lives
        assert fresh["quality"] == pytest.approx(0.95)
        assert old["quality"] == pytest.approx(NEUTRAL + (0.95 - NEUTRAL) / 4, abs=1e-3)

    def test_not_judged_uses_sub_grades(self):
        r = self._q(
            oeif_quality_of_education=0.7, oeif_leadership=0.95, oeif_date=date(2026, 10, 1)
        )
        assert r["framework"] == "oeif_not_judged"
        assert r["quality"] == pytest.approx(0.825)

    def test_later_ungraded_concerns_lower_score(self):
        r = self._q(oeif_overall=0.7, oeif_date=date(2026, 9, 1),
                    ungraded_outcome="School remains Good (Concerns) - S5 Next",
                    ungraded_date=date(2026, 10, 1))  # fmt: skip
        assert r["quality"] == pytest.approx(0.6, abs=0.01)
        assert r["inspection_date"] == date(2026, 10, 1)

    def test_uninspected_school_has_no_score(self):
        r = self._q()
        assert r["quality"] is None
        assert r["framework"] is None


def test_tcm_column_names():
    assert tcm_column("Employment (walking)") == "tcm_employment_walking"
    assert tcm_column("Leisure and Community (overall)") == "tcm_leisure_and_community_overall"
    assert tcm_column("Overall") == "tcm_overall"


def test_school_with_only_an_ungraded_inspection_gets_a_score():
    tq = TestSchoolQuality()
    r = tq._q(ungraded_outcome="School remains Good", ungraded_date=date(2026, 10, 1))
    assert r["framework"] == "ungraded_only"
    assert r["quality"] == pytest.approx(0.7)
    assert r["inspection_date"] == date(2026, 10, 1)
