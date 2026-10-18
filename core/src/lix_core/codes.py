"""Geography codes: which nations are in scope and what their area codes look like.

ONS GSS codes start with the nation letter (E, W, S, N) and an entity type (01 = LSOA,
02 = MSOA, ...). Everything that filters by nation goes through ``in_scope()`` or
``area_code_regex()``, so a build for ``LIX_NATIONS=E,W`` and one for all four nations
differ only in config (``config/nations.yaml``).
"""

import os
from collections.abc import Iterable
from typing import Literal

import polars as pl

from lix_core.config import LevelName, load_nations

Nations = tuple[str, ...]


def active_nations() -> Nations:
    """Nation codes in scope: ``LIX_NATIONS`` (e.g. ``E,W``), else every configured nation.

    The result keeps the config's order, so it is stable whatever the variable says.
    """
    configured = load_nations().nations
    env = os.environ.get("LIX_NATIONS", "").strip()
    if not env:
        return tuple(configured)
    wanted = {c.strip().upper() for c in env.split(",") if c.strip()}
    unknown = sorted(wanted - set(configured))
    if unknown:
        raise ValueError(
            f"LIX_NATIONS names nations not in config/nations.yaml: {unknown} "
            f"(configured: {list(configured)})"
        )
    return tuple(c for c in configured if c in wanted)


def area_code_regex(level: LevelName = "low", nations: Nations | None = None) -> str:
    """Anchored regex matching the codes of ``level`` in the given (default: active) nations.

    ``^(?:E01\\d{6}|W01\\d{6})$`` for LSOAs under ``E,W``. Usable in Polars, DuckDB and
    pandas alike.
    """
    specs = load_nations().nations
    parts = [specs[n].levels[level].regex.removeprefix("^").removesuffix("$")
             for n in (nations or active_nations())]  # fmt: skip
    return "^(?:" + "|".join(parts) + ")$"


def in_scope(
    column: str | pl.Expr, level: LevelName = "low", nations: Nations | None = None
) -> pl.Expr:
    """Polars predicate: ``column`` holds a code of ``level`` in an active nation."""
    col = pl.col(column) if isinstance(column, str) else column
    return col.str.contains(area_code_regex(level, nations))


# The entity digits of each tier in a minted code, after ONS's convention (E01 = LSOA)
LEVEL_ENTITY = {"oa": "00", "low": "01", "mid": "02", "upper": "06"}


def minted_codes(nation: str, level: LevelName, source_codes: Iterable[str]) -> dict[str, str]:
    """Codes for a nation whose publisher's ids cannot be codes (Ireland's Small Area ids
    carry "/" for split and merged areas): ``<nation><entity><rank>``, the rank being the
    id's 1-based position in the sorted set of ids, zero-padded to six digits
    (``I01000001``). Deterministic for a given set of ids, so a rebuild from the same
    geography vintage mints the same codes; a new vintage is a new set of codes, as a new
    census geography is in the UK."""
    unique = sorted({str(c) for c in source_codes if c is not None})
    entity = LEVEL_ENTITY[level]
    return {code: f"{nation}{entity}{i + 1:06d}" for i, code in enumerate(unique)}


def nation_of(column: str | pl.Expr) -> pl.Expr:
    """The nation letter of a GSS code column (E, W, S, N)."""
    col = pl.col(column) if isinstance(column, str) else column
    return col.str.slice(0, 1)


Benchmark = Literal["uk", "nation"]

# Deprecated: England-only LSOA pattern from v0.1. Use in_scope()/area_code_regex().
ENGLAND_LSOA21 = r"^E01\d{6}$"
