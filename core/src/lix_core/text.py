"""Names as people read them: the stray characters a source leaves in, and their repair.

A publisher's export can carry control characters, zero-width marks, stray spaces, or a
UTF-8 string that was read as Latin-1 or Windows-1252 and encoded again ("NestlÃ© UK"
for "Nestlé UK", "Â " for a no-break space, "â€™" or "Ã¢â‚¬â„¢" for a curly apostrophe,
sometimes several times over). The pipeline cleans every name it serves with
``clean_name``; ``lix validate`` refuses a pack where any remains (#212, #222).
"""

import re

import polars as pl

# What Windows-1252 shows for the bytes 0x80–0x9F, which Latin-1 reads as C1 controls
_CP1252_C1 = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
_TRAIL = f"[\\u0080-\\u00bf{_CP1252_C1}]"
# A UTF-8 sequence read as Latin-1 or Windows-1252 shows a lead byte's character followed
# by its continuation bytes' characters (a C1 control, a no-break space, ¢, ©, ® and the
# like, or Windows-1252's € ‚ „ ™): a two-byte lead (Â–ß) takes one, a three-byte lead
# (à–ï) two, a four-byte lead (ð–ô) three. "Ã©" is é, "â€™" is ’; "Sé’s" is a real name,
# because é would need two trailers
MOJIBAKE = rf"[\u00c2-\u00df]{_TRAIL}|[\u00e0-\u00ef]{_TRAIL}{{2}}|[\u00f0-\u00f4]{_TRAIL}{{3}}"
_RUN = re.compile(rf"(?:{MOJIBAKE})+")
# What a repaired run may contain: ASCII and C1 controls (a layer of a text mangled more
# than once, peeled by the next round), Latin letters with accents (Welsh ŵ and ŷ are
# Latin Extended Additional), general punctuation, currency signs, ™
_PLAIN = re.compile(r"^[\x00-\u024f\u1e00-\u1eff\u2000-\u206f\u20a0-\u20cf\u2122]*$")
# Control characters other than whitespace, the replacement character, zero-width and
# bidi marks, word joiners and byte-order marks: dropped from a name
STRAY_CHARS = r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]"
# C1 controls: dropped once no mojibake remains (they are its continuation bytes)
C1_CHARS = r"[\x80-\x9f]"
# A no-break space mangled to "Â\xa0" whose trailer a publisher then turned into a plain
# space ("Â Table!"): a lone Â before a space or at the end is dropped
LONE_A_CIRCUMFLEX = r"\u00c2(?:\s|$)"
# What a publisher writes instead of a name: "N/A", or nothing a person can read (no
# letter or digit: "≈", "#", "^"); served as no name
PLACEHOLDER_NAME = r"^(?i:n/a)$|^[^\p{L}\p{N}]*$"
# What the validator refuses in a name: stray or C1 characters, mojibake, a lone Â, a
# placeholder, a leading, trailing or doubled space
STRAY_NAME = (
    rf"{STRAY_CHARS}|{C1_CHARS}|{MOJIBAKE}|{LONE_A_CIRCUMFLEX}|{PLACEHOLDER_NAME}"
    r"|^\s|\s$|\s\s"
)

_MOJIBAKE_RE = re.compile(MOJIBAKE)


def _decode_run(run: str) -> str:
    """One mojibake run read back through Windows-1252, else Latin-1, when the bytes
    make UTF-8 and the result is plain Latin text; else the run as it came."""
    for codec in ("cp1252", "latin-1"):
        try:
            fixed = run.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if _PLAIN.match(fixed):
            return fixed
    return run


def repair_mojibake(text: str | None) -> str | None:
    """Undo double encoding run by run, as many rounds as it takes (a text mangled
    twice takes two): "St LeonardÃ¢â‚¬â„¢s" → "St Leonard’s". A run that does not read
    back as Latin text ("CAFÉ BAR" with a no-break space) is left alone, and so is
    anything outside a run ("NestlÃ© – York" keeps its dash)."""
    if text is None:
        return None
    for _ in range(6):
        fixed = _RUN.sub(lambda m: _decode_run(m.group()), text)
        if fixed == text:
            break
        text = fixed
    return text


def clean_name(expr: pl.Expr) -> pl.Expr:
    """A name column as people read it: mojibake repaired where it reads back, whitespace
    of every kind (newlines too) collapsed to single spaces, control and zero-width
    characters and a lone Â dropped, trimmed; empty or a placeholder ("N/A", "#") → null.
    A C1 control is dropped only once no mojibake remains, so an unrepairable name stays
    visible to the validator."""
    repaired = (
        pl.when(expr.str.contains(MOJIBAKE))
        .then(expr.map_elements(repair_mojibake, return_dtype=pl.Utf8))
        .otherwise(expr)
    )
    spaced = repaired.str.replace_all(r"\s+", " ").str.replace_all(STRAY_CHARS, "")
    cleaned = (
        pl.when(spaced.str.contains(MOJIBAKE))
        .then(spaced)
        .otherwise(spaced.str.replace_all(C1_CHARS, "").str.replace_all(LONE_A_CIRCUMFLEX, " "))
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
    )
    return (
        pl.when((cleaned == "") | cleaned.str.contains(PLACEHOLDER_NAME))
        .then(None)
        .otherwise(cleaned)
        .alias(expr.meta.output_name())
    )


# --- A name that arrives in capitals ("HEBDEN BRIDGE GROUP PRACTICE"), as people write it

# Kept in capitals whatever their letters; a word with no vowel is kept too (HC, NPC, the
# initials in "DR PS BATH'S PRACTICE") unless it is one of the abbreviations below
ACRONYMS = frozenset(
    "nhs gp gps pcn uk hmp pms gms apms cic llp sas ccg uea raf mod yoi".split()
)  # fmt: skip
ABBREVIATIONS = frozenset(
    "dr drs st rd mr mrs ms mt ctr ctrs cntr cnt ltd grp hth hlth ptnr ptnrs blvd mc".split()
)
# Lower case unless they open the name or a part of it
SMALL_WORDS = frozenset("and of the at for in on by with upon under".split())
# Lower case between hyphens ("Chapel-en-le-Frith", "Chorlton-cum-Hardy")
HYPHEN_BOUND = frozenset({"en", "le", "la", "cum"})
# Welsh: the words that stay lower between two others ("Ysgol y Garreg", "Maes yr Haf"),
# the acronyms of school names that carry a vowel, and w, which is one ("Cwrt", "Pwll")
WELSH_SMALL_WORDS = frozenset("y yr a ac o i yn ar dan ger na o'r a'r".split())
WELSH_ACRONYMS = frozenset({"ygg", "va", "ciw"})
# After "Dr", one or two letters before the surname are initials ("Dr AA Ansari") unless
# they are a surname or a particle of one ("Dr Ng", "Dr Al Mousawi")
TWO_LETTER_NAMES = frozenset(
    "ng li wu xu ma ho le lo yu oo su ko lu hu he vu do an yi mo so ha al el de du la da di".split()
)  # fmt: skip
_TOKEN = re.compile(r"([A-Za-z0-9']+)")
_ORDINAL = re.compile(r"^\d+(st|nd|rd|th)$")
_VOWEL = re.compile(r"[aeiouy]")
_WELSH_VOWEL = re.compile(r"[aeiouyw]")
_PART_BREAK = re.compile(r"\s-|-\s|\($|:\s*$")


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:]


def _title_word(word: str) -> str:
    """One word in title case: Mc and O' take a second capital, a possessive does not."""
    if word.startswith("mc") and len(word) > 3:
        return "Mc" + _cap(word[2:])
    pieces = word.split("'")
    return "'".join(
        _cap(p) if i == 0 or pieces[i - 1] in ("o", "d") else p for i, p in enumerate(pieces)
    )


def title_name(name: str | None, welsh: bool = False) -> str | None:
    """A name written all in capitals, in title case: "THE NHS CENTRE" → "The NHS Centre",
    "DR PS BATH'S PRACTICE" → "Dr PS Bath's Practice", "ST. MARY'S HEALTH CTR" → "St.
    Mary's Health Ctr", "MCKENZIE HOUSE" → "McKenzie House". A name that already has a
    lower-case letter is left as it is. ``welsh``: the name may be Welsh ("YSGOL Y
    GARREG" → "Ysgol y Garreg", "CWRT HENRI" → "Cwrt Henri")."""
    if name is None or any(c.islower() for c in name):
        return name
    vowel = _WELSH_VOWEL if welsh else _VOWEL
    tokens = _TOKEN.split(name)
    out = []
    opening = True  # the next word opens the name, or a part after a dash or a bracket
    after_doctor = False
    for i, token in enumerate(tokens):
        if i % 2 == 0:  # what stands between words
            out.append(token)
            # A spaced dash, a bracket or a colon starts a part; a hyphen joins a word
            if _PART_BREAK.search(token):
                opening = True
            continue
        word = token.lower()
        following = tokens[i + 1] if i + 1 < len(tokens) else ""
        has_next_word = i + 2 < len(tokens)
        between_hyphens = i >= 2 and tokens[i - 1] == "-" and following == "-"
        bracketed = i >= 2 and tokens[i - 1].endswith("(") and following.startswith(")")
        # A Welsh small word stands between spaces: the I of "C.I.W." is an initial
        spaced = i >= 2 and tokens[i - 1].endswith(" ") and following.startswith(" ")
        welsh_small = welsh and spaced and not opening and word in WELSH_SMALL_WORDS
        if any(c.isdigit() for c in word):
            letters = len(word) - len(word.lstrip("abcdefghijklmnopqrstuvwxyz"))
            shown = word if _ORDINAL.match(word) else _cap(word) if letters >= 3 else token
        elif welsh_small:
            shown = word
        elif word in ACRONYMS or (bracketed and len(word) <= 2):
            shown = token  # an acronym, or a code in brackets: "(LA)"
        elif welsh and word in WELSH_ACRONYMS:
            shown = token
        elif after_doctor and word in TWO_LETTER_NAMES:
            shown = _title_word(word)
        elif after_doctor and len(word) <= 2 and following == " " and has_next_word:
            shown = token  # initials
        elif word in ABBREVIATIONS:
            shown = _title_word(word)
        elif len(word) >= 2 and not vowel.search(word):
            shown = token
        elif (word in SMALL_WORDS and not opening) or (word in HYPHEN_BOUND and between_hyphens):
            shown = word
        else:
            shown = _title_word(word)
        out.append(shown)
        # Initials can follow one another ("DR A B SHAH"); anything else ends them
        after_doctor = word in ("dr", "drs") or (after_doctor and shown == token and len(word) <= 2)
        opening = False
    return "".join(out)


def capitals_problems(
    df: pl.DataFrame, table: str, by: str = "source", share: float = 0.5
) -> list[str]:
    """A problem line per value of ``by`` where more than ``share`` of the names are
    written in capitals: a list that arrives that way is put in title case before it is
    served (#258, #310). Some names in capitals pass: brands, codes and acronyms are
    written so ("ALDI", "W4 4NT", "RHND")."""
    if "name" not in df.columns or by not in df.columns:
        return []
    shouting = pl.col("name").str.contains(r"\p{Lu}{2}") & ~pl.col("name").str.contains(r"\p{Ll}")
    counts = (
        df.filter(pl.col("name").is_not_null())
        .group_by(by)
        .agg(pl.len().alias("names"), shouting.sum().alias("capitals"))
        .filter((pl.col("names") >= 20) & (pl.col("capitals") > share * pl.col("names")))
        .sort(by)
    )
    return [
        f"{table}: {capitals:,} of {names:,} names from {source} are all in capitals"
        for source, names, capitals in counts.rows()
    ]


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
