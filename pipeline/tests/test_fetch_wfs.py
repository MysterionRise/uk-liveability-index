"""Tests for the paged WFS fetcher, against a local server."""

from lix_core.config import WfsAccess
from lix_pipeline.fetch import wfs
from lix_pipeline.fetch.session import make_session

HITS = (
    '<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection '
    'xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="2805540" numberReturned="0" '
    'timeStamp="2026-10-10T19:00:00Z"/>'
)


def _access(url: str = "https://x/ows", **overrides) -> WfsAccess:
    base = dict(
        type="wfs",
        url=url,
        typename="ns:LAYER",
        sort_by="mm_id",
        cql_filter="risk IN ('High','Medium')",
        page_size=1_000_000,
    )
    return WfsAccess(**{**base, **overrides})


def test_pages_cover_the_count_in_order():
    urls = wfs.page_urls(_access(), "gpkg", 2_805_540)
    assert list(urls) == ["page_000", "page_001", "page_002"]
    assert "startIndex=2000000" in urls["page_002"] and "count=1000000" in urls["page_002"]
    assert "sortBy=mm_id" in urls["page_000"] and "outputFormat=gpkg" in urls["page_000"]
    assert "CQL_FILTER=risk+IN+%28%27High%27%2C%27Medium%27%29" in urls["page_000"]
    assert "srsName=EPSG%3A27700" in urls["page_000"]


def test_an_empty_layer_still_has_one_page():
    assert list(wfs.page_urls(_access(), "gpkg", 0)) == ["page_000"]


def test_resolve_counts_the_features(httpserver):
    httpserver.expect_request("/ows", query_string={"resultType": "hits"}).respond_with_data(
        HITS, content_type="text/xml"
    )
    # pytest-httpserver matches a subset of the query when given a dict? No: match loosely
    httpserver.expect_request("/ows").respond_with_data(HITS, content_type="text/xml")
    resolved = wfs.resolve_layer(_access(httpserver.url_for("/ows")), make_session())
    assert resolved["count"] == 2_805_540
    assert resolved["version"] == "ns:LAYER 2805540 features"
    assert "resultType=hits" in resolved["url"]
