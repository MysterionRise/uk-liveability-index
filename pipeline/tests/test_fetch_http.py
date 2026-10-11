"""Tests for the HTTP download engine, against a local HTTP server."""

import hashlib
import io
import json
import socket
import zipfile
from pathlib import Path

import pytest
from pytest_socket import SocketConnectBlockedError
from werkzeug import Request, Response

from lix_pipeline.fetch.http import (
    _read_meta,
    _remove_extracted,
    _safe_extract,
    _write_meta,
    download,
    download_set,
)


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    """Point data at tmp_path."""
    monkeypatch.setenv("LIX_DATA_DIR", str(tmp_path))
    return tmp_path


class TestMetaRoundTrip:
    """Test .meta.json read/write cycle."""

    def test_write_and_read(self, tmp_path: Path):
        meta = {
            "slug": "test",
            "completed": True,
            "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT",
        }
        _write_meta(tmp_path, meta)

        loaded = _read_meta(tmp_path)
        assert loaded == meta

    def test_read_missing(self, tmp_path: Path):
        assert _read_meta(tmp_path) == {}

    def test_meta_file_location(self, tmp_path: Path):
        _write_meta(tmp_path, {"test": True})
        assert (tmp_path / ".meta.json").exists()


class TestSafeExtract:
    """Test ZIP extraction with path traversal protection."""

    def test_extracts_normal_zip(self, tmp_path: Path):
        data = _zip_bytes({"data.csv": "a,b\n1,2\n", "subdir/nested.txt": "hello"})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            _safe_extract(zf, tmp_path)

        assert (tmp_path / "data.csv").exists()
        assert (tmp_path / "subdir" / "nested.txt").exists()

    def test_blocks_path_traversal(self, tmp_path: Path):
        data = _zip_bytes({"../../../etc/evil.txt": "malicious"})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            with pytest.raises(ValueError, match="outside destination"):
                _safe_extract(zf, tmp_path)

    def test_blocks_sibling_directory_with_same_prefix(self, tmp_path: Path):
        """'nspl_evil/' starts with 'nspl' as a string but is outside 'nspl/'."""
        dest = tmp_path / "nspl"
        dest.mkdir()
        data = _zip_bytes({"../nspl_evil/x.txt": "malicious"})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            with pytest.raises(ValueError, match="outside destination"):
                _safe_extract(zf, dest)
        assert not (tmp_path / "nspl_evil").exists()

    def test_extracts_only_matching_patterns(self, tmp_path: Path):
        data = _zip_bytes(
            {
                "Data/NSPL_AUG_2026_UK.csv": "pcds\n",
                "Data/NSPL_AUG_2026_UK.txt": "big duplicate",
                "Data/multi_csv/NSPL_AUG_2026_UK_AB.csv": "pcds\n",
                "Documents/LAD names.csv": "code,name\n",
            }
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = _safe_extract(zf, tmp_path, ["Data/NSPL_*_UK.csv", "Documents/*.csv"])

        assert sorted(names) == ["Data/NSPL_AUG_2026_UK.csv", "Documents/LAD names.csv"]
        assert not (tmp_path / "Data" / "NSPL_AUG_2026_UK.txt").exists()
        assert not (tmp_path / "Data" / "multi_csv").exists()

    def test_no_matching_patterns_raises(self, tmp_path: Path):
        data = _zip_bytes({"other.csv": "x\n"})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            with pytest.raises(ValueError, match="No ZIP entries match"):
                _safe_extract(zf, tmp_path, ["Data/*.csv"])


class TestDownloadDataset:
    def test_skips_cached(self, project):
        root = project
        url, fmt = "http://127.0.0.1:1/never", "csv"
        dest = root / "raw" / "cached"
        _write_meta(dest, {"completed": True, "url": url})

        # Port 1 is closed: any request would fail, so success proves the cache was used
        assert download("cached", url, fmt)["completed"] is True

    def test_downloads_csv_and_records_checksum(self, project, httpserver):
        root = project
        body = b"col1,col2\nval1,val2\n"
        httpserver.expect_request("/test.csv").respond_with_data(
            body, headers={"Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT", "ETag": '"abc"'}
        )
        url, fmt = httpserver.url_for("/test.csv"), "csv"

        download("test_csv", url, fmt)
        dest = root / "raw" / "test_csv"

        assert (dest / "test_csv.csv").read_bytes() == body
        meta = _read_meta(dest)
        assert meta["completed"] is True
        assert meta["bytes"] == len(body)
        assert meta["sha256"] == hashlib.sha256(body).hexdigest()
        assert meta["last_modified"] == "Mon, 01 Jan 2024 00:00:00 GMT"
        assert meta["etag"] == '"abc"'

    def test_downloads_and_extracts_zip(self, project, httpserver):
        root = project
        httpserver.expect_request("/test.zip").respond_with_data(
            _zip_bytes({"data.csv": "a,b\n1,2\n"}), content_type="application/zip"
        )
        url, fmt = httpserver.url_for("/test.zip"), "zip"

        download("test_zip", url, fmt)
        dest = root / "raw" / "test_zip"

        # ZIP should be extracted and deleted
        assert not (dest / "test_zip.zip").exists()
        assert (dest / "data.csv").read_text() == "a,b\n1,2\n"
        assert _read_meta(dest)["extracted"] == ["data.csv"]

    def test_follows_redirects(self, project, httpserver):
        """ONS Geoportal downloads redirect twice to a signed blob URL."""
        root = project
        httpserver.expect_request("/start").respond_with_response(
            Response(status=302, headers={"Location": httpserver.url_for("/blob.gpkg")})
        )
        httpserver.expect_request("/blob.gpkg").respond_with_data(b"SQLite format 3\x00rest")
        url, fmt = httpserver.url_for("/start"), "gpkg"

        download("geo", url, fmt)
        dest = root / "raw" / "geo"
        assert (dest / "geo.gpkg").read_bytes().startswith(b"SQLite format 3")

    def test_rejects_error_body_served_as_zip(self, project, httpserver):
        """ArcGIS answers deleted items with HTTP 200 and a JSON error body."""
        root = project
        httpserver.expect_request("/gone").respond_with_json(
            {"error": {"code": 400, "message": "Item does not exist or is inaccessible."}}
        )
        url, fmt = httpserver.url_for("/gone"), "zip"

        with pytest.raises(ValueError, match="not a valid zip"):
            download("gone", url, fmt)

        dest = root / "raw" / "gone"
        # Neither the error body nor a .part file is kept; only the (incomplete) metadata
        assert [p.name for p in dest.iterdir()] == [".meta.json"]
        assert not _read_meta(dest).get("completed")

    def test_rejects_html_error_page_served_as_csv(self, project, httpserver):
        httpserver.expect_request("/page").respond_with_data(
            b"<!DOCTYPE html><html>Not found</html>", content_type="text/html"
        )
        url, fmt = httpserver.url_for("/page"), "csv"

        with pytest.raises(ValueError, match="error page"):
            download("page", url, fmt)

    def test_304_not_modified_updates_meta(self, project, httpserver):
        """HTTP 304 should skip download and mark as completed."""
        root = project
        last_modified = "Mon, 01 Jan 2024 00:00:00 GMT"
        httpserver.expect_request(
            "/test.csv", headers={"If-Modified-Since": last_modified}
        ).respond_with_data(b"", status=304)
        url, fmt = httpserver.url_for("/test.csv"), "csv"
        dest = root / "raw" / "test_csv"
        _write_meta(dest, {"last_modified": last_modified, "url": url})

        download("test_csv", url, fmt)
        assert _read_meta(dest)["completed"] is True

    @staticmethod
    def _versioned_handler(body: bytes, etag: str, seen: list):
        """Serve ``body`` with an ETag, honouring Range only when If-Range matches it."""

        def handler(request: Request) -> Response:
            rng, if_range = request.headers.get("Range"), request.headers.get("If-Range")
            seen.append((rng, if_range))
            if rng and if_range == etag:
                start = int(rng.removeprefix("bytes=").split("-")[0])
                return Response(
                    body[start:],
                    status=206,
                    headers={
                        "Content-Range": f"bytes {start}-{len(body) - 1}/{len(body)}",
                        "ETag": etag,
                    },
                )
            return Response(body, headers={"ETag": etag})

        return handler

    def _interrupted(self, root, slug: str, partial: bytes, meta: dict) -> Path:
        """Simulate an earlier attempt that left ``partial`` bytes behind."""
        dest = root / "raw" / slug
        dest.mkdir(parents=True)
        (dest / f"{slug}.csv.part").write_bytes(partial)
        _write_meta(dest, {"completed": False, **meta})
        return dest

    def test_resumes_interrupted_download_with_range(self, project, httpserver):
        root = project
        body = b"0123456789" * 1000
        seen = []
        handler = self._versioned_handler(body, '"v1"', seen)
        httpserver.expect_request("/big.csv").respond_with_handler(handler)
        url, fmt = httpserver.url_for("/big.csv"), "csv"
        dest = self._interrupted(root, "big", body[:4000], {"partial_etag": '"v1"'})

        download("big", url, fmt)

        assert seen == [("bytes=4000-", '"v1"')]
        assert (dest / "big.csv").read_bytes() == body
        assert not (dest / "big.csv.part").exists()
        assert _read_meta(dest)["sha256"] == hashlib.sha256(body).hexdigest()

    def test_restarts_when_remote_file_changed(self, project, httpserver):
        """Appending a new version's bytes to an old partial would corrupt the file."""
        root = project
        new_body = b"new,version\n" * 100
        seen = []
        handler = self._versioned_handler(new_body, '"v2"', seen)
        httpserver.expect_request("/f.csv").respond_with_handler(handler)
        url, fmt = httpserver.url_for("/f.csv"), "csv"
        dest = self._interrupted(root, "f", b"old,version\n" * 50, {"partial_etag": '"v1"'})

        download("f", url, fmt)

        assert seen == [("bytes=600-", '"v1"')]
        assert (dest / "f.csv").read_bytes() == new_body

    def test_does_not_resume_without_validator(self, project, httpserver):
        root = project
        body = b"a,b\n1,2\n"
        seen = []
        handler = self._versioned_handler(body, '"v1"', seen)
        httpserver.expect_request("/n.csv").respond_with_handler(handler)
        url, fmt = httpserver.url_for("/n.csv"), "csv"
        dest = self._interrupted(root, "n", b"a,b\n", {})

        download("n", url, fmt)

        assert seen == [(None, None)]
        assert (dest / "n.csv").read_bytes() == body

    def test_interrupted_download_keeps_part_file_only(self, project, httpserver):
        """A failed transfer leaves a resumable .part file but nothing at the final path."""
        root = project

        def handler(request: Request) -> Response:
            def stream():
                yield b"x" * 1024
                raise ConnectionError("connection lost")

            return Response(stream(), headers={"Content-Length": "1000000", "ETag": '"v1"'})

        httpserver.expect_request("/broken.csv").respond_with_handler(handler)
        url, fmt = httpserver.url_for("/broken.csv"), "csv"

        with pytest.raises(Exception):
            download("broken", url, fmt)

        dest = root / "raw" / "broken"
        assert not (dest / "broken.csv").exists()
        meta = _read_meta(dest)
        assert not meta.get("completed")
        # The validator is recorded so the next run can resume safely
        assert meta["partial_etag"] == '"v1"'


class TestRevalidate:
    """``make refresh`` asks whether a file already on disk is still current."""

    def test_304_keeps_the_file_and_its_date(self, project, httpserver):
        body = b"a,b\n1,2\n"
        httpserver.expect_request("/f.csv", headers={"If-None-Match": '"v1"'}).respond_with_data(
            b"", status=304
        )
        url = httpserver.url_for("/f.csv")
        dest = project / "raw" / "f"
        dest.mkdir(parents=True)
        (dest / "f.csv").write_bytes(body)
        old = {
            "completed": True, "url": url, "etag": '"v1"', "downloaded_at": "2026-01-01T00:00:00",
            "sha256": hashlib.sha256(body).hexdigest(),
        }  # fmt: skip
        _write_meta(dest, old)

        meta = download("f", url, "csv", revalidate=True)

        assert (dest / "f.csv").read_bytes() == body
        assert meta["completed"] and meta["downloaded_at"] == "2026-01-01T00:00:00"
        assert meta["revalidated_at"]

    def test_changed_archive_replaces_the_old_edition(self, project, httpserver):
        """A new NSPL must not leave August's CSV beside November's."""
        httpserver.expect_request("/n.zip").respond_with_data(
            _zip_bytes({"Data/NSPL_NOV.csv": "new\n"}), headers={"ETag": '"v2"'}
        )
        url = httpserver.url_for("/n.zip")
        dest = project / "raw" / "n"
        (dest / "Data").mkdir(parents=True)
        (dest / "Data" / "NSPL_AUG.csv").write_text("old\n")
        _write_meta(
            dest,
            {"completed": True, "url": url, "etag": '"v1"', "sha256": "0" * 64,
             "extracted": ["Data/NSPL_AUG.csv"]},
        )  # fmt: skip

        meta = download("n", url, "zip", revalidate=True)

        assert not (dest / "Data" / "NSPL_AUG.csv").exists()
        assert (dest / "Data" / "NSPL_NOV.csv").read_text() == "new\n"
        assert meta["extracted"] == ["Data/NSPL_NOV.csv"] and meta["etag"] == '"v2"'

    def test_server_without_validators_is_compared_by_checksum(self, project, httpserver):
        body = b"same,data\n"
        httpserver.expect_request("/p.csv").respond_with_data(body)
        url = httpserver.url_for("/p.csv")
        dest = project / "raw" / "p"
        dest.mkdir(parents=True)
        (dest / "p.csv").write_bytes(body)
        _write_meta(
            dest,
            {"completed": True, "url": url, "downloaded_at": "2026-01-01T00:00:00",
             "sha256": hashlib.sha256(body).hexdigest()},
        )  # fmt: skip

        meta = download("p", url, "csv", revalidate=True)
        assert meta["downloaded_at"] == "2026-01-01T00:00:00"  # identical: as old as it was

        httpserver.clear()
        httpserver.expect_request("/p.csv").respond_with_data(b"new,data\n")
        meta = download("p", url, "csv", revalidate=True)
        assert meta["downloaded_at"] != "2026-01-01T00:00:00"
        assert (dest / "p.csv").read_bytes() == b"new,data\n"

    def test_cached_download_is_not_revalidated_by_default(self, project):
        url = "http://127.0.0.1:1/never"
        _write_meta(project / "raw" / "c", {"completed": True, "url": url, "etag": '"v1"'})
        assert download("c", url, "csv")["completed"] is True


class TestSets:
    def test_download_set_prunes_files_no_longer_in_the_set(self, project, httpserver):
        httpserver.expect_request("/a.csv").respond_with_data(b"a\n")
        httpserver.expect_request("/b.csv").respond_with_data(b"b\n")
        dest = project / "raw" / "s"
        dest.mkdir(parents=True)
        (dest / "stale.csv").write_text("gone\n")
        urls = {"a": httpserver.url_for("/a.csv"), "b": httpserver.url_for("/b.csv")}

        meta = download_set("s", urls, "csv", version="v1")

        assert sorted(p.name for p in dest.glob("*.csv")) == ["a.csv", "b.csv"]
        assert set(meta["files"]) == {"a", "b"} and meta["completed"]

    def test_remove_extracted_stays_inside_the_dataset_directory(self, tmp_path: Path):
        dest = tmp_path / "raw" / "x"
        (dest / "sub").mkdir(parents=True)
        (dest / "sub" / "a.txt").write_text("a")
        (tmp_path / "raw" / "evil.txt").write_text("keep")

        _remove_extracted(dest, {"extracted": ["sub/a.txt", "../evil.txt"]})

        assert not (dest / "sub").exists()
        assert (tmp_path / "raw" / "evil.txt").exists()


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_real_network_is_blocked():
    """Guard: pytest-socket must stop tests reaching the internet."""
    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(("1.1.1.1", 443), timeout=2)


class TestVersionsAndExports:
    def test_new_version_triggers_download(self, project, httpserver):
        root = project
        httpserver.expect_request("/v.csv").respond_with_data(b"v2\n")
        url = httpserver.url_for("/v.csv")
        dest = root / "raw" / "v"
        _write_meta(dest, {"completed": True, "url": url, "version": "2026-01"})

        download("v", url, "csv", version="2026-01")  # same version: cached
        assert not (dest / "v.csv").exists()

        meta = download("v", url, "csv", version="2026-02")
        assert (dest / "v.csv").read_bytes() == b"v2\n"
        assert meta["version"] == "2026-02"

    def test_new_url_triggers_download(self, project, httpserver):
        root = project
        httpserver.expect_request("/new.csv").respond_with_data(b"new\n")
        dest = root / "raw" / "u"
        _write_meta(dest, {"completed": True, "url": "https://old.example/file.csv"})

        download("u", httpserver.url_for("/new.csv"), "csv")
        assert (dest / "u.csv").read_bytes() == b"new\n"

    def test_waits_for_hub_export_to_be_generated(self, project, httpserver):
        """ArcGIS Hub answers 202 while it builds a download, then serves the file."""
        root = project
        calls = []

        def handler(request: Request) -> Response:
            calls.append(1)
            if len(calls) < 3:
                body = {"status": "InProgress", "message": "Download file is being generated."}
                return Response(json.dumps(body), status=202, content_type="application/json")
            return Response(b"SQLite format 3\x00data")

        httpserver.expect_request("/export").respond_with_handler(handler)
        download("g", httpserver.url_for("/export"), "gpkg", poll_s=0)

        assert len(calls) == 3
        assert (root / "raw" / "g" / "g.gpkg").read_bytes().startswith(b"SQLite format 3")

    def test_gives_up_on_export_that_never_finishes(self, project, httpserver):
        httpserver.expect_request("/export").respond_with_data("{}", status=202)
        with pytest.raises(TimeoutError, match="still being generated"):
            download("g", httpserver.url_for("/export"), "gpkg", export_wait_s=0, poll_s=0)
