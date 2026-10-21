"""Tests for NSPL loading and postcode geocoding."""

import polars as pl
import pytest

from lix_pipeline.geo.nspl import (
    _resolve_vintaged,
    load_nspl,
    log_match_rate,
    postcode_to_lsoa,
    read_nspl_raw,
)


class TestReadNsplRaw:
    def test_stable_column_names(self, nspl_csv):
        df = read_nspl_raw(nspl_csv).collect()
        assert set(df.columns) == {
            "postcode",
            "postcode_norm",
            "doterm",
            "usrtypind",
            "live",
            "oa21cd",
            "lsoa21cd",
            "msoa21cd",
            "lad_cd",
            "rgn_cd",
            "ctry_cd",
            "pfa_cd",
            "ruc_ind",
            "east1m",
            "north1m",
            "lat",
            "long",
        }

    def test_blank_quoted_fields_become_null(self, nspl_csv):
        df = read_nspl_raw(nspl_csv).collect()
        live = df.filter(pl.col("postcode") == "SW1A 1AA").row(0, named=True)
        assert live["doterm"] is None
        assert live["live"] is True

    def test_codes_stay_strings(self, nspl_csv):
        df = read_nspl_raw(nspl_csv).collect()
        assert df.schema["doterm"] == pl.Utf8
        assert df.schema["lat"] == pl.Float64
        assert df.schema["east1m"] == pl.Int32

    def test_missing_column_raises(self, tmp_path):
        bad = tmp_path / "NSPL_X_UK.csv"
        bad.write_text("pcds,doterm,lad26cd,rgn26cd,ctry26cd,pfa23cd,ruc21ind\n")
        with pytest.raises(ValueError, match="missing expected columns"):
            read_nspl_raw(bad)


class TestResolveVintaged:
    def test_picks_newest_vintage(self):
        assert _resolve_vintaged(["lad24cd", "lad26cd", "lad25cd"], "lad") == "lad26cd"

    def test_ind_suffix(self):
        assert _resolve_vintaged(["ruc21ind"], "ruc") == "ruc21ind"

    def test_does_not_match_longer_stems(self):
        # "lep21cd1" must not count as a lep vintage column, nor "ladx26cd" as lad
        assert _resolve_vintaged(["lep21cd1", "ladx26cd"], "lad") is None


class TestLoadNspl:
    def test_loads_live_english_postcodes_by_default(self, nspl_csv):
        df = load_nspl(nspl_csv).collect()

        # E1 6AN is terminated, CF10 1AA is Welsh
        assert len(df) == 5
        assert "E16AN" not in df["postcode_norm"].to_list()
        assert "CF101AA" not in df["postcode_norm"].to_list()

    def test_live_only_false_keeps_terminated(self, nspl_csv):
        df = load_nspl(nspl_csv, live_only=False).collect()
        assert "E16AN" in df["postcode_norm"].to_list()

    def test_nations_none_keeps_all(self, nspl_csv):
        df = load_nspl(nspl_csv, live_only=False, nations=None).collect()
        assert len(df) == 7

    def test_reads_staged_parquet(self, nspl_csv, tmp_path):
        staged = tmp_path / "nspl.parquet"
        read_nspl_raw(nspl_csv).collect().write_parquet(staged)
        assert load_nspl(staged).collect().height == 5

    def test_normalises_postcodes(self, nspl_csv):
        norms = load_nspl(nspl_csv).collect()["postcode_norm"].to_list()
        assert "SW1A1AA" in norms
        assert "EC1A1BB" in norms

    def test_returns_lazyframe_without_collecting(self, nspl_csv):
        """load_nspl should return a LazyFrame, not eagerly collect."""
        assert isinstance(load_nspl(nspl_csv), pl.LazyFrame)


class TestPostcodeToLsoa:
    def test_joins_correctly(self, nspl_csv):
        nspl = load_nspl(nspl_csv)

        input_df = pl.DataFrame(
            {
                "postcode": ["SW1A 1AA", "EC1A 1BB"],
                "value": [100, 200],
            }
        ).lazy()

        result = postcode_to_lsoa(input_df, nspl=nspl).collect()
        assert "lsoa21cd" in result.columns
        assert result["lsoa21cd"][0] == "E01000001"
        assert result["lsoa21cd"][1] == "E01000002"

    def test_normalisation_variants(self, nspl_csv):
        """Various postcode formats should all match."""
        nspl = load_nspl(nspl_csv)

        variants = pl.DataFrame(
            {
                "postcode": ["SW1A 1AA", "sw1a1aa", "  SW1A  1AA ", "Sw1a 1Aa"],
            }
        ).lazy()

        result = postcode_to_lsoa(variants, nspl=nspl).collect()

        # All should match E01000001
        for lsoa in result["lsoa21cd"].to_list():
            assert lsoa == "E01000001"

    def test_unmatched_postcodes_dont_crash(self, nspl_csv):
        """Unmatched postcodes should return null, not error."""
        nspl = load_nspl(nspl_csv)

        input_df = pl.DataFrame(
            {
                "postcode": ["ZZ99 9ZZ", "SW1A 1AA"],
            }
        ).lazy()

        result = postcode_to_lsoa(input_df, nspl=nspl).collect()
        assert result["lsoa21cd"][0] is None
        assert result["lsoa21cd"][1] == "E01000001"

    def test_preserves_original_columns(self, nspl_csv):
        nspl = load_nspl(nspl_csv)

        input_df = pl.DataFrame(
            {
                "postcode": ["SW1A 1AA"],
                "price": [500000],
                "type": ["D"],
            }
        ).lazy()

        result = postcode_to_lsoa(input_df, nspl=nspl).collect()
        assert "price" in result.columns
        assert "type" in result.columns
        assert result["price"][0] == 500000

    def test_returns_lazyframe(self, nspl_csv):
        """postcode_to_lsoa should return a LazyFrame without eagerly collecting."""
        nspl = load_nspl(nspl_csv)
        input_df = pl.DataFrame({"postcode": ["SW1A 1AA"]}).lazy()
        assert isinstance(postcode_to_lsoa(input_df, nspl=nspl), pl.LazyFrame)


class TestLogMatchRate:
    def test_logs_match_rate(self, nspl_csv, caplog):
        """log_match_rate should log the correct match/unmatched stats."""
        import logging

        nspl = load_nspl(nspl_csv)
        input_df = pl.DataFrame(
            {
                "postcode": ["SW1A 1AA", "ZZ99 9ZZ"],
            }
        ).lazy()

        result = postcode_to_lsoa(input_df, nspl=nspl).collect()

        with caplog.at_level(logging.INFO, logger="lix.geocode"):
            log_match_rate(result)

        assert "1/2 matched" in caplog.text

    def test_handles_empty_dataframe(self):
        """log_match_rate should not crash on empty DataFrames."""
        df = pl.DataFrame({"lsoa21cd": pl.Series([], dtype=pl.Utf8)})
        log_match_rate(df)  # Should not raise


def test_codes_that_look_numeric_early_are_not_type_inferred(tmp_path, nspl_parts):
    """Real NSPL starts with Scottish rows whose ruc21ind is "1"; English "UN1" comes later.

    Any type inference over the first 100 rows reads the column as i64 and then fails.
    """
    NSPL_HEADER, NSPL_ROWS, _nspl_line = nspl_parts
    scottish_like = [_nspl_line(NSPL_ROWS[0]).replace('"UN1"', '"1"')] * 150
    path = tmp_path / "NSPL_AUG_2026_UK.csv"
    path.write_text("\n".join([NSPL_HEADER, *scottish_like, _nspl_line(NSPL_ROWS[1])]) + "\n")

    df = read_nspl_raw(path).collect()
    assert df["ruc_ind"].to_list()[-1] == "UN1"


def test_place_by_polygon_moves_only_postcodes_well_outside_their_listed_area(monkeypatch):
    """A point just over a generalised boundary keeps NSPL's area; one kilometres away
    takes the polygon that holds it (#153)."""
    import geopandas as gpd
    import polars as pl
    from shapely.geometry import box

    from lix_pipeline.stage import nspl as mod

    polygons = gpd.GeoDataFrame(
        {"lsoa21cd": ["E01000001", "E01000002"]},
        geometry=[box(0, 0, 1000, 1000), box(1000, 0, 2000, 1000)],
        crs="EPSG:27700",
    ).set_index("lsoa21cd")
    monkeypatch.setattr(mod, "tier_polygons", lambda level: polygons, raising=False)
    import lix_pipeline.geo.boundaries as boundaries

    monkeypatch.setattr(boundaries, "tier_polygons", lambda level, **kw: polygons)
    df = pl.DataFrame(
        {
            "postcode": ["AA1 1AA", "AA1 1AB", "AA1 1AC"],
            "lsoa21cd": ["E01000001", "E01000001", "E01000002"],
            "east1m": [500, 1050, 1500],  # inside; 50 m over the line; 500 m into the other
            "north1m": [500, 500, 500],
            "lat": [51.5, 51.5, 51.5],
            "live": [True, True, True],
        }
    )
    out = mod.place_by_polygon(df.lazy()).collect()
    assert out["lsoa21cd"].to_list() == ["E01000001", "E01000001", "E01000002"]
    far = df.with_columns(pl.Series("east1m", [500, 1050, 1500])).with_columns(
        pl.Series("lsoa21cd", ["E01000001", "E01000001", "E01000001"])
    )
    out = mod.place_by_polygon(far.lazy()).collect()
    assert out["lsoa21cd"].to_list() == ["E01000001", "E01000001", "E01000002"]
