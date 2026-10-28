"""Read-only SQL over the index's tables, for analyst mode.

Guards: a separate in-memory DuckDB reading the public tables as views over the store's
own frames (no second copy), with file and network access disabled and configuration
locked; exactly one SELECT statement; a row, a cell, a character and a reply cap; a cursor
per query so one long query holds nobody else up; and a timeout that interrupts long
queries. DuckDB's memory limit does not count a scalar: ``repeat('x', 1e9)`` built its
gigabyte (2.4 GB of process memory) and the copies on the way to JSON took the API
container down (#208), so the functions that build a string of any size from a number
are refused, text cells are cut in SQL before a value reaches Python, and the engine
runs on two threads so a heavy query cannot starve the rest of the API.
"""

import re
import threading
from dataclasses import dataclass

import duckdb
import polars as pl

from lix_api.store import Store

MAX_ROWS = 5000
# rows × columns a reply may carry (a 22 MB "select * from lsoa" is nobody's answer)
MAX_CELLS = 200_000
# A text (or nested) cell is cut here, with an ellipsis; a reply carries at most this many
# characters of text in all
MAX_CELL_CHARS = 1_000
MAX_CHARS = 4_000_000
TIMEOUT_S = 10.0
# repeat('x', 1000000000) is one gigabyte that the memory limit does not see (#208)
STRING_BUILDERS = re.compile(
    r"\b(repeat|rpad|lpad|list_resize|array_resize|list_repeat)\s*\(", re.IGNORECASE
)
# A generated sequence is fine for a calendar or a histogram's bins; one of a hundred
# million values (`unnest(range(100000000))`) killed the API container in the v0.7.0
# release pass, below DuckDB's memory limit's notice. Literal bounds only, a million at most
SEQUENCES = re.compile(r"\b(range|generate_series)\s*\(([^()]*)\)", re.IGNORECASE)
MAX_SEQUENCE = 1_000_000
_INT = re.compile(r"^\s*-?\d+\s*$")


def _sequence_problem(sql: str) -> str | None:
    for m in SEQUENCES.finditer(sql):
        name, args = m.group(1).lower(), [a for a in m.group(2).split(",")]
        if not all(_INT.match(a) for a in args) or not 1 <= len(args) <= 3:
            return f"{name}() takes whole-number bounds here (at most {MAX_SEQUENCE:,} values)"
        nums = [int(a) for a in args]
        start, stop = (0, nums[0]) if len(nums) == 1 else (nums[0], nums[1])
        step = abs(nums[2]) if len(nums) == 3 and nums[2] else 1
        if abs(stop - start) // step > MAX_SEQUENCE:
            return f"{name}() builds at most {MAX_SEQUENCE:,} values here"
    # A sequence built any other way (a nested call, a column, an expression) is refused
    if re.search(r"\b(range|generate_series)\s*\((?:[^()]*\()", sql, re.IGNORECASE):
        return (
            "range() and generate_series() take whole-number bounds here "
            f"(at most {MAX_SEQUENCE:,} values)"
        )
    return None


TABLES = {
    "lsoa": "Every LSOA in the build: geography (incl. nation), raw__/n__/q__ per indicator, "
    "default scores with country-wide and within-nation percentiles",
    "pois": "Points of interest: category, name, lat, lon, source, licence, detail (JSON)",
    "areas": "MSOAs, local authorities, regions and nations with population and bounding box",
    "places": "Settlements from OS Open Names with their LSOA",
    "indicators": "The indicator catalogue",
}


class SqlError(ValueError):
    pass


@dataclass
class SqlResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


class SqlGuard:
    def __init__(self, store: Store) -> None:
        self.con = duckdb.connect(":memory:")
        catalogue = pl.DataFrame(
            [i.model_dump(exclude={"params"}) for i in store.indicators.values()]
        )
        frames = {
            "lsoa": store.features,
            "pois": store.pois.drop("x", "y"),
            "areas": store.areas,
            "places": store.places,
            "indicators": catalogue,
        }
        # The frames stay the store's: DuckDB reads them through Arrow where they are
        self._arrow = {name: df.to_arrow() for name, df in frames.items()}
        for name, table in self._arrow.items():
            self.con.register(name, table)
        self.con.execute("SET memory_limit = '1GB'")
        self.con.execute("SET threads = 2")
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    def run(self, sql: str, max_rows: int = MAX_ROWS, timeout_s: float = TIMEOUT_S) -> SqlResult:
        try:
            statements = self.con.extract_statements(sql)
        except duckdb.Error as e:
            raise SqlError(f"Couldn't parse the query: {e}") from e
        if len(statements) != 1:
            raise SqlError("Send exactly one statement")
        # PRAGMA table functions parse as a SELECT: the keyword says what they are
        if statements[0].type != duckdb.StatementType.SELECT or re.match(
            r"\s*(pragma|call|set|load|install)\b", sql, re.IGNORECASE
        ):
            raise SqlError("Only SELECT queries are allowed")

        if m := STRING_BUILDERS.search(sql):
            raise SqlError(f"{m.group(1).lower()}() is not allowed: it builds a value of any size")
        if problem := _sequence_problem(sql):
            raise SqlError(problem)

        body = sql.strip().rstrip(";")
        # On its own lines, so a trailing "-- comment" cannot swallow the closing bracket
        wrapped = f"SELECT * FROM (\n{body}\n) AS q LIMIT {max_rows + 1}"
        # A cursor per query: queries run side by side, and one long one holds nobody up.
        # The tables are the store's Arrow views, registered on each cursor (zero-copy)
        cursor = self.con.cursor()
        for name, table in self._arrow.items():
            cursor.register(name, table)
        timer = threading.Timer(timeout_s, cursor.interrupt)
        timer.start()
        try:
            # DESCRIBE plans without running: the column types, so that text and nested
            # columns are cut in SQL and no oversized value is ever copied into Python
            described = cursor.execute(f"DESCRIBE {wrapped}").fetchall()
            projection = ", ".join(_cut_wide(name, type_) for name, type_, *_ in described)
            result = cursor.execute(f"SELECT {projection} FROM (\n{wrapped}\n) AS w")
            columns = [d[0] for d in result.description]
            keep = min(max_rows, max(1, MAX_CELLS // max(1, len(columns))))
            rows: list[list] = []
            chars = 0
            truncated = False
            # Streamed in batches: a result that stops at the cap is never materialised whole
            while not truncated and (batch := result.fetchmany(256)):
                for row in batch:
                    if len(rows) >= keep or chars >= MAX_CHARS:
                        truncated = True
                        break
                    rows.append([_jsonable(v) for v in row])
                    chars += sum(len(v) for v in rows[-1] if isinstance(v, str))
        except duckdb.InterruptException as e:
            raise SqlError(f"Query took longer than {timeout_s:g}s") from e
        except duckdb.Error as e:
            raise SqlError(str(e).split("\n")[0]) from e
        finally:
            timer.cancel()
            cursor.close()
        return SqlResult(columns=columns, rows=rows, truncated=truncated)


def _cut_wide(name: str, type_: str) -> str:
    """The projection of one result column: text, blob and nested values as text cut at
    MAX_CELL_CHARS (nested values reached the reply as their text form before too)."""
    quoted = '"' + name.replace('"', '""') + '"'
    wide = type_.endswith("]") or type_.startswith(
        ("VARCHAR", "BLOB", "JSON", "BIT", "STRUCT", "MAP", "UNION")
    )
    if not wide:
        return quoted
    text = f"CAST({quoted} AS VARCHAR)"
    return (
        f"CASE WHEN length({text}) > {MAX_CELL_CHARS} THEN left({text}, {MAX_CELL_CHARS}) || '…' "
        f"ELSE {text} END AS {quoted}"
    )


def _jsonable(v):
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


_guard: SqlGuard | None = None


def get_guard() -> SqlGuard:
    """The shared guard over the current store (built on first use, ~1 s)."""
    global _guard
    if _guard is None:
        from lix_api.store import get_store

        _guard = SqlGuard(get_store())
    return _guard
