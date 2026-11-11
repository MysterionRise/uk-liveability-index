"""Read-only SQL over the index's tables, for analyst mode.

Guards: a separate in-memory DuckDB reading the public tables as views over the store's
own frames (no second copy), with file and network access disabled and configuration
locked; exactly one SELECT statement; a row, a cell, a character and a reply cap; a cursor
per query so one long query holds nobody else up; and a timeout that interrupts long
queries. DuckDB's memory limit does not count a scalar: ``repeat('x', 1e9)`` built its
gigabyte (2.4 GB of process memory) and the copies on the way to JSON took the API
container down (#208), so the functions that build a string of any size from a number
are refused, text cells are cut in SQL before a value reaches Python, and the engine
runs on two threads so a heavy query cannot starve the rest of the API. A SELECT can also
call functions that change the engine for the next caller (``enable_logging()``), so
those are refused and the table functions are three. Function names are checked twice:
by patterns on the query's text, and in the engine's own parse of it, where quotes and
comments around a name make no difference.
"""

import contextlib
import json
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
# repeat('x', 1000000000) is one gigabyte that the memory limit does not see (#208);
# printf('%400000000s', 'x'), format('{:>N}', 'x') and bitstring('1', N) pad the same way,
# and query()/query_table() run a query held in a string, out of this guard's sight
# (v0.7.1 hardening pass). A blocklist leaks; a child process with its own memory cap is
# the lasting fix (#235)
BUILDERS = (
    "repeat", "rpad", "lpad", "list_resize", "array_resize", "list_repeat", "printf", "format",
    "bitstring", "query", "query_table", "json_execute_serialized_sql",
)  # fmt: skip
STRING_BUILDERS = re.compile(rf"\b({'|'.join(BUILDERS)})\s*\(", re.IGNORECASE)
# A SELECT can call these too, and they change the engine for whoever asks next or read
# what others asked: after `select * from enable_logging()` every caller's queries were
# kept in a log any caller could read, and with storage = 'file' every later query
# failed and the process aborted (v0.7.2 pass). lock_configuration does not stop them
ENGINE_FUNCTIONS = (
    "enable_logging", "disable_logging", "truncate_duckdb_logs", "enable_profiling",
    "disable_profiling", "checkpoint", "force_checkpoint", "write_log", "duckdb_logs",
    "duckdb_log_contexts", "duckdb_logs_parsed",
)  # fmt: skip
ENGINE_STATE = re.compile(rf"\b({'|'.join(ENGINE_FUNCTIONS)})\s*\(", re.IGNORECASE)
# The table functions a query may call: the three that build rows from values. The engine
# has eighty-odd more, which read its settings, memory, files and logs or switch things
# on, and a list of the refused ones would be out of date with each release
TABLE_FUNCTIONS = frozenset({"generate_series", "range", "unnest"})
# The engine's log is a view as well as a function. Nothing is in it while logging stays
# off; it stays shut all the same
LOG_VIEWS = frozenset({"duckdb_logs"})
# A generated sequence is fine for a calendar or a histogram's bins; one of a hundred
# million values (`unnest(range(100000000))`) killed the API container in the v0.7.0
# release pass, below DuckDB's memory limit's notice. Literal bounds only, a million at most
SEQUENCES = re.compile(r"\b(range|generate_series)\s*\(([^()]*)\)", re.IGNORECASE)
MAX_SEQUENCE = 1_000_000
_INT = re.compile(r"^\s*-?\d+\s*$")


def _too_long(nums: list[int]) -> bool:
    """Whether a sequence with these bounds (stop; start, stop; start, stop, step) has
    more than MAX_SEQUENCE values."""
    start, stop = (0, nums[0]) if len(nums) == 1 else (nums[0], nums[1])
    step = abs(nums[2]) if len(nums) == 3 and nums[2] else 1
    return abs(stop - start) // step > MAX_SEQUENCE


def _sequence_problem(sql: str) -> str | None:
    for m in SEQUENCES.finditer(sql):
        name, args = m.group(1).lower(), [a for a in m.group(2).split(",")]
        if not all(_INT.match(a) for a in args) or not 1 <= len(args) <= 3:
            return f"{name}() takes whole-number bounds here (at most {MAX_SEQUENCE:,} values)"
        if _too_long([int(a) for a in args]):
            return f"{name}() builds at most {MAX_SEQUENCE:,} values here"
    # A sequence built any other way (a nested call, a column, an expression) is refused
    if re.search(r"\b(range|generate_series)\s*\((?:[^()]*\()", sql, re.IGNORECASE):
        return (
            "range() and generate_series() take whole-number bounds here "
            f"(at most {MAX_SEQUENCE:,} values)"
        )
    return None


def _refused(name: str) -> str | None:
    """Why a function of this name is not called here, if it is not."""
    if name in BUILDERS:
        return f"{name}() is not allowed: it can build a value of any size"
    if name in ENGINE_FUNCTIONS:
        return f"{name}() is not allowed: it changes the engine's state or reads its log"
    return None


def _parsed(cursor: duckdb.DuckDBPyConnection, sql: str) -> dict:
    """The statement as the engine's own parser read it, a JSON tree in which every
    function call carries its name: ``"repeat"('x', 9)`` and ``repeat/**/('x', 9)``, which
    the patterns on the text let through (v0.7.2 pass), are both ``repeat`` here."""
    (tree,) = cursor.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
    try:
        return json.loads(tree)
    except RecursionError as e:
        # The engine parses calls a thousand deep; Python 3.11 reads a tree about half
        # as deep as that. What cannot be read cannot be checked, and is not run
        raise SqlError("Couldn't check the query: it is nested too deeply") from e


# The types a whole-number literal parses to
_WHOLE = frozenset(
    {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"}
    | {"UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT"}
)


def _constant(node) -> int | None:
    """The value of a parsed node that is a whole-number constant, else None."""
    if not isinstance(node, dict) or node.get("class") != "CONSTANT" or node.get("alias"):
        return None
    value = node.get("value")
    if not isinstance(value, dict) or value.get("is_null"):
        return None
    kind, number = value.get("type"), value.get("value")
    if not isinstance(kind, dict) or kind.get("id") not in _WHOLE:
        return None
    if isinstance(number, dict):  # a HUGEINT comes as its two halves
        number = (number.get("upper", 0) << 64) + number.get("lower", 0)
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def _whole_number(node) -> int | None:
    """The value of a parsed argument that is a whole-number literal, else None. The
    parser folds ``-5`` into one constant; a minus sign left standing over a constant
    is read the same way."""
    if isinstance(node, dict) and node.get("function_name") == "-" and not node.get("alias"):
        inner = node.get("children")
        positive = _constant(inner[0]) if isinstance(inner, list) and len(inner) == 1 else None
        return None if positive is None else -positive
    return _constant(node)


def _parsed_problem(tree: dict) -> str | None:
    """Why the parsed statement may not run, if what it calls or reads is the reason: a
    function refused by name, a table function other than the three, a sequence without
    whole-number bounds or longer than MAX_SEQUENCE, the engine's log."""
    if tree.get("error"):
        return f"Couldn't parse the query: {tree.get('error_message')}"
    # Walked with a list, not by recursion: the engine parses a thousand levels deep
    todo: list = [tree]
    while todo:
        node = todo.pop()
        if isinstance(node, list):
            todo.extend(node)
        if not isinstance(node, dict):
            continue
        todo.extend(node.values())
        if node.get("type") == "TABLE_FUNCTION":
            function = node.get("function")
            name = function.get("function_name") if isinstance(function, dict) else None
            name = name.lower() if isinstance(name, str) else ""
            if name not in TABLE_FUNCTIONS:
                return _refused(name) or (
                    f"{name + '()' if name else 'That table function'} is not available "
                    "here: the table functions are generate_series(), range() and unnest()"
                )
        table = node.get("table_name")
        if node.get("type") == "BASE_TABLE" and isinstance(table, str):
            if table.lower() in LOG_VIEWS:
                return f"{table.lower()} is not available here: it is the engine's own log"
        name = node.get("function_name")
        if not isinstance(name, str):
            continue
        name = name.lower()
        if problem := _refused(name):
            return problem
        if name in ("range", "generate_series"):
            args = node.get("children")
            nums = [_whole_number(a) for a in args] if isinstance(args, list) else []
            if not 1 <= len(nums) <= 3 or None in nums:
                return f"{name}() takes whole-number bounds here (at most {MAX_SEQUENCE:,} values)"
            if _too_long(nums):
                return f"{name}() builds at most {MAX_SEQUENCE:,} values here"
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
        except (TypeError, UnicodeError) as e:
            # Half of a surrogate pair ("\ud800" in a JSON body) cannot be sent to the
            # engine at all: said in our words, not the codec's
            raise SqlError("Couldn't parse the query: it holds a character that is not text") from e
        if len(statements) != 1:
            raise SqlError("Send exactly one statement")
        # PRAGMA table functions parse as a SELECT: the keyword says what they are
        if statements[0].type != duckdb.StatementType.SELECT or re.match(
            r"(pragma|call|set|load|install)\b", sql.lstrip(), re.IGNORECASE
        ):
            raise SqlError("Only SELECT queries are allowed")

        if m := STRING_BUILDERS.search(sql) or ENGINE_STATE.search(sql):
            raise SqlError(_refused(m.group(1).lower()))
        if problem := _sequence_problem(sql):
            raise SqlError(problem)

        body = sql.strip().rstrip(";")
        # On its own lines, so a trailing "-- comment" cannot swallow the closing bracket
        wrapped = f"SELECT * FROM (\n{body}\n) AS q LIMIT {max_rows + 1}"
        cursor = timer = None
        # Everything that touches the engine is inside the try, the cursor's making too:
        # an engine that fails where the tables are attached (it did, with its log sent
        # to a file it could not write) is a refusal like any other, never a 500
        try:
            # A cursor per query: queries run side by side, and one long one holds nobody
            # up. The tables are the store's Arrow views, registered on each cursor
            # (zero-copy)
            cursor = self.con.cursor()
            timer = threading.Timer(timeout_s, cursor.interrupt)
            timer.start()
            if problem := _parsed_problem(_parsed(cursor, sql)):
                raise SqlError(problem)
            for name, table in self._arrow.items():
                cursor.register(name, table)
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
            if timer:
                timer.cancel()
            if cursor:
                with contextlib.suppress(duckdb.Error):
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
