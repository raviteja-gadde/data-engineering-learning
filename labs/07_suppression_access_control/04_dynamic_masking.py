"""Lab 04: Dynamic Column Masking — Same Row, Hidden Columns per Role

A masking function replaces individual scores with NULL for
non-privileged roles. Privileged users see full data; others see
only the team name and response count but not actual scores.

Demonstrates column-level access control vs row-level (Lab 03).
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.pg import pg_conn
from shared.display import print_table, print_sql, print_panel

ROLES = ["analyst_role", "viewer_role"]


def setup(conn):
    """Create roles, table, masking function, and masked view."""
    cur = conn.cursor()

    # ── Clean up ─────────────────────────────────────────────────
    cur.execute("DROP VIEW IF EXISTS team_scores_masked;")
    cur.execute("DROP TABLE IF EXISTS team_scores_raw CASCADE;")
    cur.execute("DROP FUNCTION IF EXISTS mask_score(NUMERIC);")
    for role in ROLES:
        cur.execute(f"DROP ROLE IF EXISTS {role};")

    # ── Create roles ─────────────────────────────────────────────
    for role in ROLES:
        cur.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pass';")
    # analyst_role is the privileged role
    conn.commit()

    # ── Raw scores table ─────────────────────────────────────────
    cur.execute("""
        CREATE TABLE team_scores_raw (
            team_name       VARCHAR,
            avg_q01         NUMERIC(3,2),
            avg_q07         NUMERIC(3,2),
            avg_q10         NUMERIC(3,2),
            response_count  INTEGER
        );
    """)
    cur.execute("""
        INSERT INTO team_scores_raw VALUES
            ('Team Alpha',  4.20, 3.80, 3.10, 10),
            ('Team Beta',   3.50, 3.20, 2.80,  8),
            ('Team Gamma',  3.90, 4.10, 3.60,  6),
            ('Team Delta',  2.80, 2.50, 2.10,  3);
    """)

    # ── Masking function ─────────────────────────────────────────
    cur.execute("""
        CREATE OR REPLACE FUNCTION mask_score(val NUMERIC)
        RETURNS NUMERIC AS $$
        BEGIN
            -- Only analyst_role sees the actual score
            IF current_user = 'analyst_role' THEN
                RETURN val;
            ELSE
                RETURN NULL;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── Masked view — all roles query this ───────────────────────
    cur.execute("""
        CREATE VIEW team_scores_masked AS
        SELECT team_name,
               mask_score(avg_q01)  AS avg_q01,
               mask_score(avg_q07)  AS avg_q07,
               mask_score(avg_q10)  AS avg_q10,
               response_count       -- count is always visible
        FROM team_scores_raw;
    """)

    for role in ROLES:
        cur.execute(f"GRANT SELECT ON team_scores_masked TO {role};")
    conn.commit()

    print_panel("Setup", "Table + masking function + masked view created.\n"
                "analyst_role: sees scores. viewer_role: scores masked to NULL.")


def query_as_role(conn, role, query):
    cur = conn.cursor()
    cur.execute(f"SET ROLE {role};")
    cur.execute(query)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.execute("RESET ROLE;")
    return cols, rows


def teardown(conn):
    cur = conn.cursor()
    cur.execute("DROP VIEW IF EXISTS team_scores_masked;")
    cur.execute("DROP TABLE IF EXISTS team_scores_raw CASCADE;")
    cur.execute("DROP FUNCTION IF EXISTS mask_score(NUMERIC);")
    for role in ROLES:
        cur.execute(f"DROP ROLE IF EXISTS {role};")
    conn.commit()


def main():
    query = "SELECT * FROM team_scores_masked;"

    with pg_conn(autocommit=True) as conn:
        setup(conn)

        print_panel("The Query (same for both roles)", query)

        # ── analyst_role: full access ────────────────────────────
        print_sql(f"SET ROLE analyst_role;\n{query}")
        cols, rows = query_as_role(conn, "analyst_role", query)
        print_table("Results as analyst_role (privileged)", cols, rows)

        # ── viewer_role: masked ──────────────────────────────────
        print_sql(f"SET ROLE viewer_role;\n{query}")
        cols, rows = query_as_role(conn, "viewer_role", query)
        print_table("Results as viewer_role (scores masked)", cols, rows)

        print_panel(
            "Key Takeaway",
            "Column masking hides VALUES, not ROWS.\n"
            "The viewer sees all teams but scores are NULL.\n"
            "Combined with RLS (Lab 03), you control both which\n"
            "rows and which columns each role can access.\n\n"
            "In production: Snowflake has native MASKING POLICY;\n"
            "PostgreSQL achieves it via functions + views as shown here."
        )

        teardown(conn)
        print_panel("Cleanup", "Roles, function, view, and table dropped.")


if __name__ == "__main__":
    main()
