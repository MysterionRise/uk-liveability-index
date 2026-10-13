"""Area and place names in the interface language.

The pack carries each name in English and, where a nation has one, in Welsh (``_cy``) and
Gaelic (``_gd``); a service asked for a locale returns the localised form as ``name`` and
the English beside it as ``name_en`` when they differ, so the page can show both.
"""

LOCALES = ("en", "cy", "gd", "ga")


def base_locale(locale: str | None) -> str:
    """``cy-GB`` → ``cy``; anything unknown reads as English."""
    code = (locale or "en").split("-")[0].lower()
    return code if code in LOCALES else "en"


def localised(row: dict, column: str, locale: str | None) -> str | None:
    """``row[column]`` in ``locale`` when the row has that form, else the English."""
    code = base_locale(locale)
    if code != "en":
        other = row.get(f"{column}_{code}")
        if other:
            return other
    return row.get(column)


def english_if_different(row: dict, column: str, locale: str | None) -> str | None:
    """The English name when the localised one differs from it, else None."""
    english = row.get(column)
    return english if localised(row, column, locale) != english else None
