"""Compile config/data/ae_sites.csv: emergency departments and urgent-care sites.

    python -m lix_pipeline.curate.ae_sites

Scotland's rows come from Public Health Scotland's accident and emergency site list (every
site that reports emergency activity, with its department type and postcode). England's,
Wales's and Northern Ireland's come from OpenStreetMap hospitals tagged ``emergency=yes``
(no official open site list exists: NHS ODS carries no emergency-department flag), with
the rows marked type 1 unless their name says minor injuries, urgent care or similar. Each
row records
its source and the date it was compiled, so the list can be checked and corrected by
hand.
"""

import csv
from datetime import date
from pathlib import Path

import polars as pl

from lix_core.codes import nation_of
from lix_core.log import setup_logging
from lix_core.paths import data_dir, get_project_root

logger = setup_logging("curate.ae_sites")

COLUMNS = ["name", "nation", "type", "postcode", "lat", "lon", "source", "source_url", "checked_on"]
PHS_URL = "https://www.opendata.nhs.scot/dataset/nhs-scotland-accident-emergency-sites"
OSM_URL = "https://www.openstreetmap.org"
# Names that say a hospital's emergency tag means a minor injuries or urgent care unit
NOT_TYPE1 = (
    r"(?i)minor injur|urgent care|urgent treatment|walk-in|community hospital|cottage"
    r"|day hospital|mental health|psychiatric|hospice|eye hospital|dental|maternity"
)


def scotland_rows(today: str) -> list[dict]:
    raw = pl.read_csv(
        data_dir("raw") / "phs_ae_sites" / "phs_ae_sites.csv",
        infer_schema_length=0,
        encoding="utf8-lossy",
    )
    rows = []
    for r in raw.filter(pl.col("Status").str.to_lowercase() == "open").iter_rows(named=True):
        kind = (
            "type1"
            if (r.get("CurrentDepartmentType") or "").strip().lower() == "type 1"
            else "other"
        )
        rows.append(
            {
                "name": r["TreatmentLocationName"],
                "nation": "S",
                "type": kind,
                "postcode": r.get("TreatmentLocationPostcode") or "",
                "lat": "",
                "lon": "",
                "source": f"Public Health Scotland A&E sites ({r['TreatmentLocationCode']})",
                "source_url": PHS_URL,
                "checked_on": today,
            }
        )
    return rows


def osm_rows(today: str, nations: tuple[str, ...] = ("E", "W", "N")) -> list[dict]:
    pois = pl.read_parquet(data_dir("staged") / "osm_pois.parquet")
    if "emergency" not in pois.columns:
        raise ValueError("osm_pois lacks the emergency tag: re-run `lix stage --slug osm_pois`")
    hospitals = pois.filter(
        (pl.col("value") == "hospital")
        & (pl.col("emergency") == "yes")
        & pl.col("name").is_not_null()
    )
    from lix_pipeline.geo.joins import points_to_lsoa

    hospitals = points_to_lsoa(
        hospitals.select("osm_type", "osm_id", "name", "x", "y", "lon", "lat")
    )
    hospitals = hospitals.filter(nation_of("lsoa21cd").is_in(list(nations)))
    kinds = {"n": "node", "w": "way", "r": "relation"}
    rows = []
    for r in hospitals.iter_rows(named=True):
        kind = "other" if pl.Series([r["name"]]).str.contains(NOT_TYPE1).item() else "type1"
        osm = f"{kinds.get(r['osm_type'], r['osm_type'])} {r['osm_id']}"
        rows.append(
            {
                "name": r["name"],
                "nation": r["lsoa21cd"][0],
                "type": kind,
                "postcode": "",
                "lat": f"{r['lat']:.5f}",
                "lon": f"{r['lon']:.5f}",
                "source": f"OpenStreetMap {osm} (hospital, emergency=yes)",
                "source_url": f"{OSM_URL}/{osm.replace(' ', '/')}",
                "checked_on": today,
            }
        )
    return rows


def compile_list(out: Path | None = None) -> Path:
    out = out or get_project_root() / "config" / "data" / "ae_sites.csv"
    today = date.today().isoformat()
    rows = scotland_rows(today) + osm_rows(today)
    rows.sort(key=lambda r: (r["nation"], r["name"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    by = pl.DataFrame(rows).group_by("nation", "type").agg(pl.len()).sort("nation", "type")
    logger.info(f"{len(rows)} sites → {out}: {by.rows()}")
    return out


if __name__ == "__main__":
    compile_list()
