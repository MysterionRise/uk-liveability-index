"""Tests for the WCS tile fetcher and the multi-file download set, against a local server."""

import hashlib
from pathlib import Path

import pytest
from werkzeug import Request, Response

from lix_core.config import WcsAccess
from lix_pipeline.fetch import wcs
from lix_pipeline.fetch.http import _read_meta, download_set
from lix_pipeline.fetch.session import make_session

DESCRIBE = """<?xml version="1.0" encoding="UTF-8"?>
<wcs:CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:swe="http://www.opengis.net/swe/2.0">
  <wcs:CoverageDescription gml:id="x">
    <gml:boundedBy>
      <gml:Envelope srsName="http://www.opengis.net/def/crs/EPSG/0/27700" axisLabels="E N"
          uomLabels="m m" srsDimension="2">
        <gml:lowerCorner>82645.0 5335.0</gml:lowerCorner>
        <gml:upperCorner>655995.0 657605.0</gml:upperCorner>
      </gml:Envelope>
    </gml:boundedBy>
    <gml:domainSet><gml:RectifiedGrid dimension="2" gml:id="g">
      <gml:limits><gml:GridEnvelope>
        <gml:low>0 0</gml:low><gml:high>57334 65226</gml:high>
      </gml:GridEnvelope></gml:limits>
      <gml:axisLabels>i j</gml:axisLabels>
      <gml:offsetVector srsName="x">10.0 0.0</gml:offsetVector>
      <gml:offsetVector srsName="x">0.0 -10.0</gml:offsetVector>
    </gml:RectifiedGrid></gml:domainSet>
    <gml:rangeType><swe:DataRecord><swe:field name="GRAY_INDEX"><swe:Quantity>
      <swe:nilValues><swe:NilValues>
        <swe:nilValue reason="x">-96.0</swe:nilValue>
      </swe:NilValues></swe:nilValues>
    </swe:Quantity></swe:field></swe:DataRecord></gml:rangeType>
  </wcs:CoverageDescription>
</wcs:CoverageDescriptions>"""

TIFF = b"II*\x00" + b"\x00" * 60
BIGTIFF = b"II+\x00\x08\x00\x00\x00" + b"\x00" * 56  # cloud-optimised GeoTIFFs often are


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    return tmp_path


class TestTiles:
    def test_covers_the_envelope_with_partial_edge_tiles(self):
        out = wcs.tiles([0.0, 0.0, 25.0, 10.0], res=1.0, tile_px=10)
        assert out == [
            (0.0, 0.0, 10.0, 10.0),
            (10.0, 0.0, 20.0, 10.0),
            (20.0, 0.0, 25.0, 10.0),
        ]

    def test_bbox_clips(self):
        out = wcs.tiles(
            [0.0, 0.0, 100.0, 100.0], res=1.0, tile_px=10, bbox=[15.0, 15.0, 25.0, 25.0]
        )
        assert out == [(15.0, 15.0, 25.0, 25.0)]

    def test_england_at_8192(self):
        assert len(wcs.tiles([82645.0, 5335.0, 655995.0, 657605.0], 10.0, 8192)) == 56

    def test_urls_and_names(self):
        access = WcsAccess(type="wcs", url="https://x/wcs", coverage_id="a__b", tile_px=4)
        resolved = {"envelope": [0.0, 0.0, 8.0, 4.0], "res": 1.0}
        urls = wcs.tile_urls(access, resolved)
        assert list(urls) == ["E0_N0", "E4_N0"]
        assert urls["E4_N0"] == (
            "https://x/wcs?service=WCS&version=2.0.1&request=GetCoverage&coverageId=a__b"
            "&subset=E(4,8)&subset=N(0,4)&format=image/tiff&compression=Deflate"
        )


class TestDescribe:
    def test_parses_envelope_grid_and_nodata(self, httpserver):
        httpserver.expect_request("/wcs").respond_with_data(DESCRIBE, content_type="text/xml")
        access = WcsAccess(type="wcs", url=httpserver.url_for("/wcs"), coverage_id="a__b")
        resolved = wcs.resolve_coverage(access, make_session())
        assert resolved["envelope"] == [82645.0, 5335.0, 655995.0, 657605.0]
        assert resolved["res"] == 10.0
        assert resolved["grid"] == [57335, 65227]
        assert resolved["nodata"] == -96.0
        assert resolved["version"] == "a__b 57335x65227@10m"
        assert "DescribeCoverage" in resolved["url"]

    def test_exception_report_is_an_error(self, httpserver):
        httpserver.expect_request("/wcs").respond_with_data(
            '<?xml version="1.0"?><ows:ExceptionReport>too much</ows:ExceptionReport>'
        )
        access = WcsAccess(type="wcs", url=httpserver.url_for("/wcs"), coverage_id="a__b")
        with pytest.raises(RuntimeError, match="DescribeCoverage failed"):
            wcs.describe(access, make_session())


class TestDownloadSet:
    def test_downloads_every_file_and_skips_next_time(self, httpserver, project):
        httpserver.expect_request("/a.tif").respond_with_data(TIFF)
        httpserver.expect_request("/b.tif").respond_with_data(BIGTIFF + b"\x01")
        urls = {"a": httpserver.url_for("/a.tif"), "b": httpserver.url_for("/b.tif")}
        meta = download_set("zz", urls, "tif", version="v1", session=make_session())
        assert meta["completed"] and set(meta["files"]) == {"a", "b"}
        assert (project / "raw" / "zz" / "a.tif").read_bytes() == TIFF
        assert meta["files"]["a"] == hashlib.sha256(TIFF).hexdigest()
        assert meta["bytes"] == len(TIFF) + len(BIGTIFF) + 1
        httpserver.clear()  # a second call must not touch the server
        again = download_set("zz", urls, "tif", version="v1", session=make_session())
        assert again["sha256"] == meta["sha256"]

    def test_new_version_refetches(self, httpserver, project):
        httpserver.expect_request("/a.tif").respond_with_data(TIFF)
        urls = {"a": httpserver.url_for("/a.tif")}
        download_set("zz", urls, "tif", version="v1", session=make_session())
        httpserver.clear()
        httpserver.expect_request("/a.tif").respond_with_data(TIFF + b"\x02")
        meta = download_set("zz", urls, "tif", version="v2", session=make_session())
        assert meta["files"]["a"] == hashlib.sha256(TIFF + b"\x02").hexdigest()

    def test_retries_a_server_error_and_rejects_html(self, httpserver, project, monkeypatch):
        monkeypatch.setattr("lix_pipeline.fetch.http.time.sleep", lambda s: None)
        calls = {"n": 0}

        def flaky(request: Request) -> Response:
            calls["n"] += 1
            return Response("busy", status=504) if calls["n"] == 1 else Response(TIFF)

        httpserver.expect_request("/a.tif").respond_with_handler(flaky)
        httpserver.expect_request("/bad.tif").respond_with_data("<html>oops</html>")
        session = make_session()
        meta = download_set("zz", {"a": httpserver.url_for("/a.tif")}, "tif", "v", session=session)
        assert meta["completed"] and calls["n"] == 2
        with pytest.raises(ValueError, match="not a valid tif"):
            download_set(
                "zz2", {"bad": httpserver.url_for("/bad.tif")}, "tif", "v", session=session
            )
        assert not (project / "raw" / "zz2" / "bad.tif").exists()
        assert _read_meta(project / "raw" / "zz2").get("completed") is not True


class TestFetchTiles:
    def _access(self, httpserver):
        return WcsAccess(type="wcs", url=httpserver.url_for("/wcs"), coverage_id="a__b", tile_px=4)

    def test_a_failing_tile_is_fetched_as_quarters(self, httpserver, project, monkeypatch):
        monkeypatch.setattr("lix_pipeline.fetch.http.time.sleep", lambda s: None)
        resolved = {"url": "x", "version": "v1", "envelope": [0.0, 0.0, 8.0, 4.0], "res": 1.0}

        def serve(request: Request) -> Response:
            subset = request.args.getlist("subset")
            if subset == ["E(0,4)", "N(0,4)"]:
                return Response("gateway timeout", status=504)  # the dense tile
            return Response(TIFF + subset[0].encode())

        httpserver.expect_request("/wcs").respond_with_handler(serve)
        meta = wcs.fetch_tiles(
            "zz", self._access(httpserver), resolved, "tif", session=make_session()
        )
        assert meta["completed"]
        assert meta["files"]["E0_N0"] == ["E0_N0", "E2_N0", "E0_N2", "E2_N2"]
        assert isinstance(meta["files"]["E4_N0"], str)
        names = sorted(p.name for p in (project / "raw" / "zz").glob("*.tif"))
        assert names == ["E0_N0.tif", "E0_N2.tif", "E2_N0.tif", "E2_N2.tif", "E4_N0.tif"]
        httpserver.clear()  # the second run touches nothing
        again = wcs.fetch_tiles(
            "zz", self._access(httpserver), resolved, "tif", session=make_session()
        )
        assert again["sha256"] == meta["sha256"]

    def test_quarters_of_a_tile(self):
        assert wcs.quarters((0.0, 0.0, 8.0, 4.0)) == [
            (0.0, 0.0, 4.0, 2.0),
            (4.0, 0.0, 8.0, 2.0),
            (0.0, 2.0, 4.0, 4.0),
            (4.0, 2.0, 8.0, 4.0),
        ]
