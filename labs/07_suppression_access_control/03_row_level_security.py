"""Lab 03: Row-Level Security — Same Query, Different Results per Role

Creates PostgreSQL roles and RLS policies so that the same
SELECT * FROM team_scores returns different rows depending on
who executes it. Three roles: manager_a (sees Team A), manager_b
(sees Team B), admin (sees all).

Uses SET ROLE to switch context within a single connection.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_sql, print_table
from shared.pg import pg_conn

ROLES = ["manager_a", "manager_b", "survey_admin"]
THE_QUERY = "SELECT team_name, avg_q01, avg_q07, response_count FROM team_scores;"


def setup(conn):
    """Create roles, table, RLS policies. Idempotent."""
    cur = conn.cursor()

    # ── Clean up from previous runs ──────────────────────────────
    cur.execute("DROP TABLE IF EXISTS team_scores CASCADE;")

    for role in ROLES:
        cur.execute(f"DROP ROLE IF EXISTS {role};")

    # ── Create roles ─────────────────────────────────────────────
    for role in ROLES:
        cur.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pass';")
    conn.commit()

    # ── Create table with team scores ────────────────────────────
    cur.execute("""
        CREATE TABLE team_scores (
            team_name       VARCHAR PRIMARY KEY,
            avg_q01         NUMERIC(3,2),
            avg_q07         NUMERIC(3,2),
            response_count  INTEGER,
            manager_role    VARCHAR   -- which role owns this team
        );
    """)
    cur.execute("""
        INSERT INTO team_scores VALUES
            ('Team Alpha',   4.20, 3.80, 10, 'manager_a'),
            ('Team Beta',    3.50, 3.20,  8, 'manager_a'),
            ('Team Gamma',   3.90, 4.10,  6, 'manager_b'),
            ('Team Delta',   2.80, 2.50,  3, 'manager_b');
    """)

    # ── Grant SELECT to all roles ────────────────────────────────
    for role in ROLES:
        cur.execute(f"GRANT SELECT ON team_scores TO {role};")

    # ── Enable RLS ───────────────────────────────────────────────
    cur.execute("ALTER TABLE team_scores ENABLE ROW LEVEL SECURITY;")

    # ── Policy: managers see only their teams ────────────────────
    cur.execute("""
        CREATE POLICY manager_sees_own_teams ON team_scores
            FOR SELECT
            USING (manager_role = current_user);
    """)

    # ── Policy: admin sees everything ────────────────────────────
    cur.execute("""
        CREATE POLICY admin_sees_all ON team_scores
            FOR SELECT
            TO survey_admin
            USING (true);
    """)

    conn.commit()
    print_panel("Setup Complete", "Table team_scores with RLS enabled.\n"
                "Policies: manager sees own teams, admin sees all.")


def query_as_role(conn, role, query):
    """SET ROLE, run query, then RESET ROLE."""
    cur = conn.cursor()
    cur.execute(f"SET ROLE {role};")
    cur.execute(query)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.execute("RESET ROLE;")
    return cols, rows


def teardown(conn):
    """Remove roles and table."""
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS team_scores CASCADE;")
    for role in ROLES:
        cur.execute(f"DROP ROLE IF EXISTS {role};")
    conn.commit()


def main():
    with pg_conn(autocommit=True) as conn:
        setup(conn)

        print_panel("The Query (same for everyone)", THE_QUERY)

        # ── manager_a ───────────────────────────────────────────
        print_sql(f"SET ROLE manager_a;\n{THE_QUERY}")
        cols, rows = query_as_role(conn, "manager_a", THE_QUERY)
        print_table("Results as manager_a (owns Alpha, Beta)", cols, rows)

        # ── manager_b ───────────────────────────────────────────
        print_sql(f"SET ROLE manager_b;\n{THE_QUERY}")
        cols, rows = query_as_role(conn, "manager_b", THE_QUERY)
        print_table("Results as manager_b (owns Gamma, Delta)", cols, rows)

        # ── survey_admin ────────────────────────────────────────
        print_sql(f"SET ROLE survey_admin;\n{THE_QUERY}")
        cols, rows = query_as_role(conn, "survey_admin", THE_QUERY)
        print_table("Results as survey_admin (sees all)", cols, rows)

        print_panel(
            "Key Takeaway",
            "Same table, same query, different results.\n"
            "RLS policies are enforced by the database engine itself —\n"
            "no application code can bypass them. The manager never\n"
            "even knows the other teams' rows exist."
        )

        teardown(conn)
        print_panel("Cleanup", "Roles and table dropped.")


if __name__ == "__main__":
    main()
