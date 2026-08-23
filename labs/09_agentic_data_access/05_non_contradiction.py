"""Lab 05: Non-Contradiction — Agent and Dashboard Must Agree

Two consumers (dashboard and agent tool) access the same data.
Three scenarios show when they agree, when they diverge, and why
a shared serving table is the correct architecture.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dataclasses import dataclass
from shared.duck import duckdb_conn
from shared.pg import pg_conn
from shared.display import print_table, print_panel

# ── Setup: seed both databases with identical data ───────────────────────────

SERVING_DDL = """
CREATE TABLE IF NOT EXISTS gold_team_scores (
    team_id TEXT, project_id TEXT, time_period TEXT,
    overall_score REAL, response_count INT, computed_at TEXT
)
"""

INITIAL_SCORE = 3.85
UPDATED_SCORE = 3.92
TEAM_ID, PROJECT_ID, PERIOD = "t1", "p1", "2025-Q2"


def seed_pg(conn, score: float, computed_at: str):
    cur = conn.cursor()
    cur.execute(SERVING_DDL)
    cur.execute("DELETE FROM gold_team_scores WHERE team_id = %s AND project_id = %s AND time_period = %s",
                (TEAM_ID, PROJECT_ID, PERIOD))
    cur.execute("INSERT INTO gold_team_scores VALUES (%s, %s, %s, %s, %s, %s)",
                (TEAM_ID, PROJECT_ID, PERIOD, score, 18, computed_at))
    conn.commit()


def read_pg(conn) -> tuple[float, str]:
    cur = conn.cursor()
    cur.execute("SELECT overall_score, computed_at FROM gold_team_scores "
                "WHERE team_id = %s AND project_id = %s AND time_period = %s",
                (TEAM_ID, PROJECT_ID, PERIOD))
    row = cur.fetchone()
    return (float(row[0]), row[1]) if row else (0.0, "none")


def seed_duck(conn, score: float, computed_at: str):
    conn.execute(SERVING_DDL)
    conn.execute("DELETE FROM gold_team_scores WHERE team_id = ? AND project_id = ? AND time_period = ?",
                 [TEAM_ID, PROJECT_ID, PERIOD])
    conn.execute("INSERT INTO gold_team_scores VALUES (?, ?, ?, ?, ?, ?)",
                 [TEAM_ID, PROJECT_ID, PERIOD, score, 18, computed_at])


def read_duck(conn) -> tuple[float, str]:
    row = conn.execute("SELECT overall_score, computed_at FROM gold_team_scores "
                       "WHERE team_id = ? AND project_id = ? AND time_period = ?",
                       [TEAM_ID, PROJECT_ID, PERIOD]).fetchone()
    return (round(float(row[0]), 2), row[1]) if row else (0.0, "none")


# ── Scenario Simulations ────────────────────────────────────────────────────

def scenario_1_consistent():
    """Both consumers read the same source at the same time."""
    print_panel("Scenario 1: Both Read Same Source (Consistent)",
                "Dashboard and agent tool both read PostgreSQL serving table.\n"
                "No data changes between reads.")

    with pg_conn() as conn:
        seed_pg(conn, INITIAL_SCORE, "2025-07-01T06:00:00Z")
        dashboard_score, dashboard_ts = read_pg(conn)
        agent_score, agent_ts = read_pg(conn)

    match = "MATCH" if dashboard_score == agent_score else "MISMATCH"
    print_table(
        "Scenario 1 Results",
        ["Consumer", "Score", "Computed At", "Source"],
        [
            ("Dashboard", dashboard_score, dashboard_ts, "PostgreSQL serving table"),
            ("Agent Tool", agent_score, agent_ts, "PostgreSQL serving table"),
        ],
    )
    print(f"  Result: {match}\n")


def scenario_2_inconsistent():
    """Source changes between dashboard read and agent read."""
    print_panel("Scenario 2: Source Changes Between Reads (Inconsistent!)",
                "Dashboard reads PostgreSQL at T1.\n"
                "Pipeline refreshes data.\n"
                "Agent tool computes fresh from DuckDB at T2.")

    with pg_conn() as conn:
        seed_pg(conn, INITIAL_SCORE, "2025-07-01T06:00:00Z")
        dashboard_score, dashboard_ts = read_pg(conn)

    # Pipeline runs — DuckDB has updated data (simulating fresh computation)
    with duckdb_conn() as duck:
        seed_duck(duck, UPDATED_SCORE, "2025-07-01T12:00:00Z")
        agent_score, agent_ts = read_duck(duck)

    match = "MATCH" if dashboard_score == agent_score else "MISMATCH"
    print_table(
        "Scenario 2 Results",
        ["Consumer", "Score", "Computed At", "Source"],
        [
            ("Dashboard", dashboard_score, dashboard_ts, "PostgreSQL (pre-refresh)"),
            ("Agent Tool", agent_score, agent_ts, "DuckDB (post-refresh)"),
        ],
    )
    print(f"  Result: {match}")
    print(f"  Delta: {abs(dashboard_score - agent_score):.2f}")
    print(f"  User sees dashboard showing {dashboard_score} while agent says {agent_score}.")
    print(f"  Both are 'correct' — but the inconsistency destroys trust.\n")


def scenario_3_shared_serving():
    """Both read from the same serving table, even after a refresh."""
    print_panel("Scenario 3: Shared Serving Table (Consistent Regardless)",
                "Both consumers read from the same PostgreSQL serving table.\n"
                "Even after the pipeline refreshes, both see the new data\n"
                "because they share the source.")

    with pg_conn() as conn:
        # Pipeline refreshes the serving table
        seed_pg(conn, UPDATED_SCORE, "2025-07-01T12:00:00Z")

        # Both consumers read from the same table
        dashboard_score, dashboard_ts = read_pg(conn)
        agent_score, agent_ts = read_pg(conn)

    match = "MATCH" if dashboard_score == agent_score else "MISMATCH"
    print_table(
        "Scenario 3 Results",
        ["Consumer", "Score", "Computed At", "Source"],
        [
            ("Dashboard", dashboard_score, dashboard_ts, "PostgreSQL serving table"),
            ("Agent Tool", agent_score, agent_ts, "PostgreSQL serving table"),
        ],
    )
    print(f"  Result: {match}")
    print(f"  Both show {dashboard_score} — consistent by construction.\n")


if __name__ == "__main__":
    scenario_1_consistent()
    scenario_2_inconsistent()
    scenario_3_shared_serving()

    # Summary comparison
    print_table(
        "Architecture Comparison",
        ["Pattern", "Dashboard Source", "Agent Source", "Consistent?", "Why"],
        [
            ("Independent compute", "PostgreSQL query", "DuckDB query",
             "No", "Different sources, different timing"),
            ("Shared serving table", "Gold table", "Gold table",
             "Yes", "Same rows, same values, by construction"),
            ("Shared + tool cache", "Gold table", "Tool cache -> Gold table",
             "Yes", "Cache loaded from same source; TTL bounds staleness"),
        ],
    )

    print_panel(
        "Key Insight",
        "Non-contradiction is an architectural property, not a testing outcome.\n\n"
        "If two consumers compute independently, they WILL eventually diverge.\n"
        "The fix is not 'run the same SQL' — it's 'read the same pre-computed rows.'\n\n"
        "This is the Gold layer from Topic 3 (medallion architecture) doing its job:\n"
        "compute once, serve everywhere, guarantee consistency.",
    )
