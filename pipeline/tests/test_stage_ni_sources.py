"""Northern Ireland's source readers: capital value bands, DE enrolments, LPS ward
summaries, PSNI districts, TransXChange departures and DAERA contours."""

from datetime import date

import geopandas as gpd
import numpy as np
import polars as pl
import pytest
from shapely.geometry import box

from lix_pipeline.stage.deprivation import NIMDM_RANKS, rank_to_score
from lix_pipeline.stage.housing import CAPITAL_VALUE_BANDS, CAPITAL_VALUE_EDGES, median_from_bands
from lix_pipeline.stage.noise import CONTOUR_BANDS, contour_db_at_points
from lix_pipeline.stage.price_paid import _lps_summary, _lps_ward_years, _plain
from lix_pipeline.stage.safety import PSNI_DISTRICTS
from lix_pipeline.stage.schools import de_pupils
from lix_pipeline.stage.txc import read_txc, seconds, txc_departures


def test_median_from_bands_interpolates_within_the_middle_band():
    # 10 homes under £40k, 10 in £40k–52k: the median sits at the top of the first band
    counts = [10, 10, 0, 0, 0, 0, 0, 0]
    assert median_from_bands(counts, CAPITAL_VALUE_EDGES) == pytest.approx(40_000)
    # All in the open top band: halfway through its assumed range
    counts = [0, 0, 0, 0, 0, 0, 0, 4]
    assert median_from_bands(counts, CAPITAL_VALUE_EDGES) == pytest.approx(480_000)
    assert median_from_bands([0] * 8, CAPITAL_VALUE_EDGES) is None
    assert len(CAPITAL_VALUE_BANDS) + 1 == len(CAPITAL_VALUE_EDGES)


def test_de_pupils_prefers_a_total_column_and_sums_year_groups_otherwise():
    with_total = pl.DataFrame(
        {
            "DENI ref": ["1010012"],
            "School name": ["Belmont"],
            "Total: full-time pupils": ["400"],
            "Total: total pupils": ["410"],
        }
    )
    assert de_pupils(with_total).row(0) == ("1010012", 410)
    years = pl.DataFrame(
        {
            "DENI ref": ["1010013"],
            "School name": ["Other"],
            "Mainstream class: Year 1": ["20"],
            "Mainstream class: Year 2": ["22"],
        }
    )
    assert de_pupils(years).row(0) == ("1010013", 42)


def test_lps_summary_pools_five_years_and_reads_the_latest():
    raw = pl.DataFrame(
        {
            "District": ["BELFAST"] * 7,
            "Ward": ["Boho, Cleenish and Letterbreen"] * 7,
            "SaleYr": [str(y) for y in range(2018, 2025)],
            "Type": ["All Sales"] * 7,
            "Ward_VerifiedSales": ["10", "10", "10", "10", "10", "10", "20"],
            "Reporting_MedianPrice": [
                "100000",
                "110000",
                "120000",
                "130000",
                ".",
                "150000",
                "160000",
            ],
        }
    )
    ward_years = _lps_ward_years(raw)
    assert ward_years["ward"][0] == "BOHO CLEENISH AND LETTERBREEN"
    out = _lps_summary(ward_years, 2024).row(0, named=True)
    assert out["median_price_12m"] == 160_000 and out["transaction_count_12m"] == 20
    assert out["median_prev"] == 150_000
    # 2020–2024, the 2022 median suppressed: sales-weighted mean of the others
    assert out["median_price_5y"] == pytest.approx((120 + 130 + 150 + 2 * 160) * 10_000 / 50)
    assert out["transaction_count_5y"] == 60
    assert _plain(pl.Series(["Newry, Mourne & Down"])).item() == "NEWRY MOURNE DOWN"


def test_psni_districts_cover_the_eleven_councils():
    assert len(PSNI_DISTRICTS) == 11
    assert set(PSNI_DISTRICTS.values()) == {f"N090000{n:02d}" for n in range(1, 12)}


def test_nimdm_ranks_become_within_nation_scores():
    assert set(NIMDM_RANKS.values()) >= {"imd_score", "access_score", "crime_score"}
    df = pl.DataFrame({"rank": [1.0, 2.0, 3.0, 4.0]})
    out = df.select(rank_to_score(pl.col("rank"), pl.col("rank").count()).alias("s"))
    assert out["s"].to_list() == [100.0, 75.0, 50.0, 25.0]  # rank 1 (most deprived) → 100


def test_duration_parsing():
    assert seconds("PT2M") == 120 and seconds("PT1M30S") == 90 and seconds("PT1H") == 3600
    assert seconds(None) == 0 and seconds("PT0S") == 0


TXC = """<?xml version="1.0" encoding="utf-8"?>
<TransXChange xmlns="http://www.transxchange.org.uk/" SchemaVersion="2.4">
<StopPoints>
<AnnotatedStopPointRef>
<StopPointRef>A</StopPointRef>
<CommonName>First</CommonName>
<Location>
<Longitude>-5.93</Longitude>
<Latitude>54.60</Latitude>
</Location>
</AnnotatedStopPointRef>
<AnnotatedStopPointRef>
<StopPointRef>B</StopPointRef>
<CommonName>Second</CommonName>
<Location>
<Longitude>-5.92</Longitude>
<Latitude>54.61</Latitude>
</Location>
</AnnotatedStopPointRef>
<AnnotatedStopPointRef>
<StopPointRef>C</StopPointRef>
<CommonName>Last</CommonName>
<Location>
<Longitude>-5.91</Longitude>
<Latitude>54.62</Latitude>
</Location>
</AnnotatedStopPointRef>
</StopPoints>
<JourneyPatternSections>
<JourneyPatternSection id="S1">
<JourneyPatternTimingLink id="L1">
<From>
<Activity>pickUp</Activity>
<StopPointRef>A</StopPointRef>
</From>
<To>
<Activity>pickUpAndSetDown</Activity>
<StopPointRef>B</StopPointRef>
</To>
<RunTime>PT5M</RunTime>
</JourneyPatternTimingLink>
<JourneyPatternTimingLink id="L2">
<From>
<StopPointRef>B</StopPointRef>
</From>
<To>
<Activity>setDown</Activity>
<StopPointRef>C</StopPointRef>
</To>
<RunTime>PT10M</RunTime>
</JourneyPatternTimingLink>
</JourneyPatternSection>
</JourneyPatternSections>
<Services>
<Service>
<ServiceCode>SVC</ServiceCode>
<OperatingPeriod>
<StartDate>2026-09-21</StartDate>
<EndDate>2027-06-30</EndDate>
</OperatingPeriod>
<OperatingProfile>
<RegularDayType>
<DaysOfWeek>
<MondayToFriday/>
</DaysOfWeek>
</RegularDayType>
</OperatingProfile>
<StandardService>
<JourneyPattern id="JP1">
<JourneyPatternSectionRefs>S1</JourneyPatternSectionRefs>
</JourneyPattern>
</StandardService>
</Service>
</Services>
<VehicleJourneys>
<VehicleJourney>
<ServiceRef>SVC</ServiceRef>
<JourneyPatternRef>JP1</JourneyPatternRef>
<DepartureTime>06:58:00</DepartureTime>
</VehicleJourney>
<VehicleJourney>
<ServiceRef>SVC</ServiceRef>
<JourneyPatternRef>JP1</JourneyPatternRef>
<DepartureTime>08:00:00</DepartureTime>
</VehicleJourney>
<VehicleJourney>
<ServiceRef>SVC</ServiceRef>
<JourneyPatternRef>JP1</JourneyPatternRef>
<DepartureTime>18:50:00</DepartureTime>
<OperatingProfile>
<RegularDayType>
<DaysOfWeek>
<Sunday/>
</DaysOfWeek>
</RegularDayType>
</OperatingProfile>
</VehicleJourney>
</VehicleJourneys>
</TransXChange>
"""


def test_txc_departures_count_boardable_daytime_stops(tmp_path):
    (tmp_path / "metro.xml").write_text(TXC)
    tt = read_txc(tmp_path / "metro.xml")
    assert len(tt.stops) == 3 and len(tt.journeys) == 3
    # 06:58 journey: A before 07:00 (not counted), B at 07:03 (counted); 08:00 journey:
    # A and B counted; the Sunday journey never; C is set-down only
    assert tt.departures() == {"A": 1, "B": 2}
    df = txc_departures(tmp_path).sort("stop_id")
    assert df["stop_id"].to_list() == ["A", "B"]
    assert df["departures"].to_list() == [1, 2]
    assert df["reference_day"][0] == date(2026, 10, 6)  # a Tuesday two weeks in
    assert df.filter(pl.col("stop_id") == "B")["name"][0] == "Second"


def test_contour_bands_take_the_floor_of_the_loudest_polygon():
    polys = gpd.GeoDataFrame(
        {"gridcode": [59, 64, 1000]},
        geometry=[box(0, 0, 10, 10), box(5, 0, 15, 10), box(20, 0, 30, 10)],
        crs=27700,
    )
    path = None
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = f"{d}/R3_con_lden.shp"
        polys.to_file(path)
        x = np.array([2.0, 7.0, 25.0, 50.0])
        y = np.array([5.0, 5.0, 5.0, 5.0])
        out = contour_db_at_points([path], x, y)
    assert out[0] == 55.0 and out[1] == 60.0 and out[2] == 75.0 and np.isnan(out[3])
    assert CONTOUR_BANDS[64] == 60.0


def test_logainm_records_parse_and_match_places():
    from lix_pipeline.stage.logainm import parse_places, with_irish_names

    records = [
        {
            "ID": 1,
            "Placenames": [
                {"Language": "ga", "Wording": "Béal Feirste", "Main": True},
                {"Language": "en", "Wording": "Belfast", "Main": True},
            ],
            "Categories": [{"NameEN": "City"}],
            "Geography": {
                "Accurate": True,
                "Coordinates": [{"Latitude": 54.597, "Longitude": -5.93}],
            },
            "NorthernIreland": {"Extent": "all"},
        },
        {
            "ID": 2,
            "Placenames": [{"Language": "en", "Wording": "Nowhere", "Main": True}],
            "Geography": {},
        },
    ]
    parsed = parse_places(records)
    assert parsed.row(0, named=True)["name_ga"] == "Béal Feirste"
    assert parsed.row(0, named=True)["category"] == "City"
    assert parsed.row(1, named=True)["lat"] is None
    logainm = parsed.filter(pl.col("name_ga").is_not_null()).with_columns(
        x=pl.lit(146_000.0), y=pl.lit(530_000.0), key=pl.lit("belfast")
    )
    places = pl.DataFrame(
        {
            "name": ["Belfast", "Belfast", "Bangor"],
            "name_ga": [None, "Béal Feirste (OSM)", None],
            "x": [146_500.0, 147_000.0, 170_000.0],
            "y": [530_500.0, 531_000.0, 540_000.0],
        }
    )
    out = with_irish_names(places, logainm)
    assert out["name_ga"].to_list() == ["Béal Feirste", "Béal Feirste (OSM)", None]


def test_lps_zero_sales_rows_have_no_median_and_the_weighted_mean_skips_nulls():
    from lix_pipeline.stage.price_paid import _lps_ward_years, _weighted

    raw = pl.DataFrame({
        "District": ["A", "A"], "Ward": ["X", "Y"], "SaleYr": ["2024", "2024"],
        "Ward_VerifiedSales": ["0", "40"], "Reporting_MedianPrice": ["0", "150000"],
    })  # fmt: skip
    assert _lps_ward_years(raw)["median"].to_list() == [None, 150000.0]
    df = pl.DataFrame({"v": [None, 100.0, 300.0], "w": [5.0, 1.0, 3.0]})
    assert df.select(_weighted("v", "w")).item() == 250.0
    none = pl.DataFrame({"v": [None], "w": [5.0]}, schema={"v": pl.Float64, "w": pl.Float64})
    assert none.select(_weighted("v", "w")).item() is None


def test_curated_names_fill_only_the_missing_forms_of_cities_and_towns():
    from lix_pipeline.stage.place_names import with_curated_names

    places = pl.DataFrame({
        "name": ["Edinburgh", "Cardiff", "Edinburgh"],
        "local_authority": ["City of Edinburgh", "Cardiff", "Elsewhere"],
        "place_type": ["City", "City", "Village"],
        "name_cy": [None, "Caerdydd", None],
        "name_gd": [None, None, None],
    })  # fmt: skip
    curated = pl.DataFrame({
        "name": ["Edinburgh", "Cardiff"],
        "local_authority": ["City of Edinburgh", "Cardiff"],
        "name_cy": ["Caeredin", "Caerdydd"],
        "name_gd": ["Dùn Èideann", ""],
        "name_ga": ["Dún Éideann", ""],
    })  # fmt: skip
    out = with_curated_names(places, curated)
    assert out["name_cy"].to_list() == ["Caeredin", "Caerdydd", None]
    assert out["name_gd"].to_list() == ["Dùn Èideann", None, None]
    assert out["name_ga"].to_list() == ["Dún Éideann", None, None]


def test_curated_names_match_the_alternative_name_too():
    """OpenStreetMap names the city "Derry/Londonderry"; the curated row says
    "Londonderry", so it must key on the alternative name as well (#132)."""
    from lix_pipeline.stage.place_names import with_curated_names

    places = pl.DataFrame({
        "name": ["Derry", "Strabane"],
        "name_alt": ["Londonderry", None],
        "local_authority": ["Derry City and Strabane", "Derry City and Strabane"],
        "place_type": ["City", "Town"],
    })  # fmt: skip
    curated = pl.DataFrame({
        "name": ["Londonderry", "Strabane"],
        "local_authority": ["Derry City and Strabane", "Derry City and Strabane"],
        "name_ga": ["Doire", "An Srath Bán"],
    })  # fmt: skip
    out = with_curated_names(places, curated)
    assert out["name_ga"].to_list() == ["Doire", "An Srath Bán"]
    assert "alt_name_ga" not in out.columns and "curated_name_ga" not in out.columns
