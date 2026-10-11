"""In-memory data store the API answers from, loaded once from data/serve/.

Everything is small enough to hold in memory (about 100 MB): per-LSOA features,
postcodes, places, named areas and points of interest, plus a KD-tree per POI
category for nearest-place queries.
"""

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np
import polars as pl
from pyproj import Transformer
from scipy.spatial import cKDTree

from lix_core.config import SERVE_SCHEMA_VERSION, IndicatorSpec, Preset, ThemeSpec
from lix_core.paths import data_dir

_TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)


def to_bng(lon: float, lat: float) -> tuple[float, float]:
    return _TO_BNG.transform(lon, lat)


@dataclass
class Store:
    serve_dir: Path = field(default_factory=lambda: data_dir("serve"))
    score_cache: dict = field(default_factory=dict, repr=False)

    @cached_property
    def manifest(self) -> dict:
        manifest = json.loads((self.serve_dir / "manifest.json").read_text())
        version = manifest.get("schema_version", 1)
        if version != SERVE_SCHEMA_VERSION:
            raise RuntimeError(
                f"Data pack schema v{version} but this API needs v{SERVE_SCHEMA_VERSION}: "
                "run make data-download"
            )
        return manifest

    @property
    def geography(self) -> dict:
        return self.manifest["geography"]

    @cached_property
    def nation_names(self) -> dict[str, str]:
        return {code: n["name"] for code, n in self.geography["nations"].items()}

    @cached_property
    def indicators(self) -> dict[str, IndicatorSpec]:
        # The manifest dumps the computed unit_code beside the fields; the strict model
        # recomputes it
        return {
            i["id"]: IndicatorSpec.model_validate({k: v for k, v in i.items() if k != "unit_code"})
            for i in self.manifest["indicators"]
        }

    @cached_property
    def themes(self) -> dict[str, ThemeSpec]:
        return {k: ThemeSpec.model_validate(v) for k, v in self.manifest["themes"].items()}

    @cached_property
    def presets(self) -> dict[str, Preset]:
        return {k: Preset.model_validate(v) for k, v in self.manifest["presets"].items()}

    @property
    def default_preset(self) -> str:
        return self.manifest["default_preset"]

    @cached_property
    def scored(self) -> list[IndicatorSpec]:
        return [self.indicators[i] for i in self.manifest["scored_indicators"]]

    @cached_property
    def features(self) -> pl.DataFrame:
        return pl.read_parquet(self.serve_dir / "lsoa_features.parquet")

    @cached_property
    def base_features(self) -> pl.DataFrame:
        """Features without the default preset's scores, for re-scoring with any weights."""
        return self.features.select(
            pl.exclude(r"^theme__.*$", r"^theme_pct__.*$", r"^theme_pct_nation__.*$",
                       r"^theme_coverage__.*$", "overall", "overall_pct", "overall_pct_nation",
                       "coverage", "band")
        )  # fmt: skip

    @cached_property
    def lsoa_index(self) -> dict[str, int]:
        return {code: i for i, code in enumerate(self.features["lsoa21cd"].to_list())}

    @cached_property
    def lsoa_tree(self) -> cKDTree:
        """Population-weighted centroids on the working grid, for nearest-area lookups."""
        f = self.features
        return cKDTree(np.column_stack([f["pwc_x"].to_numpy(), f["pwc_y"].to_numpy()]))

    @cached_property
    def postcodes(self) -> pl.DataFrame:
        return pl.read_parquet(self.serve_dir / "postcodes.parquet")

    @cached_property
    def places(self) -> pl.DataFrame:
        return pl.read_parquet(self.serve_dir / "places.parquet")

    @cached_property
    def areas(self) -> pl.DataFrame:
        return pl.read_parquet(self.serve_dir / "areas.parquet")

    @cached_property
    def place_search_names(self) -> list[str]:
        """Folded place names followed by their folded alternative names ("" where there
        is none), so an index below ``places.height`` is a name and one above it the
        alternative of the same row."""
        from lix_api.services.text import fold

        names = [fold(n) for n in self.places["name"].to_list()]
        if "name_alt" in self.places.columns:
            alts = [fold(a) if a else "" for a in self.places["name_alt"].to_list()]
        else:
            alts = [""] * len(names)
        return names + alts

    @cached_property
    def area_search_names(self) -> list[str]:
        from lix_api.services.text import fold

        return [fold(n) for n in self.areas["name"].to_list()]

    @cached_property
    def pois(self) -> pl.DataFrame:
        return pl.read_parquet(self.serve_dir / "pois.parquet")

    @cached_property
    def poi_trees(self) -> dict[str, tuple[cKDTree, pl.DataFrame]]:
        trees = {}
        for (category,), df in self.pois.partition_by("category", as_dict=True).items():
            xy = np.column_stack([df["x"].to_numpy(), df["y"].to_numpy()])
            trees[category] = (cKDTree(xy), df)
        return trees

    def warm(self) -> "Store":
        """Load everything up front so the first request isn't slow."""
        for attr in (
            "manifest", "features", "lsoa_index", "postcodes", "places", "areas",
            "place_search_names", "area_search_names",
        ):  # fmt: skip
            getattr(self, attr)
        self.poi_trees  # noqa: B018
        return self


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def set_store(store: Store) -> None:
    """Swap the store (tests point it at fixture data)."""
    global _store
    _store = store
