"""Names as people read them: the stray characters a source leaves in, and their repair.

A publisher's export can carry control characters, zero-width marks, stray spaces, or a
UTF-8 string that was read as Windows-1252 and encoded again ("NestlÃ© UK" for "Nestlé
UK", "Â " for a no-break space, "Ã¢Â€Â™" for a curly apostrophe). The pipeline cleans
every name it serves with ``clean_name``; ``lix validate`` refuses a pack where any
remains (#212, #222).
"""

import re

import polars as pl

# Control characters (C0 and C1), the replacement character, zero-width and bidi marks,
# word joiners and byte-order marks
STRAY_CHARS = r"[\x00-\x1f\x7f-\x9f�​-‏ -‮⁠-⁤﻿]"
# A UTF-8 sequence read as Latin-1 shows a lead byte's character (Â–ô) followed by a
# continuation byte's (U+0080–U+00BF: a C1 control, a no-break space, ¢, ©, ® and the
# like): "Ã©" for é, "â€™" for a curly apostrophe. No real name in these islands has one
MOJIBAKE = r"[\u00c2-\u00f4][\u0080-\u00bf]"
# What the validator refuses in a name: stray characters, mojibake, a leading, trailing
# or doubled space
STRAY_NAME = rf"{STRAY_CHARS}|{MOJIBAKE}|^\s|\s$|\s\s"

_MOJIBAKE_RE = re.compile(MOJIBAKE)


def repair_mojibake(text: str | None) -> str | None:
    """Undo double encoding while the text still shows it and the bytes round-trip:
    "St LeonardÃ¢Â€Â™s" → "St Leonard’s" (twice-encoded text takes two rounds)."""
    if text is None:
        return None
    for _ in range(3):
        if not _MOJIBAKE_RE.search(text):
            break
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
    return text


def clean_name(expr: pl.Expr) -> pl.Expr:
    """A name column as people read it: mojibake repaired where it round-trips, control
    and zero-width characters dropped, whitespace collapsed and trimmed, empty → null."""
    repaired = (
        pl.when(expr.str.contains(MOJIBAKE))
        .then(expr.map_elements(repair_mojibake, return_dtype=pl.Utf8))
        .otherwise(expr)
    )
    cleaned = (
        repaired.str.replace_all(STRAY_CHARS, "").str.replace_all(r"\s+", " ").str.strip_chars()
    )
    return pl.when(cleaned == "").then(None).otherwise(cleaned).alias(expr.meta.output_name())


def stray_name_problems(
    df: pl.DataFrame, table: str, key: str, columns: list[str] | None = None
) -> list[str]:
    """One problem line per name column of ``df`` that carries a stray character, with
    three examples keyed by ``key``. Name columns are ``name``, ``name_*``, ``*nm`` and
    ``*_name`` unless ``columns`` says which."""
    problems = []
    names = columns or [
        c for c in df.columns if c == "name" or c.startswith("name_") or c.endswith(("nm", "_name"))
    ]
    for col in names:
        if col not in df.columns or df[col].dtype != pl.Utf8:
            continue
        bad = df.filter(pl.col(col).str.contains(STRAY_NAME))
        if bad.height:
            examples = bad.select(key, col).head(3).rows()
            problems.append(
                f"{table}.{col}: {bad.height:,} names with stray characters, e.g. {examples}"
            )
    return problems
