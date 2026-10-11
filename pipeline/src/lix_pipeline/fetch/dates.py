"""Find the release date written into a file name or URL.

Publishers spell dates many ways: "as_at_31_August_2026", "31-august-2026",
"july-2026", "July-26", "072026", "2026-07-01". ``find_date`` returns the latest date
it can read, so files can be ordered newest first.
"""

import calendar
import re
from datetime import date
from urllib.parse import unquote

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
# Spaces, underscores, hyphens or dots between the parts
_SEP = r"[\s_\-.]*"

_PATTERNS = [
    # 31 August 2026 / 31-august-2026 / 31_Aug_2026
    (re.compile(rf"(?<!\d)(\d{{1,2}}){_SEP}({_MONTH}){_SEP}(\d{{4}})", re.I), ("d", "m", "y")),
    # August 2026 / july-2026 / July-26
    (re.compile(rf"(?<![a-z])({_MONTH}){_SEP}(\d{{4}}|\d{{2}})(?!\d)", re.I), ("m", "y")),
    # 2026-07-01 / 20261007 / 2026_03_31
    (
        re.compile(r"(?<!\d)(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)"),
        ("y", "mn", "d"),
    ),
    # 072026 (month then year, as in GPWPracticeCSV.072026.zip)
    (re.compile(r"(?<!\d)(0[1-9]|1[0-2])(20\d{2})(?!\d)"), ("mn", "y")),
    # 202601 (year then month, as in Ofcom's 202601_fixed_broadband_coverage.zip)
    (re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)"), ("y", "mn")),
]


def find_date(text: str) -> date | None:
    """The latest date found in ``text``, or None."""
    # "%20" in URLs would otherwise read as day 20
    text = unquote(text)
    found: list[date] = []
    for pattern, parts in _PATTERNS:
        for match in pattern.finditer(text):
            values = dict(zip(parts, match.groups()))
            try:
                year = int(values["y"])
                if year < 100:
                    year += 2000
                month = int(values["mn"]) if "mn" in values else _MONTHS[values["m"].lower()]
                day = int(values.get("d") or 1)
                found.append(date(year, month, day))
            except (ValueError, KeyError):
                continue
    return max(found) if found else None
