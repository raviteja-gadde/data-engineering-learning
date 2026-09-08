"""Lab 05: Suppression as Policy — Three Enforcement Strategies Compared

Implements the same suppression rule (hide aggregates where n < 4) at
three levels: application, database view, and transformation (dbt pattern).
Compares reliability, auditability, and bypass risk.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_sql, print_table
from shared.duck import duckdb_conn

THRESHOLD = 4


def create_data(conn):
    conn.execute("""
        CREATE TABLE responses (
            employee_id INTEGER, team VARCHAR, q01 INTEGER
        )
    """)
    data = []
    eid = 1
    for score in [4, 5, 3, 4, 5, 4, 3, 4, 3, 5]:  # Team A: 10
        data.append((eid, "Team A", score)); eid += 1
    for score in [2, 3, 1]:                          # Team B: 3
        data.append((eid, "Team B", score)); eid += 1
    for score in [3, 4, 4, 5, 2, 3]:                # Team C: 6
        data.append((eid, "Team C", score)); eid += 1
    conn.executemany("INSERT INTO responses VALUES (?, ?, ?)", data)
    print_panel("Data", f"{len(data)} responses: Team A (10), Team B (3), Team C (6)")


def approach_1_application(conn):
    """Python function wrapping raw results -- the fragile way."""
    print_panel("Approach 1: Application-Level",
                "Python nullifies small groups. Every consumer must call suppress().")
    sql = "SELECT team, COUNT(*) AS n, ROUND(AVG(q01), 2) AS avg_q01\nFROM responses GROUP BY team ORDER BY team"
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Raw Query (no suppression)", ["team", "n", "avg_q01"], rows)

    suppress = lambda rows: [(r[0], r[1], r[2] if r[1] >= THRESHOLD else None) for r in rows]
    print_table("After Python suppress()", ["team", "n", "avg_q01"], suppress(rows))
    print_panel("Risk", "A second dashboard that skips suppress() leaks Team B's data.")


def approach_2_database_view(conn):
    """SQL view with built-in suppression."""
    print_panel("Approach 2: Database View",
                "Suppression in the view definition. Automatic for all consumers.")
    view_sql = f"""CREATE OR REPLACE VIEW team_scores_safe AS
SELECT team, COUNT(*) AS n,
       CASE WHEN COUNT(*) >= {THRESHOLD} THEN ROUND(AVG(q01), 2) END AS avg_q01
FROM responses GROUP BY team"""
    print_sql(view_sql)
    conn.execute(view_sql)
    query = "SELECT * FROM team_scores_safe ORDER BY team"
    print_sql(query)
    rows = conn.execute(query).fetchall()
    print_table("Query the View (suppression automatic)", ["team", "n", "avg_q01"], rows)
    print_panel("Advantage", "One place, one rule. Threshold change updates all consumers.")


def approach_3_transformation_sql(conn):
    """Standalone SQL showing a dbt model pattern."""
    print_panel("Approach 3: Transformation-Level (dbt Pattern)",
                "Suppression baked into pipeline output. Raw data not exposed downstream.")
    transform_sql = f"""CREATE OR REPLACE TABLE team_scores_transformed AS
SELECT team, COUNT(*) AS n,
       CASE WHEN COUNT(*) >= {THRESHOLD} THEN ROUND(AVG(q01), 2) END AS avg_q01,
       COUNT(*) < {THRESHOLD} AS is_suppressed
FROM responses GROUP BY team"""
    print_sql(transform_sql)
    conn.execute(transform_sql)
    rows = conn.execute("SELECT * FROM team_scores_transformed ORDER BY team").fetchall()
    print_table("Materialized Table (pre-suppressed)", ["team", "n", "avg_q01", "suppressed"], rows)

    macro_sql = """-- dbt macro: macros/suppress_below.sql
-- {% macro suppress_below(column, threshold=4) %}
--     CASE WHEN COUNT(*) >= {{ threshold }} THEN {{ column }} END
-- {% endmacro %}
--
-- Usage: {{ suppress_below('ROUND(AVG(q01), 2)') }} AS avg_q01"""
    print_sql(macro_sql)
    print_panel("Advantage", "Version-controlled, CI-tested, consistent via reusable macro.")


def main():
    with duckdb_conn() as conn:
        create_data(conn)
        approach_1_application(conn)
        approach_2_database_view(conn)
        approach_3_transformation_sql(conn)

        print_table("Comparison: Where to Enforce Suppression",
            ["Property", "App-Level", "DB View", "Transform (dbt)"],
            [("Reliability",     "Low -- each consumer\nmust implement",
              "High -- automatic", "High -- baked in"),
             ("Bypass risk",     "High -- direct table\naccess skips it",
              "Medium -- base table\nstill queryable", "Low -- raw data\nnot exposed"),
             ("Auditability",    "Scattered in app code",
              "Single SQL def", "Version-controlled"),
             ("Threshold change","Update every consumer",
              "Update one view", "Update one macro"),
             ("Dynamic cuts",    "Flexible -- runtime",
              "Flexible -- GROUP BY", "Fixed at build time")])

        print_panel("Key Takeaway",
            "Suppression is a DATA INTEGRITY concern, not presentation.\n"
            "Enforce as close to the data as possible: views or transforms.")


if __name__ == "__main__":
    main()
