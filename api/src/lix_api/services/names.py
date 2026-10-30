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


def indicator_caveats(store, spec, locale: str | None) -> str | None:
    """An indicator's caveat in the interface language when its catalogue has one
    (config/i18n, carried by the manifest), else the English (#209)."""
    lang = base_locale(locale)
    if lang != "en" and spec.caveats:
        catalogue = ((store.manifest.get("i18n") or {}).get(lang) or {}).get("indicators") or {}
        text = (catalogue.get(spec.id) or {}).get("caveats")
        if text:
            return text
    return spec.caveats


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


def ruc_label(store, row: dict, locale: str | None) -> str | None:
    """The row's urban/rural class in ``locale``: the catalogue's label for the nation's
    code (config/i18n ``urban_rural``, carried by the manifest), else the English."""
    code = base_locale(locale)
    english = row.get("ruc21nm")
    if code == "en":
        return english
    classes = ((store.manifest.get("i18n") or {}).get(code) or {}).get("urban_rural") or {}
    label = (classes.get(row.get("nation")) or {}).get(str(row.get("ruc21cd")))
    return label or english


def council_name(store, name: str | None, locale: str | None) -> str | None:
    """A council's name in ``locale`` where the areas table has that form."""
    code = base_locale(locale)
    if not name or code == "en":
        return name
    return (store.council_names.get(name) or {}).get(code) or name
