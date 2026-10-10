"""Tests for the Phase 2b stagers (health, safety, environment, transport, childcare,
housing, community), on small files shaped like the real ones."""

from pathlib import Path

import polars as pl
import pytest

from lix_pipeline.stage.childcare import stage_ofsted_childcare
from lix_pipeline.stage.community import stage_claimant_count
from lix_pipeline.stage.environment import stage_ea_flood_all_postcodes, stage_ea_flood_postcodes
from lix_pipeline.stage.health import stage_nhsbsa_pharmacies, stage_ods_dentists
from lix_pipeline.stage.housing import stage_voa_ctsop
from lix_pipeline.stage.safety import stage_stats19
from lix_pipeline.stage.schools import NEUTRAL, OEIF_SCALE
from lix_pipeline.stage.transport import stage_naptan, stage_ofcom_broadband

# Postcode → (easting, northing, LSOA); "CF10 1AA" is Welsh, "ZZ1 1ZZ" isn't in NSPL
POSTCODES = {
    "AB1 2CD": (530000, 180000, "E01000001"),
    "EF3 4GH": (531000, 181000, "E01000002"),
    "CF10 1AA": (318000, 176000, "W01000001"),
}


@pytest.fixture
def data(tmp_path: Path, monkeypatch) -> Path:
    """A data dir with a tiny staged NSPL, so postcode geocoding works offline."""
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    staged = tmp_path / "staged"
    staged.mkdir()
    pl.DataFrame(
        {
            "postcode": list(POSTCODES),
            "postcode_norm": [p.replace(" ", "") for p in POSTCODES],
            "east1m": [v[0] for v in POSTCODES.values()],
            "north1m": [v[1] for v in POSTCODES.values()],
            "lsoa21cd": [v[2] for v in POSTCODES.values()],
            "ctry_cd": ["E92000001", "E92000001", "W92000004"],
            "live": [True, True, True],
        }
    ).write_parquet(staged / "nspl.parquet")
    return tmp_path


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


def _ods_row(code: str, name: str, postcode: str, status: str) -> str:
    cells = [""] * 27
    cells[0], cells[1], cells[9], cells[12] = code, name, postcode, status
    return ",".join(f'"{c}"' for c in cells) + "\n"


def test_dentists_active_in_england_only(data):
    _write(
        data / "raw" / "ods_dentists" / "ods_dentists.csv",
        _ods_row("V1", "OPEN DENTAL", "AB1 2CD", "ACTIVE")
        + _ods_row("V2", "CLOSED DENTAL", "EF3 4GH", "INACTIVE")
        + _ods_row("V3", "WELSH DENTAL", "CF10 1AA", "ACTIVE"),
    )
    df = stage_ods_dentists().collect()
    assert df.select("practice_code", "lsoa21cd", "x").rows() == [("V1", "E01000001", 530000)]


def test_pharmacies_drop_appliance_contractors(data):
    header = (
        "PHARMACY_ODS_CODE_F_CODE,PHARMACY_TRADING_NAME,POST_CODE,"
        "WEEKLY_TOTAL,SUN_TOTAL,CONTRACT_TYPE\n"
    )
    _write(
        data / "raw" / "nhsbsa_pharmacies" / "nhsbsa_pharmacies.csv",
        header
        + "FA1, BOOTS ,AB1 2CD,40.5,0,Community\n"
        + "FA2,STOMA SUPPLIES,EF3 4GH,40,0,DAC\n"
        + "FA3,RURAL,EF3 4GH,30,4,LPS\n",
        encoding="utf-8-sig",
    )
    df = stage_nhsbsa_pharmacies().collect().sort("pharmacy_code")
    assert df["pharmacy_code"].to_list() == ["FA1", "FA3"]
    assert df["name"][0] == "BOOTS"
    assert df["sunday_hours"].to_list() == [0.0, 4.0]


def test_stats19_england_with_location(data):
    _write(
        data / "raw" / "stats19" / "stats19.csv",
        "collision_index,collision_year,collision_severity,location_easting_osgr,"
        "location_northing_osgr,local_authority_ons_district\n"
        "1,2025,1,530000,180000,E09000001\n"
        "2,2025,3,NULL,NULL,E09000001\n"
        "3,2024,2,318000,176000,W06000015\n",
    )
    df = stage_stats19().collect()
    assert df.select("collision_index", "severity", "year").rows() == [("1", "fatal", 2025)]


def test_flood_postcodes_summed_per_lsoa(data):
    cols = ["PC", "cntpc", "RES_cntpc"] + [
        f"RES_CNT_{b}" for b in ("VeryLow", "Low", "Medium", "High")
    ]
    rows = [
        ["AB1 2CD", "10", "10", "0", "2", "3", "1"],
        ["VPO01148", "500", "500", "500", "0", "0", "0"],  # pseudo-postcode
        ["CF10 1AA", "5", "5", "0", "0", "5", "0"],  # Wales
    ]
    _write(
        data / "raw" / "ea_flood_postcodes" / "DATA" / "x.RoFRS_Postcodes_AtRisk.csv",
        ",".join(cols) + "\n" + "".join(",".join(r) + "\n" for r in rows),
        encoding="utf-8-sig",
    )
    df = stage_ea_flood_postcodes().collect()
    assert df.rows(named=True) == [
        {"lsoa21cd": "E01000001", "res_high": 1, "res_medium": 3, "res_low": 2, "res_verylow": 0}
    ]


def test_flood_all_sources_summed_per_lsoa(data):
    rows = [
        "AB1 2CD,3,4,10,Unlikely",
        "EF3 4GH,0,0,0,Possible",
        "ZZ1 1ZZ,9,9,9,Unlikely",  # not in NSPL
        "CF10 1AA,1,0,0,Unlikely",  # Wales, out of an England-only build
    ]
    _write(
        data / "raw" / "ea_flood_postcode_tool" / "ea_flood_postcode_tool.csv",
        "Postcode,HIGH_CNT,MED_CNT,LOW_CNT,GWTR_RISK\n" + "\n".join(rows) + "\n",
        encoding="utf-8-sig",  # the real file starts with a byte-order mark
    )
    df = stage_ea_flood_all_postcodes().collect().sort("lsoa21cd")
    assert df.rows(named=True) == [
        {
            "lsoa21cd": "E01000001",
            "any_high": 3,
            "any_medium": 4,
            "any_low": 10,
            "share_groundwater": 0.0,
        },
        {
            "lsoa21cd": "E01000002",
            "any_high": 0,
            "any_medium": 0,
            "any_low": 0,
            "share_groundwater": 1.0,
        },
    ]


def test_naptan_modes_and_status(data):
    _write(
        data / "raw" / "naptan" / "naptan.csv",
        "ATCOCode,CommonName,LocalityName,StopType,Easting,Northing,Status\n"
        "1,High St,Town,BCT,530000,180000,active\n"
        "2,Old Stop,Town,BCT,530100,180100,inactive\n"
        "3,Town Station,Town,RLY,530500,180500,active\n"
        "4,Taxi Rank,Town,TXR,530600,180600,active\n",
    )
    df = stage_naptan().collect()
    assert df.select("atco_code", "mode").rows() == [("1", "bus"), ("3", "rail")]


def test_ofcom_output_areas_summed_to_lsoa(data):
    pl.DataFrame(
        {"oa21cd": ["E00000001", "E00000002"], "lsoa21cd": ["E01000001", "E01000001"]}
    ).write_parquet(data / "staged" / "oa_lookup.parquet")
    _write(
        data / "raw" / "ofcom_broadband" / "x" / "202601_fixed_oa_res_coverage_r1.csv",
        "output_area,All Premises,Number of premises with Gigabit availability,"
        "Number of premises with SFBB availability,"
        "Number of premises unable to receive decent broadband from fixed or FWA\n"
        "E00000001,100,100,100,0\n"
        "E00000002,100,50,90,10\n"
        "S00000001,80,0,0,80\n",
    )
    df = stage_ofcom_broadband().collect()
    row = df.row(0, named=True)
    assert df.height == 1
    assert row["premises"] == 200
    assert row["gigabit_pct"] == pytest.approx(75)
    assert row["below_uso_pct"] == pytest.approx(5)


def test_childcare_nurseries_with_quality(data):
    prefix = "EYR REIF: Most recent: "
    header = [
        "Web link", "Provider URN", "Provider type", "Provider subtype",
        "Provider Early Years Register flag", "Provider name", "Provider postcode", "Places",
        f"{prefix}Inclusion", f"{prefix}Leadership and governance",
        "EYR OEIF/CIF: Most recent: Overall effectiveness",
    ]  # fmt: skip
    rows = [
        ["l", "1", "Childcare on non-domestic premises", "Full day care", "Y", "New grades",
         "AB1 2CD", "60", "Strong standard", "Strong standard", ""],
        ["l", "2", "Childcare on non-domestic premises", "Sessional day care", "Y", "Old grade",
         "EF3 4GH", "24", "", "", "1"],
        ["l", "3", "Childcare on non-domestic premises", "Full day care", "Y", "New setting",
         "EF3 4GH", "40", "", "", ""],
        ["l", "4", "Childminder", "", "Y", "Redacted", "REDACTED", "6", "", "", "2"],
        ["l", "5", "Childcare on non-domestic premises", "Out-of-school day care", "N", "Club",
         "AB1 2CD", "30", "", "", "2"],
    ]  # fmt: skip
    text = "Registered childcare providers as at 30 June 2026,,\n"
    text += "This worksheet contains one table.,,\n"
    text += ",".join(header) + "\n" + "".join(",".join(r) + "\n" for r in rows)
    _write(data / "raw" / "ofsted_childcare" / "ofsted_childcare.csv", text, encoding="utf-8-sig")
    df = stage_ofsted_childcare().collect().sort("urn")
    assert df["urn"].to_list() == ["1", "2", "3"]
    assert df["quality"].to_list() == pytest.approx([0.8, OEIF_SCALE["1"], NEUTRAL])
    assert df["quality_source"].to_list() == [
        "renewed framework",
        "previous framework",
        "not yet inspected",
    ]


def test_voa_stock_by_band_and_period(data):
    bp = ["bp_pre_1900", "bp_1900_1918", "bp_2000_2008", "bp_2026", "bp_unkw"]
    header = ["geography", "ecode", "band", *bp, "all_properties"]
    rows = [
        ["LSOA", "E01000001", "All", "10", "20", "30", "-", "0", "100"],
        ["LSOA", "E01000001", "A", "10", "0", "0", "0", "0", "40"],
        ["LSOA", "E01000001", "B", "0", "20", "0", "0", "0", "60"],
        ["LAUA", "E09000001", "All", "1", "1", "1", "1", "1", "5"],
    ]
    _write(
        data / "raw" / "voa_ctsop" / "CTSOP_4_1" / "CTSOP4_1_2026_03_31.csv",
        ",".join(header) + "\n" + "".join(",".join(r) + "\n" for r in rows),
    )
    df = stage_voa_ctsop().collect()
    row = df.row(0, named=True)
    assert df.height == 1
    assert row["dwellings"] == 100
    assert row["built_pre_1919"] == 30
    assert row["built_2000_on"] == 30  # the suppressed "-" counts as nothing
    assert (row["band_a"], row["band_b"]) == (40, 60)


def test_claimants_england_lsoas(data):
    _write(
        data / "raw" / "claimant_count" / "claimant_count.csv",
        '"DATE_NAME","GEOGRAPHY_CODE","OBS_VALUE"\n'
        '"August 2026","E01000001",55\n"August 2026","W01000001",5\n',
    )
    df = stage_claimant_count().collect()
    assert df.rows() == [("E01000001", "August 2026", 55)]
