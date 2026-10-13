"""config/nations.yaml and the nation-scope helpers in lix_core.codes."""

import polars as pl
import pytest

from lix_core import codes
from lix_core.config import NationsConfig, load_nations


def test_real_config_loads():
    cfg = load_nations()
    assert cfg.country.area_key == "lsoa21cd"
    assert list(cfg.nations) == ["E", "W", "S"]
    assert cfg.nations["E"].levels["low"].count == 33_755
    assert cfg.nations["W"].levels["low"].count == 1_917
    assert cfg.nations["S"].levels["low"].count == 7_392
    assert cfg.nations["W"].pseudo_region and cfg.nations["S"].pseudo_region
    assert cfg.nations["S"].geo.low_boundaries == "sg_dz22_boundaries"
    assert cfg.nations["S"].levels["mid"].labels == "intermediate zones"


def test_config_rejects_bad_regex_and_code():
    raw = load_nations().model_dump()
    raw["nations"]["W"]["levels"]["low"]["regex"] = r"^E01\d{6}$"
    cfg = NationsConfig.model_validate(raw)  # the model itself is fine
    assert cfg.nations["W"].levels["low"].regex.startswith("^E")
    # the loader's cross-checks catch it
    load_nations.cache_clear()
    import lix_core.config as config_module

    original = config_module.get_config
    config_module.get_config = lambda name: raw if name == "nations" else original(name)
    try:
        with pytest.raises(ValueError, match=r"W\.low"):
            load_nations()
    finally:
        config_module.get_config = original
        load_nations.cache_clear()


class TestActiveNations:
    def test_default_is_every_configured_nation(self, monkeypatch):
        monkeypatch.delenv("LIX_NATIONS", raising=False)
        assert codes.active_nations() == ("E", "W", "S")

    def test_env_narrows_and_keeps_config_order(self, monkeypatch):
        monkeypatch.setenv("LIX_NATIONS", "w, e")
        assert codes.active_nations() == ("E", "W")
        monkeypatch.setenv("LIX_NATIONS", "W")
        assert codes.active_nations() == ("W",)

    def test_unknown_nation_is_an_error(self, monkeypatch):
        monkeypatch.setenv("LIX_NATIONS", "E,N")
        with pytest.raises(ValueError, match=r"\['N'\]"):
            codes.active_nations()


class TestScope:
    def test_regex_combines_active_nations(self, monkeypatch):
        monkeypatch.setenv("LIX_NATIONS", "E,W")
        assert codes.area_code_regex() == r"^(?:E01\d{6}|W01\d{6})$"
        assert codes.area_code_regex("mid", nations=("W",)) == r"^(?:W02\d{6})$"

    def test_in_scope_filters_a_frame(self, monkeypatch):
        df = pl.DataFrame({"lsoa21cd": ["E01000001", "W01000001", "S01000001", "E02000001", None]})
        monkeypatch.setenv("LIX_NATIONS", "E,W")
        assert df.filter(codes.in_scope("lsoa21cd"))["lsoa21cd"].to_list() == [
            "E01000001",
            "W01000001",
        ]
        monkeypatch.setenv("LIX_NATIONS", "E")
        assert df.filter(codes.in_scope(pl.col("lsoa21cd")))["lsoa21cd"].to_list() == ["E01000001"]
        assert df.filter(codes.in_scope("lsoa21cd", "mid"))["lsoa21cd"].to_list() == ["E02000001"]

    def test_nation_of(self):
        df = pl.DataFrame({"c": ["E01000001", "W01000001", None]})
        assert df.select(codes.nation_of("c"))["c"].to_list() == ["E", "W", None]

    def test_deprecated_alias_still_england(self):
        assert codes.ENGLAND_LSOA21 == r"^E01\d{6}$"
