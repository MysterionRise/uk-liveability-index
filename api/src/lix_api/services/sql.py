"""Read-only SQL over the index's tables, for analyst mode.

Guards: a separate in-memory DuckDB reading the public tables as views over the store's
own frames (no second copy), with file and network access disabled and configuration
locked; exactly one SELECT statement; a row and a cell cap; a cursor per query so one
long query holds nobody else up; and a timeout that interrupts long queries.
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
TIMEOUT_S = 10.0

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
            result = cursor.execute(wrapped)
            columns = [d[0] for d in result.description]
            rows = result.fetchall()
        except duckdb.InterruptException as e:
            raise SqlError(f"Query took longer than {timeout_s:g}s") from e
        except duckdb.Error as e:
            raise SqlError(str(e).split("\n")[0]) from e
        finally:
            timer.cancel()
            cursor.close()
        keep = min(max_rows, max(1, MAX_CELLS // max(1, len(columns))))
        truncated = len(rows) > keep
        return SqlResult(
            columns=columns,
            rows=[[_jsonable(v) for v in row] for row in rows[:keep]],
            truncated=truncated,
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
