"""Tests for resolvers (ArcGIS, GOV.UK, CKAN, Nomis), paged fetchers and the fetch orchestrator."""

import json
from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from werkzeug import Request, Response

from lix_core.config import (
    ArcgisItemAccess,
    CkanAccess,
    DatasetSpec,
    GovukAttachmentAccess,
    NomisAccess,
)
from lix_pipeline import fetch as fetch_mod
from lix_pipeline.fetch import ManualDownloadRequired, arcgis, ckan, fetch, govuk, nomis, select
from lix_pipeline.fetch.lock import read_lock
from lix_pipeline.fetch.session import make_session

ONS_QUERY = 'title:"National Statistics Postcode Lookup" AND owner:ONSGeography_data'


def _arcgis_access(**overrides) -> ArcgisItemAccess:
    base = dict(
        type="arcgis_item",
        query=ONS_QUERY,
        title_regex=r"^National Statistics Postcode Lookup \([A-Z][a-z]+ \d{4}\)$",
        item_type="CSV Collection",
        pin="pinned0",
        mode="data",
    )
    return ArcgisItemAccess(**{**base, **overrides})


@pytest.fixture
def arcgis_server(httpserver, monkeypatch):
    monkeypatch.setattr(arcgis, "ARCGIS", httpserver.url_for("/sharing/rest"))
    return httpserver


class TestResolveArcgisItem:
    SEARCH = {
        "results": [
            # Newest overall, but wrong type
            {"id": "table1", "type": "Feature Service", "modified": 300,
             "title": "National Statistics Postcode Lookup (August 2026) for the UK (Table)"},
            # Matches type but not title
            {"id": "guide1", "type": "CSV Collection", "modified": 250,
             "title": "National Statistics Postcode Lookup (August 2026) User Guide"},
            {"id": "aug26", "type": "CSV Collection", "modified": 200,
             "title": "National Statistics Postcode Lookup (August 2026)"},
            {"id": "may26", "type": "CSV Collection", "modified": 100,
             "title": "National Statistics Postcode Lookup (May 2026)"},
        ]
    }  # fmt: skip

    def test_picks_newest_matching_item(self, arcgis_server):
        arcgis_server.expect_request("/sharing/rest/search").respond_with_json(self.SEARCH)
        resolved = arcgis.resolve_item(_arcgis_access(), make_session())
        assert resolved["item_id"] == "aug26"
        assert resolved["title"] == "National Statistics Postcode Lookup (August 2026)"
        assert resolved["url"].endswith("/sharing/rest/content/items/aug26/data")

    def test_falls_back_to_pin_when_search_finds_nothing(self, arcgis_server):
        arcgis_server.expect_request("/sharing/rest/search").respond_with_json({"results": []})
        arcgis_server.expect_request("/sharing/rest/content/items/pinned0").respond_with_json(
            {"id": "pinned0", "title": "Pinned", "modified": 5, "type": "CSV Collection"}
        )
        resolved = arcgis.resolve_item(_arcgis_access(), make_session())
        assert resolved["item_id"] == "pinned0"

    def test_hub_export_and_featureserver_urls(self, arcgis_server):
        item = {"id": "fs1", "title": "T", "modified": 1, "type": "Feature Service",
                "url": "https://services1.arcgis.com/x/FeatureServer/"}  # fmt: skip
        arcgis_server.expect_request("/sharing/rest/content/items/fs1").respond_with_json(item)
        session = make_session()

        hub = _arcgis_access(query=None, pin="fs1", mode="hub_export", export_format="geoPackage")
        # The ONS geoportal unless the entry names another hub (Tailte Éireann's portal)
        assert arcgis.resolve_item(hub, session)["url"] == (
            "https://geoportal.statistics.gov.uk/api/download/v1/items/fs1/geoPackage?layers=0"
        )
        osi = hub.model_copy(update={"hub": "https://data-osi.opendata.arcgis.com/", "layer": 1})
        assert arcgis.resolve_item(osi, session)["url"] == (
            "https://data-osi.opendata.arcgis.com/api/download/v1/items/fs1/geoPackage?layers=1"
        )

        fs = _arcgis_access(query=None, pin="fs1", mode="featureserver")
        assert arcgis.resolve_item(fs, session)["url"] == (
            "https://services1.arcgis.com/x/FeatureServer/0"
        )

    def test_arcgis_error_with_http_200_raises(self, arcgis_server):
        arcgis_server.expect_request("/sharing/rest/search").respond_with_json({"results": []})
        arcgis_server.expect_request("/sharing/rest/content/items/pinned0").respond_with_json(
            {"error": {"code": 400, "message": "Item does not exist or is inaccessible."}}
        )
        with pytest.raises(RuntimeError, match="Item does not exist"):
            arcgis.resolve_item(_arcgis_access(), make_session())


def _feature_layer(httpserver, records: list[dict], page_size: int, short_every: int = 0):
    """Serve a fake feature layer: metadata, count, and offset-paged queries.

    ``short_every`` > 0 makes each response return at most that many records, like a
    server hitting its transfer limit.
    """
    httpserver.expect_request("/layer/0", query_string={"f": "json"}).respond_with_json(
        {"maxRecordCount": page_size, "objectIdField": "OID", "fields": []}
    )

    def query(request: Request) -> Response:
        args = request.args
        if args.get("returnCountOnly") == "true":
            return Response(json.dumps({"count": len(records)}), content_type="application/json")
        assert args["orderByFields"] == "OID"
        offset, size = int(args["resultOffset"]), int(args["resultRecordCount"])
        if short_every:
            size = min(size, short_every)
        page = records[offset : offset + size]
        if args["f"] == "geojson":
            body = {"type": "FeatureCollection", "features": page}
        else:
            body = {"features": page}
        return Response(json.dumps(body), content_type="application/json")

    httpserver.expect_request("/layer/0/query").respond_with_handler(query)
    return httpserver.url_for("/layer/0")


class TestFetchFeatureserver:
    def test_points_to_parquet_with_bng_xy(self, httpserver, tmp_path: Path):
        records = [
            {"attributes": {"LSOA21CD": f"E0100000{i}"}, "geometry": {"x": 1000.0 + i, "y": 2000.0}}
            for i in range(5)
        ]
        url = _feature_layer(httpserver, records, page_size=2)
        access = _arcgis_access(mode="featureserver", geometry="point", fields=["LSOA21CD"])
        out = tmp_path / "pts.parquet"

        n = arcgis.fetch_featureserver(url, access, out, make_session(), workers=2)

        df = pl.read_parquet(out)
        assert n == 5
        assert df.columns == ["LSOA21CD", "x", "y"]
        assert df["LSOA21CD"].to_list() == [f"E0100000{i}" for i in range(5)]
        assert df["x"].to_list() == [1000.0, 1001.0, 1002.0, 1003.0, 1004.0]

    def test_follows_short_pages(self, httpserver, tmp_path: Path):
        records = [{"attributes": {"OA21CD": f"E{i:08d}"}} for i in range(7)]
        url = _feature_layer(httpserver, records, page_size=4, short_every=3)
        out = tmp_path / "t.parquet"

        arcgis.fetch_featureserver(url, _arcgis_access(mode="featureserver"), out, make_session())

        assert pl.read_parquet(out)["OA21CD"].to_list() == [f"E{i:08d}" for i in range(7)]

    def test_count_mismatch_raises(self, httpserver, tmp_path: Path):
        httpserver.expect_request("/layer/0", query_string={"f": "json"}).respond_with_json(
            {"maxRecordCount": 10, "objectIdField": "OID", "fields": []}
        )

        def query(request: Request) -> Response:
            if request.args.get("returnCountOnly") == "true":
                return Response(json.dumps({"count": 3}), content_type="application/json")
            return Response(json.dumps({"features": [{"attributes": {"a": 1}}]}))

        httpserver.expect_request("/layer/0/query").respond_with_handler(query)
        with pytest.raises(RuntimeError, match="Expected 3 records"):
            arcgis.fetch_featureserver(
                httpserver.url_for("/layer/0"),
                _arcgis_access(mode="featureserver"),
                tmp_path / "x.parquet",
                make_session(),
            )
        assert not (tmp_path / "x.parquet").exists()

    def test_polygons_to_geopackage_in_bng(self, httpserver, tmp_path: Path):
        square = [[[-1.0, 52.0], [-0.99, 52.0], [-0.99, 52.01], [-1.0, 52.01], [-1.0, 52.0]]]
        records = [
            {"type": "Feature", "properties": {"MSOA21CD": "E02000001"},
             "geometry": {"type": "Polygon", "coordinates": square}},
        ]  # fmt: skip
        url = _feature_layer(httpserver, records, page_size=10)
        out = tmp_path / "m.gpkg"

        arcgis.fetch_featureserver(
            url, _arcgis_access(mode="featureserver", geometry="polygon"), out, make_session()
        )

        gdf = gpd.read_file(out)
        assert gdf.crs.to_epsg() == 27700
        assert gdf["MSOA21CD"].tolist() == ["E02000001"]
        # ~690m x ~1110m square around 1°W 52°N
        assert 0.6e6 < gdf.geometry.area.iloc[0] < 0.9e6


class TestResolveGovuk:
    def test_finds_attachment_by_regex(self, httpserver, monkeypatch):
        monkeypatch.setattr(govuk, "CONTENT_API", httpserver.url_for("/api/content"))
        page = {
            "public_updated_at": "2025-11-17T09:20:39+00:00",
            "details": {
                "attachments": [
                    {"title": "File 1", "url": "https://assets/media/1/File_1_IoD2025_IMD.xlsx"},
                    {"title": "File 7", "url": "https://assets/media/7/File_7_IoD2025_All.csv"},
                ]
            },
        }
        httpserver.expect_request("/api/content/government/statistics/iod").respond_with_json(page)
        access = GovukAttachmentAccess(
            type="govuk_attachment",
            path="government/statistics/iod",
            attachment_regex=r"File_7_IoD2025_.*\.csv$",
        )

        resolved = govuk.resolve_attachment(access, make_session())

        assert resolved["url"] == "https://assets/media/7/File_7_IoD2025_All.csv"
        assert resolved["title"] == "File 7"

    def test_ambiguous_match_raises(self, httpserver, monkeypatch):
        monkeypatch.setattr(govuk, "CONTENT_API", httpserver.url_for("/api/content"))
        page = {"details": {"attachments": [{"url": "a.csv"}, {"url": "b.csv"}]}}
        httpserver.expect_request("/api/content/p").respond_with_json(page)
        access = GovukAttachmentAccess(
            type="govuk_attachment", path="p", attachment_regex=r"\.csv$"
        )
        with pytest.raises(RuntimeError, match="found 2"):
            govuk.resolve_attachment(access, make_session())


def _spec(access: dict, fmt: str = "csv", **kw) -> DatasetSpec:
    return DatasetSpec(
        title="Test",
        theme=kw.pop("theme", "geography"),
        access=access,
        format=fmt,
        description="d",
        licence="OGL-3.0",
        attribution="a",
        cadence="static",
        **kw,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    """A throwaway repo root (for the lockfile) and data directory."""
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("LIX_ROOT", str(tmp_path))
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


class TestFetchOrchestrator:
    def test_resolves_downloads_and_locks(self, repo, httpserver):
        httpserver.expect_request("/f.csv", method="HEAD").respond_with_data(
            "", headers={"Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"}
        )
        httpserver.expect_request("/f.csv", method="GET").respond_with_data(b"a,b\n1,2\n")
        registry = {"f": _spec({"type": "http", "url": httpserver.url_for("/f.csv")})}

        fetched = fetch("f", registry=registry)

        lock = read_lock()["f"]
        assert lock["resolved"]["version"] == "Mon, 01 Jan 2024 00:00:00 GMT"
        assert lock["fetched"]["sha256"] == fetched["sha256"]
        assert (repo / "data" / "raw" / "f" / "f.csv").read_bytes() == b"a,b\n1,2\n"

    def test_reports_change_and_records_the_version(self, repo, httpserver):
        httpserver.expect_request("/f.csv", method="HEAD").respond_with_data(
            "", headers={"ETag": '"v1"'}
        )
        httpserver.expect_request("/f.csv", method="GET").respond_with_data(b"a,b\n")
        registry = {"f": _spec({"type": "http", "url": httpserver.url_for("/f.csv")})}

        first = fetch("f", registry=registry)
        assert first["changed"] is True
        assert read_lock()["f"]["fetched"]["version"] == '"v1"'
        assert "changed" not in read_lock()["f"]["fetched"]

        again = fetch("f", registry=registry)  # cached: nothing changed
        assert again["changed"] is False

    def test_strict_rejects_changed_checksum(self, repo, httpserver):
        httpserver.expect_request("/f.csv", method="HEAD").respond_with_data("")
        httpserver.expect_request("/f.csv", method="GET").respond_with_data(b"new,data\n")
        registry = {"f": _spec({"type": "http", "url": httpserver.url_for("/f.csv")})}
        (repo / "config" / "datasets.lock.json").write_text(
            json.dumps({"f": {"fetched": {"sha256": "0" * 64}}})
        )

        with pytest.raises(RuntimeError, match="Checksum differs"):
            fetch("f", registry=registry, strict=True)

    def test_manual_dataset_missing_then_present(self, repo):
        access = {"type": "manual", "instructions": "Log in to GeoDS", "expect": ["AHAH*.csv"]}
        registry = {"ahah": _spec(access, theme="environment")}

        with pytest.raises(ManualDownloadRequired, match="Log in to GeoDS"):
            fetch("ahah", registry=registry)

        folder = repo / "data" / "manual" / "ahah"
        folder.mkdir(parents=True)
        (folder / "AHAH_V5_1.csv").write_text("lsoa21cd,ahah\n")
        fetched = fetch("ahah", registry=registry)
        assert list(fetched["files"]) == ["AHAH_V5_1.csv"]
        assert read_lock()["ahah"]["fetched"]["files"] == fetched["files"]

    def test_unknown_slug(self, repo):
        with pytest.raises(ValueError, match="Unknown dataset slug"):
            fetch("nope", registry={})


class TestSelect:
    REGISTRY = {
        "a": _spec({"type": "http", "url": "u"}),
        "b": _spec({"type": "http", "url": "u"}, theme="safety"),
        "c": _spec({"type": "http", "url": "u"}, theme="safety", priority="P1"),
    }

    def test_filters(self):
        assert select(self.REGISTRY) == ["a", "b", "c"]
        assert select(self.REGISTRY, theme="safety") == ["b", "c"]
        assert select(self.REGISTRY, priority="P1") == ["c"]
        assert select(self.REGISTRY, slugs=["a"]) == ["a"]

    def test_unknown_slug(self):
        with pytest.raises(ValueError, match="Unknown dataset slug"):
            select(self.REGISTRY, slugs=["zzz"])


def test_fetch_module_exports():
    # The CLI relies on these names
    for name in ("fetch", "resolve_and_lock", "check_link", "select", "ManualDownloadRequired"):
        assert hasattr(fetch_mod, name)


class TestHtmlLink:
    def test_skips_releases_without_the_file(self, httpserver):
        from lix_core.config import HtmlLinkAccess
        from lix_pipeline.fetch.html import resolve_link

        base = "/pub/patients"
        httpserver.expect_request(base).respond_with_data(
            f'<a href="{base}/october-2026">Oct</a><a href="{base}/july-2026">Jul</a>'
            f'<a href="{base}/september-2026">Sep</a>',
            content_type="text/html",
        )
        # Newest announced release has no files yet; September has files but no LSOA one
        httpserver.expect_request(f"{base}/october-2026").respond_with_data("coming soon")
        httpserver.expect_request(f"{base}/september-2026").respond_with_data(
            '<a href="https://files.example/gp-reg-pat-prac-all.zip">all</a>'
        )
        httpserver.expect_request(f"{base}/july-2026").respond_with_data(
            '<a href="https://files.example/gp-reg-pat-prac-lsoa-July-26.zip">lsoa</a>'
        )
        access = HtmlLinkAccess(
            type="html_link",
            page=httpserver.url_for(base),
            steps=[
                {"pattern": r'href="(/pub/patients/[a-z]+-\d{4})"', "order": "date_desc"},
                {"pattern": r'href="(https://files\.example/[^"]*lsoa[^"]*\.zip)"'},
            ],
        )

        resolved = resolve_link(access, make_session())

        assert resolved == {
            "url": "https://files.example/gp-reg-pat-prac-lsoa-July-26.zip",
            "release": "2026-07-01",
        }

    def test_relative_links_and_desc_order(self, httpserver):
        from lix_core.config import HtmlLinkAccess
        from lix_pipeline.fetch.html import resolve_link

        httpserver.expect_request("/data/pcm-data").respond_with_data(
            '<a href="../datastore/pcm/mapno22023.csv">a</a>'
            '<a href="../datastore/pcm/mapno22024.csv">b</a>'
        )
        access = HtmlLinkAccess(
            type="html_link",
            page=httpserver.url_for("/data/pcm-data"),
            steps=[{"pattern": r'href="([^"]*/mapno2\d{4}\.csv)"', "order": "desc"}],
        )
        assert resolve_link(access, make_session())["url"] == httpserver.url_for(
            "/datastore/pcm/mapno22024.csv"
        )

    def test_no_match_raises(self, httpserver):
        from lix_core.config import HtmlLinkAccess
        from lix_pipeline.fetch.html import resolve_link

        httpserver.expect_request("/p").respond_with_data("nothing here")
        access = HtmlLinkAccess(
            type="html_link", page=httpserver.url_for("/p"), steps=[{"pattern": r'href="(x)"'}]
        )
        with pytest.raises(RuntimeError, match="No link matching"):
            resolve_link(access, make_session())


def test_govuk_pick_latest(httpserver, monkeypatch):
    monkeypatch.setattr(govuk, "CONTENT_API", httpserver.url_for("/api/content"))
    stem = "https://assets/media/{}/Management_information_-_state-funded_schools_-_latest_inspections_as_at_{}.csv"
    page = {"details": {"attachments": [
        {"url": stem.format(1, "30_June_2026")},
        {"url": stem.format(2, "31_August_2026")},
        {"url": stem.format(3, "31_July_2026")},
        {"url": "https://assets/media/4/all_inspections_year_to_date.csv"},
    ]}}  # fmt: skip
    httpserver.expect_request("/api/content/ofsted").respond_with_json(page)
    access = GovukAttachmentAccess(
        type="govuk_attachment",
        path="ofsted",
        attachment_regex=r"latest_inspections_as_at_.*\.csv$",
        pick="latest",
    )
    assert govuk.resolve_attachment(access, make_session())["url"] == stem.format(
        2, "31_August_2026"
    )


def test_dated_http_url_walks_back_to_latest_file(httpserver, monkeypatch):
    import datetime as dt

    from lix_core.config import HttpAccess
    from lix_pipeline.fetch import _resolve_http

    class FixedDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 7)

    monkeypatch.setattr(fetch_mod, "date", FixedDate)
    # Today's file isn't published yet (and HEAD errors, like GIAS's server); yesterday's is
    httpserver.expect_request("/edubasealldata20261007.csv").respond_with_data("", status=404)
    httpserver.expect_request("/edubasealldata20261006.csv", method="HEAD").respond_with_data(
        "", status=500
    )
    httpserver.expect_request("/edubasealldata20261006.csv", method="GET").respond_with_data("x")
    access = HttpAccess(
        type="http", url=httpserver.url_for("/edubasealldata{date}.csv"), date_format="%Y%m%d"
    )
    url, _ = _resolve_http(access, make_session())
    assert url.endswith("/edubasealldata20261006.csv")


class TestCkan:
    def test_takes_the_latest_matching_resource(self, httpserver):
        httpserver.expect_request(
            "/api/3/action/package_show", query_string={"id": "pharmacies"}
        ).respond_with_json(
            {
                "result": {
                    "resources": [
                        {"name": "CONSOL_PHARMACY_LIST_202526Q4", "url": "https://x/q4.csv"},
                        {"name": "CONSOL_PHARMACY_LIST_202606Q1", "url": "https://x/q1.csv"},
                        {"name": "Data dictionary", "url": "https://x/dict.xlsx"},
                    ]
                }
            }  # fmt: skip
        )
        access = CkanAccess(
            type="ckan",
            api=httpserver.url_for("/api/3/action"),
            package="pharmacies",
            resource_regex="^CONSOL_PHARMACY_LIST_",
        )
        resolved = ckan.resolve_resource(access, make_session())
        assert resolved["url"] == "https://x/q1.csv"

    def test_all_matches_returns_every_file_as_one_edition(self, httpserver):
        httpserver.expect_request("/api/3/action/package_show").respond_with_json(
            {
                "result": {
                    "resources": [
                        {
                            "name": "Belfast",
                            "url": "https://x/belfast_all-sales.csv",
                            "last_modified": "2025-08-22T00:00:00",
                        },
                        {
                            "name": "Derry",
                            "url": "https://x/derry_all-sales.csv",
                            "last_modified": "2025-08-21T00:00:00",
                        },
                        {"name": "Belfast detached", "url": "https://x/belfast_det.csv"},
                    ]
                }
            }  # fmt: skip
        )
        access = CkanAccess(
            type="ckan",
            api=httpserver.url_for("/api/3/action"),
            package="prices",
            resource_regex="_all-sales\\.csv$",
            all_matches=True,
        )
        resolved = ckan.resolve_resource(access, make_session())
        assert resolved["urls"] == {
            "belfast_all-sales": "https://x/belfast_all-sales.csv",
            "derry_all-sales": "https://x/derry_all-sales.csv",
        }
        assert resolved["modified"] == "2025-08-22T00:00:00"

    def test_no_match_is_an_error(self, httpserver):
        httpserver.expect_request("/api/3/action/package_show").respond_with_json(
            {"result": {"resources": [{"name": "other", "url": "u"}]}}
        )
        access = CkanAccess(
            type="ckan", api=httpserver.url_for("/api/3/action"), package="p", resource_regex="x"
        )
        with pytest.raises(RuntimeError, match="No resource"):
            ckan.resolve_resource(access, make_session())


class TestNomis:
    @pytest.fixture
    def nomis_server(self, httpserver, monkeypatch):
        monkeypatch.setattr(nomis, "API", httpserver.url_for("/api"))
        rows = [f'"August 2026","E0100{i:04d}",{i}' for i in range(7)]

        def handler(request: Request) -> Response:
            if request.args.get("select") == "date_name":
                return Response('"DATE_NAME"\n"August 2026"\n', content_type="text/csv")
            offset = int(request.args["recordoffset"])
            limit = int(request.args["recordlimit"])
            page = rows[offset : offset + limit]
            body = '"DATE_NAME","GEOGRAPHY_CODE","OBS_VALUE"\n' + "".join(r + "\n" for r in page)
            return Response(body, content_type="text/csv")

        httpserver.expect_request("/api/NM_1.data.csv").respond_with_handler(handler)
        return NomisAccess(
            type="nomis", dataset="NM_1", params={"geography": "TYPE151"}, page_size=3
        )

    def test_resolves_to_the_period_returned(self, nomis_server):
        resolved = nomis.resolve_query(nomis_server, make_session())
        assert resolved["version"] == "August 2026"
        assert "geography=TYPE151" in resolved["url"]

    def test_pages_into_one_csv(self, nomis_server, tmp_path):
        out = tmp_path / "claimants.csv"
        assert nomis.fetch_query(nomis_server, out, make_session()) == 7
        df = pl.read_csv(out)
        assert df.height == 7
        assert df["OBS_VALUE"].to_list() == list(range(7))

    def test_fetch_orchestrator_locks_the_period(self, repo, nomis_server):
        registry = {"cc": _spec(nomis_server.model_dump(), theme="community")}
        fetched = fetch("cc", registry=registry)
        assert fetched["rows"] == 7
        assert read_lock()["cc"]["resolved"]["version"] == "August 2026"
        # Same period again: nothing is re-downloaded
        assert fetch("cc", registry=registry)["rows"] == 7


class TestOverture:
    def _access(self, **kw):
        from lix_core.config import OvertureAccess

        return OvertureAccess(
            type="overture", theme="places", kind="place", bbox=(-6.5, 49.8, 1.9, 55.9),
            select=["id", "names.primary AS name"], where="basic_category = 'bar'", **kw,
        )  # fmt: skip

    def test_latest_release_sorts_by_date_then_number(self, httpserver, monkeypatch):
        from lix_pipeline.fetch import overture

        monkeypatch.setattr(overture, "BUCKET_HTTPS", httpserver.url_for("/bucket"))
        xml = "".join(
            f"<CommonPrefixes><Prefix>release/{r}/</Prefix></CommonPrefixes>"
            for r in ["2026-08-19.0", "2026-09-23.1", "2026-09-23.0"]
        )
        httpserver.expect_request("/bucket").respond_with_data(f"<x>{xml}</x>")
        resolved = overture.resolve_query(self._access(), make_session())
        # The version names the release and the query, so a widened bbox is a new edition
        assert resolved["version"].startswith("2026-09-23.1+")
        assert resolved["url"].endswith("/release/2026-09-23.1/theme=places/type=place/*")
        wider = self._access().model_copy(update={"bbox": (-8.7, 49.8, 1.9, 60.9)})
        assert overture.resolve_query(wider, make_session())["version"] != resolved["version"]

    def test_pinned_release_and_sql(self):
        from lix_pipeline.fetch import overture

        access = self._access(release="2026-08-19.0")
        resolved = overture.resolve_query(access, make_session())
        sql = overture.build_sql(access, resolved["url"])
        assert "release/2026-08-19.0/" in sql
        assert "bbox.xmin BETWEEN -6.5 AND 1.9" in sql and "(basic_category = 'bar')" in sql
        assert sql.startswith("SELECT id, names.primary AS name FROM read_parquet(")


class TestCheckLink:
    """The nightly link check: fail on a vanished file, warn on a firewall, read s3 globs."""

    def test_ok_and_new_version(self, httpserver):
        httpserver.expect_request("/f.csv").respond_with_data("x", headers={"ETag": '"v2"'})
        spec = _spec({"type": "http", "url": httpserver.url_for("/f.csv")})
        status, note = fetch_mod.check_link("zz_check_test", spec, make_session())
        assert status == "ok"
        assert note.startswith("ok (")

    def test_missing_file_fails(self, httpserver):
        httpserver.expect_request("/gone.csv").respond_with_data("", status=404)
        spec = _spec({"type": "http", "url": httpserver.url_for("/gone.csv")})
        status, note = fetch_mod.check_link("zz_check_test", spec, make_session())
        assert status == "fail"
        assert "404" in note

    def test_forbidden_is_a_warning_not_a_failure(self, httpserver):
        httpserver.expect_request("/blocked.csv").respond_with_data("", status=403)
        spec = _spec({"type": "http", "url": httpserver.url_for("/blocked.csv")})
        status, note = fetch_mod.check_link("zz_check_test", spec, make_session())
        assert status == "warn"
        assert note.startswith("not verified: 403")

    def test_overture_glob_is_probed_through_the_bucket_listing(self, httpserver, monkeypatch):
        from lix_pipeline.fetch import overture

        monkeypatch.setattr(overture, "S3_HTTPS", httpserver.url_for("/{bucket}"))
        httpserver.expect_request(
            "/b",
            query_string={
                "list-type": "2",
                "prefix": "release/2026-09-23.1/theme=places/type=place/",
                "max-keys": "1",
            },
        ).respond_with_data(
            "<ListBucketResult><Contents><Key>k</Key></Contents></ListBucketResult>"
        )
        httpserver.expect_request(
            "/b",
            query_string={"list-type": "2", "prefix": "release/2099-01-01.0/x/", "max-keys": "1"},
        ).respond_with_data("<ListBucketResult><KeyCount>0</KeyCount></ListBucketResult>")
        session = make_session()
        assert (
            overture.probe("s3://b/release/2026-09-23.1/theme=places/type=place/*", session) == 200
        )
        assert overture.probe("s3://b/release/2099-01-01.0/x/*", session) == 404
        assert fetch_mod._reachable(
            "s3://b/release/2026-09-23.1/theme=places/type=place/*", session
        ) == (200, None)

    def test_conditional_probe_tells_unchanged_from_changed(self, repo, httpserver):
        httpserver.expect_request("/f.csv", method="HEAD").respond_with_data(
            "", headers={"ETag": '"v1"'}
        )
        httpserver.expect_request(
            "/f.csv", method="GET", headers={"If-None-Match": '"v1"'}
        ).respond_with_data(b"", status=304)
        spec = _spec({"type": "http", "url": httpserver.url_for("/f.csv")})
        (repo / "config" / "datasets.lock.json").write_text(
            json.dumps({"f": {"resolved": {"version": '"v1"'}, "fetched": {"etag": '"v1"'}}})
        )

        result = fetch_mod.check_source("f", spec, make_session())
        assert (result["status"], result["changed"]) == ("ok", False)
        assert result["note"].startswith("unchanged")

        # The publisher replaced the file: a new validator, so no 304
        httpserver.clear()
        httpserver.expect_request("/f.csv", method="HEAD").respond_with_data(
            "", headers={"ETag": '"v2"'}
        )
        httpserver.expect_request("/f.csv", method="GET").respond_with_data(b"new")
        result = fetch_mod.check_source("f", spec, make_session())
        assert result["changed"] and result["note"].startswith("new upstream version")
        assert (result["version"], result["locked_version"]) == ('"v2"', '"v1"')

    def test_size_stands_in_for_a_missing_validator(self, repo, httpserver):
        httpserver.expect_request("/g.csv", method="HEAD").respond_with_data("")
        httpserver.expect_request("/g.csv", method="GET").respond_with_data(b"123456789")
        spec = _spec({"type": "http", "url": httpserver.url_for("/g.csv")})
        (repo / "config" / "datasets.lock.json").write_text(
            json.dumps({"g": {"resolved": {"version": None}, "fetched": {"bytes": 5}}})
        )

        result = fetch_mod.check_source("g", spec, make_session())
        assert result["changed"] and "size 5 → 9" in result["note"]

        (repo / "config" / "datasets.lock.json").write_text(
            json.dumps({"g": {"resolved": {"version": None}, "fetched": {"bytes": 9}}})
        )
        result = fetch_mod.check_source("g", spec, make_session())
        assert not result["changed"] and result["note"].startswith("ok, size unchanged")
