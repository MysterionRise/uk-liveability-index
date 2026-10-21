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
from scipy.spatial import cKDTree

from lix_core.config import SERVE_SCHEMA_VERSION, IndicatorSpec, Preset, ThemeSpec
from lix_core.geo import lonlat_to_working
from lix_core.paths import data_dir

# A name's English form and the other languages' forms, as the pack's columns call them
# The English name, its alternative ("Londonderry" for Derry) and the other languages
NAME_COLUMNS = ("name", "name_alt", "name_cy", "name_gd", "name_ga")


def to_bng(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 → the working CRS the pack's coordinates (``pwc_x``, POI x/y) are in."""
    x, y = lonlat_to_working([lon], [lat])
    return float(x[0]), float(y[0])


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
    def nation_aliases(self) -> dict[str, str]:
        """Each nation's folded name in every language the pack knows, and the other
        names people use for it ("Republic of Ireland", "ROI"), → its code ("alba" → S),
        for exact matching: a nation is never a fuzzy search result."""
        from lix_api.services.text import fold

        out: dict[str, str] = {}
        for code, nation in self.geography["nations"].items():
            names = (nation.get("names") or {}).values()
            for name in (nation["name"], *names, *nation.get("aliases", [])):
                out[fold(name)] = code
        return out

    @cached_property
    def scope_aliases(self) -> dict[str, tuple[str, ...]]:
        """Every folded name a scope can be given → the nations it means: a nation's
        names and aliases, and a country's ("UK", "United Kingdom" → England, Wales,
        Scotland, Northern Ireland)."""
        from lix_api.services.text import fold

        out: dict[str, tuple[str, ...]] = {a: (code,) for a, code in self.nation_aliases.items()}
        active = set(self.geography.get("active") or self.geography["nations"])
        for country in self.geography.get("countries", {}).values():
            nations = tuple(n for n in country.get("nations", []) if n in active)
            if not nations:
                continue
            names = (country.get("names") or {}).values()
            aliases = country.get("aliases", [])
            for name in (country.get("name"), country.get("short"), *names, *aliases):
                if name:
                    out.setdefault(fold(name), nations)
        return out

    @cached_property
    def headline_rules(self) -> dict[str, str]:
        """Nation → the base of its headline percentile ("all" or "nation"), from the
        manifest; "all" for a pack made before nations could opt in (#147)."""
        return {
            code: n.get("headline_within", "all") for code, n in self.geography["nations"].items()
        }

    @cached_property
    def region_councils(self) -> dict[str, set[str]]:
        """Folded region name → the council codes in it ("london" → the 33 boroughs), so a
        region works as a scope ("Clapham, London")."""
        from lix_api.services.text import fold

        out: dict[str, set[str]] = {}
        pairs = self.features.select("rgn_nm", "lad_cd").unique()
        for region, lad in pairs.iter_rows():
            if region:
                out.setdefault(fold(region), set()).add(lad)
        return out

    @cached_property
    def county_councils(self) -> dict[str, set[str]]:
        """Folded county name → the council codes that share it (Dublin's four), where
        the pack carries a county for councils."""
        from lix_api.services.text import fold

        if "county" not in self.areas.columns:
            return {}
        out: dict[str, set[str]] = {}
        rows = self.areas.filter((pl.col("level") == "lad") & pl.col("county").is_not_null())
        for code, county in rows.select("code", "county").iter_rows():
            out.setdefault(fold(county), set()).add(code)
        return out

    @cached_property
    def applicable(self) -> dict[str, list[str]]:
        """Scored indicator → the nations it is built for (the manifest's coverage)."""
        scored = set(self.manifest["scored_indicators"])
        return {
            i["id"]: list(i.get("coverage") or [])
            for i in self.manifest["indicators"]
            if i["id"] in scored
        }

    @cached_property
    def theme_gaps(self) -> dict[str, list[str]]:
        """Nation → themes it declares it cannot cover (shown as limited data)."""
        return {
            code: list(n.get("theme_gaps") or []) for code, n in self.geography["nations"].items()
        }

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
                       r"^theme_pct_ruc_class__.*$", r"^theme_pct_all__.*$",
                       r"^theme_coverage__.*$", "overall", "overall_pct",
                       "overall_pct_nation", "overall_pct_ruc_class", "overall_pct_all",
                       "coverage", "band", "headline_within")
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
    def place_name_columns(self) -> list[str]:
        """The places' name columns the search reads: English, then each language's form."""
        return [c for c in NAME_COLUMNS if c in self.places.columns]

    @cached_property
    def place_search_names(self) -> list[str]:
        """Every place name folded, column after column (``""`` where a place lacks a
        form), so index ``i`` names column ``i // places.height`` of row ``i % height``."""
        from lix_api.services.text import fold

        out: list[str] = []
        for column in self.place_name_columns:
            out += [fold(n) if n else "" for n in self.places[column].to_list()]
        return out

    @cached_property
    def area_name_columns(self) -> list[str]:
        return [c for c in NAME_COLUMNS if c in self.areas.columns]

    @cached_property
    def area_search_names(self) -> list[str]:
        """Every area name folded, column after column, like ``place_search_names``."""
        from lix_api.services.text import fold

        out: list[str] = []
        for column in self.area_name_columns:
            out += [fold(n) if n else "" for n in self.areas[column].to_list()]
        return out

    @cached_property
    def pois(self) -> pl.DataFrame:
        return pl.read_parquet(self.serve_dir / "pois.parquet")

    @cached_property
    def poi_trees(self) -> dict[str, tuple[cKDTree, pl.DataFrame]]:
        """A KD-tree per category, and one for the well-run pubs alone, so a nearest query
        over them finds every one within reach rather than a share of the nearest pubs."""
        trees = {}
        for (category,), df in self.pois.partition_by("category", as_dict=True).items():
            xy = np.column_stack([df["x"].to_numpy(), df["y"].to_numpy()])
            trees[category] = (cKDTree(xy), df)
            if category == "pub":
                well_run = df.filter(pl.col("detail").str.contains(r'"well_run": ?true'))
                if well_run.height:
                    xy = np.column_stack([well_run["x"].to_numpy(), well_run["y"].to_numpy()])
                    trees["well_run_pub"] = (cKDTree(xy), well_run)
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
