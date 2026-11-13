"""Phase 9 sources: school results, life expectancy, bus timetables, sports facilities."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from lix_pipeline.geo.access import nearby_max, nearby_mean
from lix_pipeline.stage.community import stage_life_expectancy
from lix_pipeline.stage.fsa import stage_active_places
from lix_pipeline.stage.schools import stage_ks2_results, stage_ks4_results
from lix_pipeline.stage.transport import reference_tuesday, stage_bods_gtfs


@pytest.fixture
def data(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    return tmp_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_ks2_prefers_the_three_year_average(data):
    header = "time_period,school_urn,breakdown,subject,expected_standard_pupil_percent\n"
    _write(
        data / "raw" / "ks2_results" / "ks2_results.csv",
        header
        + '202425,1001,Total,"Reading, writing and maths",70\n'
        + '202425,1001,3 year average,"Reading, writing and maths",64\n'
        + '202425,1002,Total,"Reading, writing and maths",55\n'
        + '202425,1003,Total,"Reading, writing and maths",x\n'
        + "202425,1002,Total,Reading,80\n",
    )
    df = stage_ks2_results().collect().sort("urn")
    assert df.select("urn", "ks2_rwm_pct").rows() == [(1001, 64.0), (1002, 55.0)]


def test_ks4_keeps_state_mainstream_all_pupils(data):
    cols = ["time_period", "school_urn", "establishment_type_group", "breakdown", "sex",
            "disadvantage_status", "first_language", "prior_attainment", "mobility",
            "attainment8_average", "pupil_count"]  # fmt: skip
    rows = [
        ["202425", "2001", "Academies", *["Total"] * 6, "48.2", "150"],
        ["202425", "2002", "Independent schools", *["Total"] * 6, "60.0", "80"],
        ["202425", "2003", "Academies", "Total", "Boys", *["Total"] * 4, "44.0", "75"],
        ["202425", "2004", "Community school", *["Total"] * 6, "40.0", "5"],
        ["202324", "2005", "Academies", *["Total"] * 6, "50.0", "120"],
    ]
    _write(
        data / "raw" / "ks4_results" / "ks4_results.csv",
        ",".join(cols) + "\n" + "".join(",".join(r) + "\n" for r in rows),
    )
    assert stage_ks4_results().collect().select("urn", "attainment8").rows() == [(2001, 48.2)]


def test_life_expectancy_mean_of_men_and_women(data):
    _write(
        data / "raw" / "life_expectancy" / "life_expectancy.csv",
        "Area Code,Sex,Category,Time period,Value\n"
        "E02000001,Male,,2019 - 23,80\nE02000001,Female,,2019 - 23,84\n"
        "E92000001,Male,,2019 - 23,79\n",
    )
    df = stage_life_expectancy().collect()
    assert df.select("msoa21cd", "le_mean").rows() == [("E02000001", 82.0)]


def test_active_places_public_and_operational(data):
    path = data / "raw" / "active_places" / "active_places.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({
        "facilityid": [1, 2, 3], "siteid": [10, 10, 11],
        "facilitytype": ["Swimming Pool", "Sports Hall", "Health and Fitness Gym"],
        "facilitysubtype": ["Main", "Main", "Gym"],
        "facstatus": ["Operational", "Closed", "Operational"],
        "accessibilitygroupstr": ["Public Access", "Public Access", "Private"],
        "accessibilitytypestr": ["Pay and Play"] * 3, "managementgroupstr": ["Local Authority"] * 3,
        "easting": [500000, 500100, 500200], "northing": [200000, 200000, 200000],
    }).write_parquet(path)  # fmt: skip
    assert stage_active_places().collect()["facilityid"].to_list() == [1]


def test_a_tuesday_the_feed_runs_far_fewer_trips_on_is_passed_over():
    """BODS's files of 12 November 2026: the Tuesday two weeks in, 1 December, runs 252,541
    trips against 464,419 the week before, and every English city read as almost busless."""
    start, end = date(2026, 11, 12), date(2028, 3, 31)
    trips = {
        date(2026, 11, 17): 456_496, date(2026, 11, 24): 464_419, date(2026, 12, 1): 252_541,
        date(2026, 12, 8): 469_248, date(2026, 12, 15): 464_285,
    }  # fmt: skip
    assert reference_tuesday(start, end) == date(2026, 12, 1)
    assert reference_tuesday(start, end, trips_on=trips.get) == date(2026, 11, 24)
    # A normal day stays, even when a later week runs a little more
    normal = trips | {date(2026, 12, 1): 460_000}
    assert reference_tuesday(start, end, trips_on=normal.get) == date(2026, 12, 1)
    # The week before is thin too: the week after
    both = trips | {date(2026, 11, 24): 250_000}
    assert reference_tuesday(start, end, trips_on=both.get) == date(2026, 12, 8)
    # Every week is thin (a feed that is ending): the day two weeks in stands
    thin = {d: 10 for d in trips}
    assert reference_tuesday(start, end, trips_on=thin.get) == date(2026, 12, 1)


def test_reference_tuesday():
    assert reference_tuesday(date(2026, 10, 7), date(2027, 7, 7)) == date(2026, 10, 27)
    # Never past the end of the timetable
    assert reference_tuesday(date(2026, 10, 7), date(2026, 10, 10)) == date(2026, 10, 10)


def test_bus_departures_follow_the_calendar(data):
    root = data / "raw" / "bods_gtfs"
    # Feed starts Wed 7 Oct 2026 → reference day Tue 27 Oct 2026
    _write(root / "calendar.txt",
           "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
           "weekday,1,1,1,1,1,0,0,20261007,20270707\n"
           "sunday,0,0,0,0,0,0,1,20261007,20270707\n"
           "removed,1,1,1,1,1,0,0,20261007,20270707\n")  # fmt: skip
    _write(
        root / "calendar_dates.txt",
        "service_id,date,exception_type\nremoved,20261027,2\nsunday,20261027,1\n",
    )
    _write(
        root / "trips.txt",
        "route_id,service_id,trip_id\nr,weekday,t1\nr,sunday,t2\nr,removed,t3\nr,weekday,t4\n",
    )
    _write(root / "stop_times.txt",
           "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type\n"
           "t1,08:00:00,08:00:00,A,1,0\n"   # counts
           "t2,09:00:00,09:00:00,A,1,0\n"   # added on the day: counts
           "t3,09:30:00,09:30:00,A,1,0\n"   # removed on the day
           "t4,06:30:00,06:30:00,A,1,0\n"   # before 07:00
           "t4,10:00:00,10:00:00,B,2,1\n")  # no pickup  # fmt: skip
    _write(
        root / "stops.txt",
        "stop_id,stop_name,stop_lat,stop_lon\nA,High St,53.8,-1.55\nB,Low St,53.81,-1.55\n",
    )
    df = stage_bods_gtfs().collect()
    assert df.select("stop_id", "departures").rows() == [("A", 2)]
    assert df["reference_day"][0] == date(2026, 10, 27)


def test_a_departure_is_counted_once_however_many_copies_of_the_timetable_say_so(data):
    """An operator publishes the next version of a timetable before the last one ends, and
    the feed holds both: First Bristol's 12,380 trips on 24 November 2026 were 6,967
    journeys, and a city's buses read double."""
    root = data / "raw" / "bods_gtfs"
    days = "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
    _write(root / "calendar.txt",
           f"service_id,{days},start_date,end_date\n"
           "v1,1,1,1,1,1,0,0,20261007,20270707\n"
           "v2,1,1,1,1,1,0,0,20261020,20270707\n")  # fmt: skip
    _write(root / "calendar_dates.txt", "service_id,date,exception_type\n")
    _write(root / "routes.txt",
           "route_id,agency_id,route_short_name,route_long_name,route_type\n"
           "r1,OP1,4,,3\nr1b,OP1,4,,3\nr2,OP1,5,,3\nr3,OP2,4,,3\n")  # fmt: skip
    _write(root / "trips.txt",
           "route_id,service_id,trip_id,direction_id\n"
           "r1,v1,a,0\nr1b,v2,b,0\n"      # the 4 at 08:00, in two versions of the timetable
           "r1,v1,c,1\n"                    # the 4 the other way
           "r2,v1,d,0\n"                    # the 5
           "r3,v1,e,0\n"                    # another operator's 4
           "r1b,v2,f,0\n")  # fmt: skip     # the new version's 08:10, which the old one lacks
    _write(root / "stop_times.txt",
           "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type\n"
           + "".join(f"{trip},{time},{time},A,1,0\n" for trip, time in [
               ("a", "08:00:00"), ("b", "08:00:00"), ("c", "08:00:00"), ("d", "08:00:00"),
               ("e", "08:00:00"), ("f", "08:10:00"),
           ]))  # fmt: skip
    _write(root / "stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nA,High St,53.8,-1.55\n")
    df = stage_bods_gtfs().collect()
    # Six trips, five departures: the two copies of the 08:00 number 4 are one bus
    assert df.select("stop_id", "departures").rows() == [("A", 5)]


ORIGINS = pl.DataFrame({
    "lsoa21cd": ["E01000001", "E01000001", "E01000002"],
    "east1m": [500_000, 500_100, 520_000], "north1m": [200_000, 200_000, 200_000],
})  # fmt: skip
POIS = pl.DataFrame(
    {"x": [500_050.0, 500_300.0, 530_000.0], "y": [200_000.0] * 3, "v": [80.0, 40.0, 10.0]}
)


def test_nearby_mean_weights_by_distance_and_falls_back_to_nearest():
    df = nearby_mean(ORIGINS, POIS, "v", radius_m=1000).sort("lsoa21cd")
    first, second = df["value"].to_list()
    assert 40 < first < 80  # both schools in range, the nearer one counts more
    assert second == pytest.approx(10.0)  # none within 1km: the nearest school's value


def test_nearby_max_takes_the_busiest_stop_in_range():
    df = nearby_max(ORIGINS, POIS, "v", radius_m=300).sort("lsoa21cd")
    assert df["value"].to_list() == [80.0, 0.0]


def test_nearest_k_mean_uses_the_nearest_few_and_falls_back():
    from lix_pipeline.geo.access import nearest_k_mean

    pois = pl.DataFrame({
        "x": [500_050.0, 500_300.0, 501_000.0, 535_000.0],
        "y": [200_000.0] * 4,
        "q": [0.9, 0.5, 0.1, 0.7],
    })  # fmt: skip
    df = nearest_k_mean(ORIGINS, pois, "q", k=2, sigma_m=500, max_m=5000).sort("lsoa21cd")
    near, remote = df["value"].to_list()
    assert 0.5 < near < 0.9  # the two nearest only; the 0.1 school is third
    # Nothing within 5km: the single nearest school (0.7, 15km away) counts
    assert remote == pytest.approx(0.7)
