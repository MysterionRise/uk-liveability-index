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


LATEST = (
    '{"type":"FeatureCollection","features":[{"type":"Feature","id":"LAYER.1","geometry":null,'
    '"properties":{"pub_date":"%s"}}],"numberMatched":2805540,"numberReturned":1}'
)


def _serve(httpserver, latest: str = "2026-05-21", hits: str = HITS) -> list[dict]:
    """A layer that answers the hits request and the newest-feature request; returns the
    queries it was asked, in order."""
    from werkzeug import Request, Response

    asked: list[dict] = []

    def answer(request: Request) -> Response:
        asked.append(dict(request.args))
        if request.args.get("resultType") == "hits":
            return Response(hits, content_type="text/xml")
        return Response(LATEST % latest, content_type="application/json")

    httpserver.expect_request("/ows").respond_with_handler(answer)
    return asked


def test_the_version_carries_the_layers_newest_date(httpserver):
    """A count alone misses an edit that keeps the count (#232): where the layer dates its
    features, the newest date is part of the version. One feature is asked for, newest
    first, that field only."""
    asked = _serve(httpserver)
    access = _access(httpserver.url_for("/ows"), version_field="pub_date")
    resolved = wfs.resolve_layer(access, make_session())
    assert resolved["version"] == "ns:LAYER 2805540 features, pub_date 2026-05-21"
    assert resolved["count"] == 2_805_540
    newest = asked[1]
    assert newest["count"] == "1" and newest["sortBy"] == "pub_date DESC"
    assert newest["propertyName"] == "pub_date" and newest["outputFormat"] == "application/json"
    assert newest["CQL_FILTER"] == "risk IN ('High','Medium')"  # of the features kept


def test_a_layer_that_did_not_move_keeps_its_version_and_an_edit_changes_it(httpserver):
    _serve(httpserver)
    access = _access(httpserver.url_for("/ows"), version_field="pub_date")
    first = wfs.resolve_layer(access, make_session())
    assert wfs.resolve_layer(access, make_session())["version"] == first["version"]
    httpserver.clear()
    _serve(httpserver, latest="2026-11-03")  # the same count, republished
    assert wfs.resolve_layer(access, make_session())["version"] != first["version"]


def test_a_layer_without_a_dated_field_is_versioned_by_its_count(httpserver):
    asked = _serve(httpserver)
    resolved = wfs.resolve_layer(_access(httpserver.url_for("/ows")), make_session())
    assert resolved["version"] == "ns:LAYER 2805540 features" and len(asked) == 1


def test_an_empty_layer_has_no_newest_date(httpserver):
    empty = HITS.replace('numberMatched="2805540"', 'numberMatched="0"')
    httpserver.expect_request("/ows").respond_with_data(empty, content_type="text/xml")
    access = _access(httpserver.url_for("/ows"), version_field="pub_date")
    assert wfs.resolve_layer(access, make_session())["version"] == "ns:LAYER 0 features"


def test_the_welsh_flood_layers_are_versioned_by_count_and_date():
    """Fetched as one GetFeature file, the two layers came back as a new edition on every
    refresh: GeoServer writes the GeoPackage afresh, so its checksum never repeats (#232)."""
    from lix_core.config import load_registry

    datasets = load_registry()
    for slug in ("nrw_fraw_rivers", "nrw_fraw_sea"):
        access = datasets[slug].access
        assert access.type == "wfs", slug
        assert access.sort_by == "mm_id" and access.version_field == "pub_date", slug
    # No other plain download is a WFS GetFeature for a GeoPackage
    rewritten = [
        slug
        for slug, d in datasets.items()
        if d.access.type == "http" and "request=GetFeature" in d.access.url and d.format == "gpkg"
    ]
    assert rewritten == []
