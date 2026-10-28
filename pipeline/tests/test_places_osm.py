def test_northern_irish_places_take_the_nearest_postcodes_district(monkeypatch):
    """Every Northern Irish place had an empty postcode_district (#211)."""
    import polars as pl

    import lix_pipeline.geo.nspl as nspl_mod
    from lix_pipeline.stage.places_osm import nearest_district

    pcs = pl.DataFrame({
        "postcode": ["BT1 1AA", "BT47 2AB"],
        "east1m": [333_000.0, 243_000.0], "north1m": [374_000.0, 417_000.0],
    })  # fmt: skip
    monkeypatch.setattr(nspl_mod, "load_nspl", lambda **kw: pcs.lazy())
    places = pl.DataFrame(
        {"x": [333_200.0, 243_100.0, 300_000.0], "y": [374_100.0, 417_050.0, 300_000.0]}
    )
    assert nearest_district(places, "N").to_list() == ["BT1", "BT47", None]
    assert nearest_district(places, "I").to_list() == [None, None, None]
