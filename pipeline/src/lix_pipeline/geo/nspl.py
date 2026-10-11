"""NSPL (National Statistics Postcode Lookup): postcode → OA/LSOA/MSOA/LAD geocoding."""

import re
from pathlib import Path

import polars as pl

from lix_core.log import setup_logging
from lix_core.paths import data_dir

logger = setup_logging("geocode")

# NSPL columns we keep, by stem. Columns whose names carry a boundary vintage
# (lad26cd, rgn26cd, ...) are matched by stem so a new NSPL release with lad27cd
# still works; the output always uses the stem (lad_cd, rgn_cd, ...).
NSPL_FIXED_COLUMNS = [
    "pcds",
    "doterm",
    "usrtypind",  # 0 = small user (mostly homes), 1 = large user (businesses)
    "oa21cd",
    "lsoa21cd",
    "msoa21cd",
    "east1m",
    "north1m",
    "lat",
    "long",
]
NSPL_VINTAGED_STEMS = {
    "lad": "lad_cd",
    "rgn": "rgn_cd",
    "ctry": "ctry_cd",
    "pfa": "pfa_cd",
    "ruc": "ruc_ind",
}
# Country codes start with the nation letter: E92000001 England, W92000004 Wales, ...
ENGLAND = ("E",)


def _edition(name: str):
    """The date in a file name: NSPL_NOV_2026_UK.csv, or the Documents' "as at 06_26"."""
    from datetime import date

    from lix_pipeline.fetch.dates import find_date

    found = find_date(name)
    if found:
        return found
    m = re.search(r"(?<!\d)(0[1-9]|1[0-2])_(\d{2})(?!\d)", name)
    return date(2000 + int(m.group(2)), int(m.group(1)), 1) if m else date.min


def _newest(paths: list[Path]) -> Path:
    """The file of the newest edition: by the date in its name (NSPL_NOV_2026 beats
    NSPL_AUG_2026, which sorts first alphabetically), then by name."""
    return max(paths, key=lambda p: (_edition(p.name), p.name))


def nspl_document(pattern: str) -> Path:
    """One of the code→name lookups shipped in the NSPL zip's Documents/ folder."""
    matches = list((data_dir("raw") / "nspl" / "Documents").glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No NSPL document matching {pattern!r}")
    return _newest(matches)


def find_nspl_csv(nspl_dir: Path) -> Path:
    """Locate the NSPL CSV of the newest edition inside the extracted directory."""
    matches = list(nspl_dir.rglob("NSPL*_UK.csv"))
    if not matches:
        raise FileNotFoundError(f"No NSPL CSV found in {nspl_dir}")
    return _newest(matches)


def _resolve_vintaged(columns: list[str], stem: str) -> str | None:
    """Return the newest column matching ``{stem}NN(cd|ind)``, e.g. lad26cd for 'lad'."""
    pattern = re.compile(rf"^{stem}(\d{{2}})(cd|ind)$")
    matches = [(int(m.group(1)), c) for c in columns if (m := pattern.match(c))]
    return max(matches)[1] if matches else None


def default_nspl_path() -> Path:
    """Staged NSPL Parquet if it exists, else the raw CSV."""
    staged = data_dir("staged") / "nspl.parquet"
    if staged.exists():
        return staged
    return find_nspl_csv(data_dir("raw") / "nspl")


def read_nspl_raw(nspl_path: Path) -> pl.LazyFrame:
    """Scan the raw NSPL CSV and return the kept columns under stable names.

    All codes (and ``doterm``, a YYYYMM string) are read as strings so leading zeros
    and empty values survive.
    """
    # Header only; read_csv(n_rows=0) still type-infers rows and chokes on "UN1"
    header = pl.scan_csv(nspl_path, infer_schema=False).collect_schema().names()
    rename = {}
    for stem, out in NSPL_VINTAGED_STEMS.items():
        col = _resolve_vintaged(header, stem)
        if col is None:
            raise ValueError(f"NSPL has no {stem}NNcd/ind column; header: {header}")
        rename[col] = out
    missing = [c for c in NSPL_FIXED_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"NSPL is missing expected columns {missing}")

    keep = NSPL_FIXED_COLUMNS + list(rename)
    float_cols = {"lat", "long"}
    lf = pl.scan_csv(
        nspl_path,
        schema_overrides={c: pl.Float64 if c in float_cols else pl.Utf8 for c in keep},
        infer_schema=False,
    )
    lf = lf.select(keep).rename(rename)

    # NSPL quotes every field, so blanks arrive as "" rather than null
    string_cols = [c for c in lf.collect_schema().names() if c not in float_cols]
    lf = lf.with_columns(
        pl.when(pl.col(c).str.strip_chars() != "").then(pl.col(c)).alias(c) for c in string_cols
    )

    # Normalise postcode: strip all whitespace, uppercase
    return lf.with_columns(
        pl.col("pcds").alias("postcode"),
        pl.col("pcds").str.replace_all(r"\s", "").str.to_uppercase().alias("postcode_norm"),
        pl.col("doterm").is_null().alias("live"),
        pl.col("east1m").cast(pl.Int32, strict=False),
        pl.col("north1m").cast(pl.Int32, strict=False),
    ).drop("pcds")


def load_nspl(
    nspl_path: Path | None = None,
    live_only: bool = True,
    nations: tuple[str, ...] | None = ENGLAND,
) -> pl.LazyFrame:
    """Load NSPL (raw CSV or staged Parquet) as a LazyFrame keyed by ``postcode_norm``.

    Args:
        live_only: drop terminated postcodes. Set False to geocode historic records,
            e.g. house sales at postcodes that have since been retired.
        nations: keep postcodes whose country code starts with one of these letters
            ("E", "W", "S", "N", plus "L"/"M" for Channel Islands / Isle of Man).
            None keeps everything.
    """
    if nspl_path is None:
        nspl_path = default_nspl_path()

    logger.info(f"Loading NSPL from {nspl_path}")

    if nspl_path.suffix == ".parquet":
        lf = pl.scan_parquet(nspl_path)
    else:
        lf = read_nspl_raw(nspl_path)

    if live_only:
        lf = lf.filter(pl.col("live"))
    if nations is not None:
        lf = lf.filter(pl.col("ctry_cd").str.slice(0, 1).is_in(list(nations)))

    # No eager collect here — let the caller decide when to materialise
    return lf


def postcode_to_lsoa(
    df: pl.LazyFrame,
    postcode_col: str = "postcode",
    nspl: pl.LazyFrame | None = None,
) -> pl.LazyFrame:
    """Join a LazyFrame to NSPL to append lsoa21cd.

    The postcode column is normalised (strip whitespace, uppercase) before joining.
    """
    if nspl is None:
        nspl = load_nspl()

    # Normalise the input postcode column (strip all whitespace, uppercase)
    df = df.with_columns(
        pl.col(postcode_col)
        .str.strip_chars()
        .str.replace_all(r"\s", "")
        .str.to_uppercase()
        .alias("_pc_norm"),
    )

    # Left join on normalised postcode
    nspl_lookup = nspl.select(
        [
            pl.col("postcode_norm"),
            pl.col("lsoa21cd"),
        ]
    ).unique(subset=["postcode_norm"], keep="first")

    result = df.join(nspl_lookup, left_on="_pc_norm", right_on="postcode_norm", how="left")

    # Drop temporary column
    result = result.drop("_pc_norm")

    return result


def log_match_rate(df: pl.DataFrame, lsoa_col: str = "lsoa21cd") -> None:
    """Log postcode-to-LSOA match rate on an already-collected DataFrame."""
    total = len(df)
    if total == 0:
        return
    unmatched = df.filter(pl.col(lsoa_col).is_null()).height
    pct = unmatched / total * 100
    msg = f"Postcode matching: {total - unmatched:,}/{total:,} matched ({pct:.1f}% unmatched)"
    if pct > 5:
        logger.warning(msg)
    else:
        logger.info(msg)
