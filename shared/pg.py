"""PostgreSQL connection helper (local container via psycopg2)."""

from contextlib import contextmanager
import subprocess
import sys

import psycopg2

PG_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "learning",
    "password": "learning",
    "dbname": "learning_db",
}


def _container_running() -> bool:
    """Check whether the learning-postgres container is running."""
    try:
        result = subprocess.run(
            ["podman", "ps", "--filter", "name=learning-postgres", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        return "learning-postgres" in result.stdout
    except Exception:
        return False


@contextmanager
def pg_conn(autocommit: bool = False):
    """Yield a psycopg2 connection to the local PostgreSQL container.

    Checks that the container is running before attempting to connect.
    Commits on clean exit; rolls back on exception.

    Args:
        autocommit: Set True for DDL-heavy sessions.
    """
    if not _container_running():
        print(
            "[ERROR] The learning-postgres container is not running.\n"
            "Start it with:\n\n"
            "  export DOCKER_HOST=\"unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')\"\n"
            "  podman compose -f docker/docker-compose.postgres.yml up -d\n",
            file=sys.stderr,
        )
        raise RuntimeError("PostgreSQL container not running")

    conn = psycopg2.connect(**PG_CONFIG)
    conn.autocommit = autocommit
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()
