"""The forms a word takes at the start of a phrase in Welsh, Irish and Scottish Gaelic.

Initial consonants mutate after many words ("tren" → "drên", "thren", "nhren"; "càr" →
"chàr"; "páistí" → "pháistí", "bpáistí"), so a word list in its dictionary form misses
most of the ways people write it. ``variants`` gives the dictionary form and every
mutated form of a phrase's first word, on text already folded (accents and apostrophes
dropped, as the rules engine folds it), so the phrase table can hold them all (#161).
English gets its plural.
"""

# Welsh: soft, nasal and aspirate mutations of an initial consonant (folded spelling)
WELSH = {
    "c": ("g", "ngh", "ch"),
    "p": ("b", "mh", "ph"),
    "t": ("d", "nh", "th"),
    "g": ("", "ng"),
    "b": ("f", "m"),
    "d": ("dd", "n"),
    "ll": ("l",),
    "m": ("f",),
    "rh": ("r",),
}
# Irish: lenition (an h after the consonant) and eclipsis (a consonant before it)
IRISH_LENITED = tuple("bcdfgmpst")
IRISH_ECLIPSED = {"b": "mb", "c": "gc", "d": "nd", "f": "bhf", "g": "ng", "p": "bp", "t": "dt"}
# Scottish Gaelic: lenition only
GAELIC_LENITED = tuple("bcdfgmpst")


def _first_word_forms(word: str, locale: str) -> set[str]:
    out = {word}
    if locale == "cy":
        for initial, forms in WELSH.items():
            if word.startswith(initial) and (initial != "ll" or word.startswith("ll")):
                rest = word[len(initial) :]
                out.update(f + rest for f in forms)
    elif locale == "ga":
        if word[:1] in IRISH_LENITED and word[1:2] != "h":
            out.add(word[0] + "h" + word[1:])
        if word[:1] in IRISH_ECLIPSED:
            out.add(IRISH_ECLIPSED[word[0]] + word[1:])
    elif locale == "gd":
        if word[:1] in GAELIC_LENITED and word[1:2] != "h":
            out.add(word[0] + "h" + word[1:])
    elif locale == "en" and " " not in word and len(word) > 2:
        out.add(word + "s")
        if word.endswith(("s", "x", "ch", "sh")):
            out.add(word + "es")
    return {w for w in out if w}


def variants(phrase: str, locale: str) -> set[str]:
    """The phrase with its first word in every form it takes in ``locale``."""
    phrase = " ".join(phrase.split())
    if not phrase:
        return set()
    first, _, rest = phrase.partition(" ")
    # Welsh "ll" and "rh" count as one letter: try the longer initial first
    forms = _first_word_forms(first, locale)
    return {f"{form} {rest}".strip() for form in forms}
