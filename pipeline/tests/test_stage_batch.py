"""The batch riding with v0.6.0: chargers beyond OpenStreetMap, listed buildings, radon
potential, and the feature-layer access type they need."""

import json

import geopandas as gpd
import numpy as np
import polars as pl
import pytest
from shapely.geometry import box
from werkzeug import Request, Response

from lix_core.config import ArcgisLayerAccess
from lix_pipeline.fetch import arcgis
from lix_pipeline.fetch.session import make_session
from lix_pipeline.geo.contours import band_at_points
from lix_pipeline.stage.chargers import dedup, tidy_esb, tidy_ocm
from lix_pipeline.stage.heritage import tidy_niah
from lix_pipeline.stage.radon import high_share

# --- chargers ----------------------------------------------------------------------------


def test_tidy_esb_keeps_the_active_island_nations_and_places_sites():
    raw = pl.DataFrame(
        {
            "﻿Territory": ["RoI", "NI", "RoI"],
            "County": ["Dublin", "Antrim", "Cork"],
            "Address": ["Grafton St", "Belfast", "Patrick St"],
            "Nr. Chargers": ["2", "1", ""],
            "Latitude": ["53.3418", "54.5973", "51.8985"],
            "Longitude": ["-6.2603", "-5.9301", "-8.4756"],
        }
    )
    out = tidy_esb(raw, ("I",))
    assert out["name"].to_list() == ["Grafton St", "Patrick St"]
    assert out["points"].to_list() == [2.0, None]
    assert out["source"].unique().to_list() == ["ESB ecars"]
    # Dublin on the British National Grid (about 117 km east, 391 km north of the origin)
    assert 100_000 < out["x"][0] < 130_000 and 380_000 < out["y"][0] < 400_000


def test_tidy_ocm_keeps_open_providers_only_and_names_them():
    def poi(id_, provider_id, title, open_, lat=51.5, lon=-0.1, **extra):
        provider = {"ID": provider_id, "Title": title, "IsOpenDataLicensed": open_, **extra}
        address = {"Title": f"site {id_}", "Latitude": lat, "Longitude": lon}
        return {"ID": id_, "DataProviderID": provider_id, "DataProvider": provider,
                "AddressInfo": address}  # fmt: skip

    pois = [
        {**poi(1, 1, "Open Charge Map Contributors", True), "NumberOfPoints": 4},
        poi(2, 15, "Closed Importer", False, lon=-0.2),
        {
            **poi(3, 18, "Open Importer", True, lat=51.6, lon=-0.3, License="OGL v3"),
            "NumberOfPoints": None,
        },  # fmt: skip
        {"ID": 4, "DataProviderID": 1, "AddressInfo": {"Title": "no coordinates"}},
    ]
    out = tidy_ocm(pois)
    assert out["charger_id"].to_list() == ["ocm:1", "ocm:3"]
    assert out["licence"].to_list() == ["CC-BY-4.0", "OGL v3"]
    assert out["provider"].to_list() == ["Open Charge Map Contributors", "Open Importer"]
    assert out["points"].to_list() == [4.0, None]


def test_dedup_drops_sites_within_fifty_metres_of_a_kept_one():
    kept = pl.DataFrame({"x": [1000.0, 5000.0], "y": [1000.0, 5000.0]})
    new = pl.DataFrame(
        {
            "name": ["same", "far", "same2"],
            "x": [1030.0, 2000.0, 4990.0],
            "y": [1000.0, 2000.0, 5040.0],
        }
    )
    assert dedup(kept, new)["name"].to_list() == ["far"]
    assert dedup(pl.DataFrame({"x": [], "y": []}), new).height == 3


# --- heritage ----------------------------------------------------------------------------


def test_tidy_niah_keeps_regional_rating_and_above():
    raw = pl.DataFrame(
        {
            "REG_NO": [1, 2, 3, 4],
            "NAME": ["a", "b", "c", "d"],
            "RATING": ["Regional", "Local", "National", "Record only"],
            "x": [1.0, 2.0, 3.0, 4.0],
            "y": [1.0, 2.0, 3.0, 4.0],
        }
    )
    out = tidy_niah(raw)
    assert out["ref"].to_list() == ["1", "3"]
    assert out["grade"].to_list() == ["Regional", "National"]
    assert out["source"][0] == "National Inventory of Architectural Heritage"


# --- radon -------------------------------------------------------------------------------


def _squares(n: int, size: float = 1000.0) -> gpd.GeoDataFrame:
    """An n × n grid of squares, value = the row index."""
    rows = []
    for i in range(n):
        for j in range(n):
            rows.append(
                {
                    "value": float(i),
                    "geometry": box(j * size, i * size, (j + 1) * size, (i + 1) * size),
                }
            )
    return gpd.GeoDataFrame(rows, crs="EPSG:27700")  # fmt: skip


def test_band_at_points_takes_the_tree_route_for_many_small_polygons():
    squares = _squares(40)  # 1,600 squares: above the tree threshold
    x = np.array([500.0, 1500.0, 39_500.0, -10.0])
    y = np.array([500.0, 1500.0, 39_500.0, 500.0])
    out = band_at_points(squares, "value", x, y)
    assert out[:3].tolist() == [0.0, 1.0, 39.0]
    assert np.isnan(out[3])
    # The same answer from the per-polygon route
    small = _squares(3)
    assert band_at_points(small, "value", x[:2], y[:2]).tolist() == [0.0, 1.0]


def test_high_share_counts_homes_in_squares_at_or_above_the_threshold():
    squares = _squares(3)  # values 0, 1, 2 by row
    homes = pl.DataFrame(
        {
            "lsoa21cd": ["A", "A", "A", "A", "B", "B"],
            "east1m": [500.0, 500.0, 500.0, 500.0, 500.0, 99_999.0],
            "north1m": [500.0, 1500.0, 2500.0, 2500.0, 2500.0, 500.0],
        }
    )
    out = high_share(homes, squares, threshold=2).sort("lsoa21cd")
    # A: 2 of 4; B: 1 of the 1 home the map covers (the home off the map is unknown, #149)
    assert out["high_share"].to_list() == [50.0, 100.0]
    nowhere = homes.with_columns(pl.lit(99_999.0).alias("east1m"))
    assert high_share(nowhere, squares, threshold=2)["high_share"].to_list() == [None, None]


# --- a layer by URL ----------------------------------------------------------------------


@pytest.fixture
def layer_server(httpserver):
    layer = {
        "name": "Listed Buildings",
        "objectIdField": "FID",
        "fields": [
            {"name": "FID", "type": "esriFieldTypeOID"},
            {"name": "REF", "type": "esriFieldTypeInteger"},
        ],
        "maxRecordCount": 2,
        "advancedQueryCapabilities": {"supportsPagination": False},
        "editingInfo": {"lastEditDate": 1_700_000_000_000},
    }  # fmt: skip
    rows = {i: {"FID": i, "REF": 100 + i} for i in (3, 5, 8, 13, 21)}

    def query(request: Request) -> Response:
        args = request.args
        if args.get("returnCountOnly") == "true":
            return Response(json.dumps({"count": len(rows)}), content_type="application/json")
        if args.get("returnIdsOnly") == "true":
            return Response(
                json.dumps({"objectIdFieldName": "FID", "objectIds": sorted(rows, reverse=True)}),
                content_type="application/json",
            )
        if "resultOffset" in args:
            return Response(
                json.dumps({"error": {"code": 400, "message": "Pagination is not supported."}}),
                content_type="application/json",
            )
        lo, hi = (int(t) for t in __import__("re").findall(r"\d+", args["where"]))
        feats = [
            {"attributes": r, "geometry": {"x": r["FID"] * 10.0, "y": 1.0}}
            for i, r in rows.items()
            if lo <= i <= hi
        ]
        return Response(json.dumps({"features": feats}), content_type="application/json")

    httpserver.expect_request("/layer/0").respond_with_json(layer)
    httpserver.expect_request("/layer/0/query").respond_with_handler(query)
    return httpserver


def test_resolve_layer_versions_by_last_edit(layer_server):
    access = ArcgisLayerAccess(type="arcgis_layer", url=layer_server.url_for("/layer/0"))
    resolved = arcgis.resolve_layer(access, make_session())
    assert resolved["url"].endswith("/layer/0")
    assert resolved["version"].startswith("2023-11-14")
    assert resolved["title"] == "Listed Buildings"


def test_fetch_featureserver_pages_by_object_id_ranges_without_pagination(layer_server, tmp_path):
    access = ArcgisLayerAccess(
        type="arcgis_layer", url=layer_server.url_for("/layer/0"), fields=["REF"], geometry="point"
    )
    out = tmp_path / "hes.parquet"
    n = arcgis.fetch_featureserver(access.url, access, out, make_session(), workers=2)
    assert n == 5
    df = pl.read_parquet(out).sort("FID")
    assert df["FID"].to_list() == [3, 5, 8, 13, 21]
    assert df["x"].to_list() == [30.0, 50.0, 80.0, 130.0, 210.0]
