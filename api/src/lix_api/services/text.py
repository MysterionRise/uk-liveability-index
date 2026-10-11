"""Text folding for search: neither case nor accents should matter."""

import unicodedata

from rapidfuzz.utils import default_process


def fold(text: str) -> str:
    """ "Ynys Môn" → "ynys mon": NFKD with the combining marks dropped, then rapidfuzz's
    default processing (lower case, non-alphanumerics to spaces, trimmed)."""
    base = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    return default_process(base)
