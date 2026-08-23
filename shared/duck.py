"""DuckDB connection helper."""

from contextlib import contextmanager
import duckdb


@contextmanager
def duckdb_conn(db_path: str = ":memory:"):
    """Yield a DuckDB connection, closing it on exit.

    Args:
        db_path: Path to a persistent .duckdb file, or ":memory:" (default)
                 for an ephemeral in-memory database.
    """
    conn = duckdb.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
