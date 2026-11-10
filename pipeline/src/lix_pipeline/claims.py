"""The numbers the docs state, checked against what the repository computes.

``lix docs --check`` runs :func:`check_claims` beside the generated-file check, so a count
that has gone stale in README.md, docs/*.md, CITATION.cff, .env.example or pyproject.toml
fails a pull request (#253: an audit on 4 November 2026 found about twenty).

The check is a table of patterns (``CLAIMS``), each tied to one fact and to the words that
make a number unambiguous ("62,833 neighbourhoods", "58 cases"), plus two kinds of table
(a model's row of a results table, a category's row of the eval cases table) and a few
phrases after a model's name ("answers in about 4 seconds"). A number no pattern claims
is not looked at: a false alarm would block every pull request, so the check is narrow on
purpose.

- An unhedged count must be exact. A hedged one ("about 224", "nearly", "over") passes
  within ``TOLERANCE`` (2%) of the fact, and "over", "more than" and "at least" must not
  exceed it.
- A count at or below half the fact is taken to be about some of them, not all ("five
  areas were scored on two themes"), and is left alone: the check is for totals that
  have drifted, which sit near the fact.
- Seconds, costs and word counts pass when the report's value, rounded to the precision
  the doc states, is the stated one ("4 s" for 3.56, "about $0.03" for 0.0274).
- CHANGELOG.md is never read: past releases state the numbers of their day. Nor is
  docs/data-sources.md, which is generated whole from the registry (its descriptions
  count other things: the Data Zones of 2011, a force's LSOAs).
- A line carrying ``claims: ignore`` (in a comment: ``<!-- claims: ignore -->``, or
  ``# claims: ignore`` outside Markdown) is skipped, for a number that is right for a
  reason the patterns cannot see. The app prints an HTML comment in methodology.md,
  evals.md and validation.md as text, so reword the sentence there instead.

Every fact comes from committed files (config/, api/evals/cases.yaml, docs/evals/*.json),
so CI computes it without the data pack. What only the pack or a machine knows (download
sizes, how fast the map recolours, how many checks pass) is not checked here.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from lix_core.config import (
    get_config,
    load_indicators,
    load_label_catalogues,
    load_nations,
    load_registry,
    load_weights,
)
from lix_core.paths import get_project_root

# How far a hedged count ("about 224") may sit from the fact, as a share of the fact
TOLERANCE = 0.02
IGNORE = "claims: ignore"
# Beside docs/*.md. CHANGELOG.md is deliberately absent (see the module docstring)
SCANNED = ("README.md", "CITATION.cff", ".env.example", "pyproject.toml")
UNSCANNED_DOCS = frozenset({"data-sources.md"})


@dataclass(frozen=True)
class ModelRun:
    """One committed eval report (docs/evals/<model>.json)."""

    model: str  # as run: openrouter:anthropic/claude-haiku-5.5
    report: str  # the report's file name
    passed: int
    cases: int
    run: date
    p50: float | None = None  # seconds per turn
    p95: float | None = None
    cost: float | None = None  # dollars per case
    words: float | None = None  # median reply length


# ---------------------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------------------


def config_facts() -> dict:
    """Counts read from config/: areas, indicators, sources, themes, presets, languages
    and the face-validity review."""
    from lix_pipeline.stage import credited_sources

    nations = load_nations().nations
    catalogue = load_indicators()
    out: dict = {}
    # The areas each nation is expected to have, which `lix validate serve` holds every
    # full pack to
    areas = {code: nation.levels["low"].count for code, nation in nations.items()}
    if all(areas.values()):
        out["areas"] = sum(areas.values())
    out.update({f"areas.{code}": count for code, count in areas.items() if count})

    roles = Counter(i.role for i in catalogue.indicators)
    out["indicators"] = len(catalogue.indicators)
    out.update({f"indicators.{role}": roles[role] for role in ("scored", "context", "diagnostic")})

    out["sources.registered"] = len(load_registry())
    # "In use" is what a pack's manifest credits: the same list, from the same function
    out["sources.in_use"] = len(credited_sources(catalogue))

    out["themes"] = len(catalogue.themes)
    for code, nation in nations.items():
        out[f"themes.{code}"] = len(catalogue.themes) - len(nation.theme_gaps)
    out["presets"] = len(load_weights().presets)
    out["languages"] = 1 + len(load_label_catalogues())  # English lives in the config itself

    places = get_config("validation_places")["places"]
    checks = [check for place in places for check in place.get("checks", [])]
    out["validation.places"] = len(places)
    out["validation.checks"] = len(checks)
    out["validation.anchors"] = sum(bool(check.get("anchor")) for check in checks)
    return out


def eval_facts(cases: Path, reports: Path) -> dict:
    """The eval suite (cases.yaml) and each model's committed report."""
    listed = yaml.safe_load(cases.read_text())["cases"]
    grounded = [c for c in listed if (c.get("expect") or {}).get("grounded")]
    out: dict = {
        "eval.cases": len(listed),
        "eval.grounded": len(grounded),
        # Grounding checked outside the grounding category: the "(+29)" of docs/evals.md
        "eval.grounded.elsewhere": sum(c["category"] != "grounding" for c in grounded),
    }
    for category, count in Counter(c["category"] for c in listed).items():
        out[f"eval.category.{category}"] = count

    runs = []
    for path in sorted(reports.glob("*.json")):
        report = json.loads(path.read_text())
        runs.append(
            ModelRun(
                model=report["model"],
                report=path.name,
                passed=report["passed"],
                cases=report["cases"],
                run=datetime.fromisoformat(report["run_at"]).date(),
                p50=report.get("turn_seconds_p50"),
                p95=report.get("turn_seconds_p95"),
                cost=report.get("cost_per_case"),
                words=report.get("reply_words_median"),
            )
        )
    out["eval.runs"] = runs
    out["eval.models"] = len(runs)
    out["eval.passed"] = sorted(run.passed for run in runs)
    out["eval.run_dates"] = sorted({run.run for run in runs})
    return out


def facts() -> dict:
    """Every number the claims check knows, from committed files only."""
    root = get_project_root()
    return {
        **config_facts(),
        **eval_facts(root / "api" / "evals" / "cases.yaml", root / "docs" / "evals"),
    }


# Where a fact comes from, for the message (the longest matching prefix wins)
SOURCES = {
    "areas": "config/nations.yaml",
    "indicators": "config/indicators.yaml",
    "sources.registered": "config/datasets.yaml",
    "sources.in_use": "the sources of config/indicators.yaml and the backbone",
    "themes": "config/indicators.yaml",
    "themes.": "config/indicators.yaml less the nation's theme_gaps in config/nations.yaml",
    "presets": "config/weights.yaml",
    "languages": "config/i18n/ and English",
    "eval": "docs/evals/*.json",
    "eval.cases": "api/evals/cases.yaml",
    "eval.grounded": "api/evals/cases.yaml",
    "eval.category": "api/evals/cases.yaml",
    "validation": "config/validation_places.yaml",
}


def _source(fact: str) -> str:
    return SOURCES[max((p for p in SOURCES if fact.startswith(p)), key=len)]


# ---------------------------------------------------------------------------------------
# Numbers as the docs write them
# ---------------------------------------------------------------------------------------

WORDS = {
    word: n
    for n, word in enumerate(
        "one two three four five six seven eight nine ten eleven twelve".split(), start=1
    )
}
MONTHS = (
    "January February March April May June July August September October November December"
).split()

_HEDGE = (
    r"(?:\b(?P<hedge>about|around|roughly|approximately|nearly|almost|over|more\s+than"
    r"|at\s+least)\s+)?"
)
_FLOORS = ("over", "more than", "at least")
# Not the tail of another number, a range ("80–120") or a word ("twenty-nine")
_START = r"(?<![\w.,–-])"
_ANY = r"\d{1,3}(?:,\d{3})+|\d+"
_TOKENS = {
    # Any whole number
    "<N>": _ANY,
    # ... or one to twelve as a word, where the docs spell small counts out
    "<W>": _ANY + "|" + "|".join(WORDS),
    # A total in the thousands
    "<T>": r"\d{1,3}(?:,\d{3})+|\d{4,}",
    # A count as the docs write one, with separators: a bare four-digit number before a
    # geography's name is its year ("the 2011 Data Zones")
    "<C>": r"\d{1,3}(?:,\d{3})+|\d{1,3}",
}
_DECIMAL = r"\d+(?:\.\d+)?"
_DATE = (
    r"(?P<day>\d{1,2})\s+(?P<month>" + "|".join(MONTHS) + r")(?:\s+(?P<year>\d{4}))?"
    r"|(?P<iso>\d{4}-\d{2}-\d{2})"
)


def _pattern(text: str) -> re.Pattern:
    for token, body in _TOKENS.items():
        text = text.replace(token, f"{_HEDGE}{_START}(?P<n>{body})")
    return re.compile(text, re.IGNORECASE)


def _number(text: str) -> int:
    return WORDS.get(text.lower()) or int(text.replace(",", ""))


def _count_agrees(stated: int, hedge: str | None, actual: int) -> bool:
    if not hedge:
        return stated == actual
    if " ".join(hedge.lower().split()) in _FLOORS and stated > actual:
        return False
    return abs(stated - actual) <= TOLERANCE * actual


def _rounds_to(actual: float, stated: str) -> bool:
    """Whether ``actual``, rounded to the decimals ``stated`` shows, is ``stated``."""
    step = Decimal(1).scaleb(-len(stated.partition(".")[2]))
    return Decimal(str(actual)).quantize(step, rounding=ROUND_HALF_UP) == Decimal(stated)


def _date_agrees(match: re.Match, dates: list[date]) -> bool:
    """A stated day against the one day every report was run on (no year: any year)."""
    if len(dates) != 1:
        return False
    if match["iso"]:
        return match["iso"] == dates[0].isoformat()
    stated = (int(match["day"]), MONTHS.index(match["month"].capitalize()) + 1)
    year_agrees = match["year"] is None or int(match["year"]) == dates[0].year
    return stated == (dates[0].day, dates[0].month) and year_agrees


def _long_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


# ---------------------------------------------------------------------------------------
# The claims
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    fact: str | tuple[str, ...]  # several: the number may be any of them
    pattern: re.Pattern


def _claim(fact: str | tuple[str, ...], text: str) -> Claim:
    return Claim(fact, _pattern(text))


_SCORES_ON = r"\b[^.]{0,60}?\bscores?\s+on\s+<W>\s+themes"

# In order: an earlier claim takes its number, and a later pattern leaves that number
# alone ("checked on 31 cases" is the grounded count, not the size of the suite)
CLAIMS: tuple[Claim, ...] = (
    # --- areas (config/nations.yaml: each nation's expected low-level count)
    _claim("areas", r"<T>\s+neighbourhoods"),
    _claim("areas", r"\ball\s+<T>\s+areas"),
    _claim("areas", r"#1\s+of\s+<T>"),
    _claim("areas.S", r"2022\s+Data\s+Zone\s+\(<C>\)"),
    _claim("areas.S", r"\bthe\s+<C>\s+Data\s+Zones\s+(?!of\b|in\b)"),
    _claim("areas.N", r"Super\s+Data\s+Zone\s+\(<C>[,)]"),
    _claim("areas.N", r"\bthe\s+<C>\s+Super\s+Data\s+Zones\s+(?!of\b|in\b)"),
    _claim("areas.I", r"Small\s+Area\s+\(<C>\)"),
    _claim("areas.I", r"\bthe\s+<C>\s+Small\s+Areas\s+(?!of\b|in\b)"),
    # --- indicators, by role (digits only: "two indicators stand in" is not the total)
    _claim("indicators.scored", r"\bthe\s+<N>\s+that\s+count\s+towards\s+the\s+score"),
    _claim("indicators.scored", r"<N>\s+scored(?:\s+(?:indicators|across|and)\b|\s*[,;)])"),
    _claim("indicators.context", r"<N>\s+context\s+(?:indicators|measures)"),
    _claim("indicators.diagnostic", r"<N>\s+diagnostic\b"),
    _claim("indicators", r"<N>\s+indicators\b"),
    # --- sources: "open datasets" is the README's phrase for those in use
    _claim("sources.in_use", r"<N>\s+open\s+datasets"),
    _claim("sources.registered", r"<N>\s+in\s+the\s+registry"),
    _claim(("sources.in_use", "sources.registered"), r"<N>\s+datasets\b"),
    # --- themes, presets, languages (the docs spell these out, so words count). A nation
    # with declared gaps scores on fewer themes, and the methodology says how many
    _claim("themes.N", r"\bNorthern\s+Ireland" + _SCORES_ON),
    _claim("themes.N", r"\bNorthern\s+Ireland['’]s\s+<W>\s+themes"),
    _claim("themes.I", r"(?<!Northern\s)\b(?:Ireland|Irish)" + _SCORES_ON),
    _claim("themes.I", r"(?<!Northern\s)\bIreland['’]s\s+<W>\s+themes"),
    _claim("themes", r"<W>\s+(?:scored\s+)?themes\b"),
    _claim("presets", r"<W>\s+(?:ready-made\s+|persona\s+|built-in\s+)?presets\b"),
    # English and the translations; "the other three languages" is the translations alone
    _claim("languages", r"\binterface\s+in\s+<W>\s+languages\b"),
    _claim("languages", r"<W>\s+interface\s+languages\b"),
    # --- the eval suite (api/evals/cases.yaml) and how many models have a report
    _claim("eval.grounded", r"\bchecked\s+on\s+<N>\s+cases"),
    _claim("eval.grounded", r"<N>\s+of\s+them\s+also\s+checking"),
    # ... and elsewhere either count: "what is checked (31 cases)"
    _claim(("eval.cases", "eval.grounded"), r"<N>\s+(?:assistant\s+)?(?:eval\s+)?cases\b"),
    _claim("eval.models", r"<W>\s+(?:models\s+)?are\s+evaluated"),
    _claim("eval.models", r"\brun\s+on\s+<W>\s+models"),
    # --- the face-validity review (config/validation_places.yaml)
    _claim("validation.anchors", r"<N>\s+of\s+them\s+run\s+on\s+every\s+build"),
    _claim("validation.anchors", r"<N>\s+anchors\b"),
    _claim("validation.checks", r"<N>\s+(?:face-validity\s+)?checks\b"),
    _claim("validation.places", r"<N>\s+(?:contrasting\s+|well-known\s+)?places\b"),
)

# "(56, 56 and 52 passed": every model's pass count, in any order
_PASS_COUNTS = re.compile(r"\((?P<list>\d+(?:,\s*\d+)*,?\s+and\s+\d+)\s+passed\b", re.IGNORECASE)
# The day the committed reports were run, where the docs name it
_RUN_DATE = re.compile(
    r"\b(?:full\s+run\s+each\s+on|misses\s+on|passed\s+on)\s+(?:" + _DATE + ")", re.IGNORECASE
)

# The columns of a results table (its first header cell starts with "Model") → the field
MODEL_COLUMNS = {
    "passed": "passed",
    "eval cases passed": "passed",
    "turn p50": "p50",
    "typical answer": "p50",
    "turn p95": "p95",
    "cost per question": "cost",
    "reply words (median)": "words",
    "run": "run",
}
# The rows of the "Category | Cases" table whose label is not the category's id
CATEGORY_LABELS = {
    "look-ups": "lookup",
    "follow-ups": "multi_turn",
    "scope and ethics": "scope_ethics",
    "languages": "language",
    "profiles": "profile",
}

# What follows a model's name, up to the end of its clause
_CLAUSE_END = re.compile(r"[.!?;:](?=\s|$)|\n[ \t]*\n|\|")
_PROSE = (
    (
        "p50",
        re.compile(
            rf"\bin\s+(?:about\s+)?(?P<n>{_DECIMAL})\s+(?:seconds?|s)\b"
            # "... and Opus 5.5 in about 6 (evals)": the unit came a clause earlier, so
            # the number must end the phrase
            rf"|\bin\s+about\s+(?P<m>{_DECIMAL})(?=\s*(?:[(),;]|and\b|\Z))",
            re.IGNORECASE,
        ),
    ),
    ("cost", re.compile(rf"\$(?P<n>{_DECIMAL})\s+(?:a|per)\s+question", re.IGNORECASE)),
    (
        "misses",
        re.compile(r"\A['’]s\s+(?P<n>\d+|" + "|".join(WORDS) + r")\s+misses\b", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class Found:
    """One number a file states and what the repository says of it."""

    offset: int  # of the number, in the file's text
    said: str
    actual: str
    source: str
    agrees: bool


@dataclass(frozen=True)
class Mismatch:
    file: str
    line: int
    said: str
    actual: str
    source: str

    def __str__(self) -> str:
        return (
            f'{self.file}:{self.line}: says "{self.said}", '
            f"the repo says {self.actual} ({self.source})"
        )


def _plain(text: str) -> str:
    """The text with emphasis, code marks and line-leading comment or quote marks blanked,
    one character for one, so "**62,833 neighbourhoods**" reads as prose and every offset
    still points into the file."""
    text = re.sub(r"[*`]", " ", text)
    return re.sub(r"(?m)^([ \t]*)([#>]+)", lambda m: m[1] + " " * len(m[2]), text)


def _said(text: str) -> str:
    return " ".join(text.split())


# --- counts ---------------------------------------------------------------------------


def _counts(text: str, known: dict):
    taken: list[tuple[int, int]] = []
    for claim in CLAIMS:
        names = (claim.fact,) if isinstance(claim.fact, str) else claim.fact
        for match in claim.pattern.finditer(text):
            start, end = match.span("n")
            if any(start < e and s < end for s, e in taken):
                continue
            taken.append((start, end))
            if not all(name in known for name in names):
                continue
            actual = [known[name] for name in names]
            stated = _number(match["n"])
            if 2 * stated <= min(actual):
                continue  # some of them, not the total: "scored on two themes"
            yield Found(
                start,
                _said(match[0]),
                " or ".join(f"{value:,}" for value in actual),
                _source(names[0]),
                any(_count_agrees(stated, match["hedge"], value) for value in actual),
            )
    if "eval.passed" in known:
        for match in _PASS_COUNTS.finditer(text):
            stated = sorted(int(n) for n in re.findall(r"\d+", match["list"]))
            yield Found(
                match.start("list"),
                _said(match[0]),
                ", ".join(str(n) for n in sorted(known["eval.passed"], reverse=True)),
                _source("eval"),
                stated == known["eval.passed"],
            )
    if known.get("eval.run_dates"):
        for match in _RUN_DATE.finditer(text):
            yield Found(
                match.start("day") if match["day"] else match.start("iso"),
                _said(match[0]),
                ", ".join(_long_date(day) for day in known["eval.run_dates"]),
                _source("eval"),
                _date_agrees(match, known["eval.run_dates"]),
            )


# --- models ---------------------------------------------------------------------------


def _name_tokens(model: str) -> list[str]:
    """openrouter:anthropic/claude-haiku-5.5 → claude, haiku, 5.5; and qwen3.8-27b →
    qwen, 3.8, 27b."""
    return re.findall(r"[a-z]+|\d+(?:\.\d+)*[a-z]*", re.split(r"[:/]", model.lower())[-1])


def model_names(runs: list[ModelRun]) -> dict[ModelRun, re.Pattern]:
    """How the docs may name each model with a report: in full ("Claude Haiku 5.5"), from
    the first word no other report shares ("Haiku 5.5"), or by that word alone ("Haiku"),
    but never that word with another version after it."""
    out = {}
    gap = r"[\s._-]*"
    for run in runs:
        tokens = _name_tokens(run.model)
        others = {t for other in runs if other is not run for t in _name_tokens(other.model)}
        forms = [gap.join(re.escape(t) for t in tokens)]
        own = next(
            (i for i, t in enumerate(tokens) if t.isalpha() and len(t) > 2 and t not in others),
            None,
        )
        if own is not None:
            if own:
                forms.append(gap.join(re.escape(t) for t in tokens[own:]))
            forms.append(re.escape(tokens[own]) + rf"(?!{gap}\d)")
        out[run] = re.compile(r"\b(?:" + "|".join(forms) + r")\b", re.IGNORECASE)
    return out


def _model_of(cell: str, names: dict[ModelRun, re.Pattern]) -> ModelRun | None:
    named = [run for run, pattern in names.items() if pattern.search(cell)]
    return named[0] if len(named) == 1 else None


def _measure(run: ModelRun, field: str, stated: str) -> tuple[str, bool] | None:
    """A report's seconds, cost or word count beside the figure a doc states for it: how
    the report's value reads, and whether it rounds to the stated one."""
    actual = getattr(run, field)
    if actual is None:
        return None
    shown = {"cost": f"${actual}", "words": f"{actual:g}"}.get(field, f"{actual} s")
    return shown, _rounds_to(actual, stated)


def _cell(run: ModelRun, field: str, cell: str) -> tuple[str, bool] | None:
    """A results-table cell against the report (None: not in a form this check reads)."""
    if field == "passed":
        if m := re.search(r"(\d+)\s*/\s*(\d+)", cell):
            return f"{run.passed}/{run.cases}", (int(m[1]), int(m[2])) == (run.passed, run.cases)
    elif field == "run":
        if m := re.search(_DATE, cell, re.IGNORECASE):
            return run.run.isoformat(), _date_agrees(m, [run.run])
    else:
        lead = r"\$" if field == "cost" else ""
        unit = r"\s*s\b" if field in ("p50", "p95") else ""
        if m := re.search(rf"{lead}({_DECIMAL}){unit}", cell):
            return _measure(run, field, m[1])
    return None


def _cells(row: str) -> list[tuple[int, str]]:
    """A Markdown table row's cells, each with its offset within the row."""
    first = row.index("|") + 1
    out, start = [], first
    for bar in re.finditer(r"(?<!\\)\|", row[first:]):
        out.append((start, row[start : first + bar.start()]))
        start = first + bar.end()
    return out


def _tables(text: str, known: dict, names: dict[ModelRun, re.Pattern]):
    """The rows of a results table (a model against its report) and of the eval
    categories' table (a category against cases.yaml)."""
    header: list[str] | None = None
    offset = 0
    for row in text.split("\n"):
        at, offset = offset, offset + len(row) + 1
        if not row.lstrip().startswith("|"):
            header = None
            continue
        cells = _cells(row)
        if header is None:
            header = [cell.strip().lower() for _, cell in cells]
            continue
        if not cells or len(cells) != len(header):
            continue
        label = cells[0][1].strip()
        if header[0].startswith("model") and (run := _model_of(label, names)):
            for column, (start, cell) in zip(header, cells, strict=True):
                field = MODEL_COLUMNS.get(column)
                if field and (read := _cell(run, field, cell)):
                    source = f"docs/evals/{run.report}"
                    yield Found(at + start, _said(cell), read[0], source, read[1])
        elif header[:2] == ["category", "cases"]:
            category = CATEGORY_LABELS.get(label.lower(), label.lower())
            start, cell = cells[1]
            m = re.fullmatch(r"\s*(\d+)(?:\s*\(\+(\d+)\))?\s*", cell)
            count = known.get(f"eval.category.{category}")
            if not m or count is None:
                continue
            # "2 (+29)": the grounding cases, and the cases elsewhere that check it too
            elsewhere = known.get("eval.grounded.elsewhere") if category == "grounding" else None
            extra = m[2] if elsewhere is not None else None
            yield Found(
                at + start,
                _said(cell),
                f"{count} (+{elsewhere})" if extra else str(count),
                _source("eval.category"),
                int(m[1]) == count and (extra is None or int(extra) == elsewhere),
            )


def _prose(text: str, names: dict[ModelRun, re.Pattern]):
    """What is said of a model in the clause after its name: its typical answer time, its
    cost a question, how many cases it missed."""
    mentions = sorted(
        (
            (m.start(), m.end(), run)
            for run, pattern in names.items()
            for m in pattern.finditer(text)
        ),
        key=lambda mention: mention[:2],
    )
    for i, (_, end, run) in enumerate(mentions):
        stop = min(mentions[i + 1][0] if i + 1 < len(mentions) else len(text), end + 200)
        clause = text[end:stop]
        if cut := _CLAUSE_END.search(clause):
            clause = clause[: cut.start()]
        for field, pattern in _PROSE:
            for m in pattern.finditer(clause):
                group = "n" if m["n"] else "m"
                if field == "misses":
                    missed = run.cases - run.passed
                    read = str(missed), _number(m[group]) == missed
                else:
                    read = _measure(run, field, m[group])
                if read:
                    source = f"docs/evals/{run.report}"
                    yield Found(end + m.start(group), _said(m[0]), read[0], source, read[1])


# ---------------------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------------------


def claims_in(text: str, known: dict) -> list[Found]:
    """Every number in one file's text that a pattern ties to a fact in ``known`` (see
    :func:`facts`), whether or not it agrees, in the file's order."""
    plain = _plain(text)
    names = model_names(known.get("eval.runs", []))
    found = [*_counts(plain, known), *_tables(plain, known, names), *_prose(plain, names)]
    return sorted(found, key=lambda claim: claim.offset)


def scan(text: str, known: dict, name: str) -> list[Mismatch]:
    """The claims in one file's text that disagree with ``known``, less any on a line
    that opts out."""
    lines = text.split("\n")
    out = []
    for claim in claims_in(text, known):
        line = text.count("\n", 0, claim.offset) + 1
        if not claim.agrees and IGNORE not in lines[line - 1]:
            out.append(Mismatch(name, line, claim.said, claim.actual, claim.source))
    return out


def scanned_files(root: Path) -> list[Path]:
    """The files whose claims are checked: the hand-written docs, never CHANGELOG.md."""
    docs = sorted(p for p in (root / "docs").glob("*.md") if p.name not in UNSCANNED_DOCS)
    return [p for p in [*(root / name for name in SCANNED), *docs] if p.is_file()]


def check_claims(root: Path | None = None, known: dict | None = None) -> list[str]:
    """One line for each stated number the repository contradicts:
    ``file:line: says "X", the repo says Y (where Y comes from)``."""
    root = root or get_project_root()
    known = facts() if known is None else known
    return [
        str(mismatch)
        for path in scanned_files(root)
        for mismatch in scan(path.read_text(), known, path.relative_to(root).as_posix())
    ]
