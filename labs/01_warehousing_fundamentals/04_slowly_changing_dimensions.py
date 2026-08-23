"""Lab 04: Slowly Changing Dimensions — Handling Teams That Restructure

Models a mid-year team restructure using SCD Type 2 (valid_from/valid_to).
Two employees move from Engineering to Product. Shows how the same data
answers "results under old structure" vs "results under current structure."
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 04: Slowly Changing Dimensions",
                    "Team Alpha restructures mid-year: 2 members move to Product.\n"
                    "SCD Type 2 preserves both versions so we can query either.")

        # ── Schema with SCD Type 2 fields ──
        conn.execute("""
            CREATE TABLE dim_team_scd (
                team_key     INTEGER PRIMARY KEY,
                team_code    VARCHAR,        -- natural key (stable across versions)
                team_name    VARCHAR,
                department   VARCHAR,
                headcount    INTEGER,
                valid_from   DATE,
                valid_to     DATE,
                is_current   BOOLEAN
            )
        """)
        conn.execute("""
            CREATE TABLE dim_question (
                question_key INT PRIMARY KEY,
                question_id VARCHAR, question_text VARCHAR, category VARCHAR
            )
        """)
        conn.execute("""
            CREATE TABLE fact_survey_responses (
                response_id INT PRIMARY KEY, team_key INT,
                question_key INT, response_date DATE,
                score DECIMAL(2,1)
            )
        """)

        # ── SCD Type 2 dimension data ──
        # Team Alpha: was in Engineering (8 people), then moved to Product (6 people — 2 left)
        # Team Beta: stable, no changes
        print_panel("DIMENSION DATA", "Team Alpha has TWO rows — old version and current version")
        scd_sql = """\
-- Team Alpha v1: Engineering, 8 people, active Jan-Jun 2025
-- Team Alpha v2: Product, 6 people, active Jul 2025 onward
-- Team Beta: no change, one row
INSERT INTO dim_team_scd VALUES
  (1, 'ALPHA', 'Team Alpha', 'Engineering', 8,  '2025-01-01', '2025-06-30', false),
  (2, 'ALPHA', 'Team Alpha', 'Product',     6,  '2025-07-01', '9999-12-31', true),
  (3, 'BETA',  'Team Beta',  'Engineering', 10, '2025-01-01', '9999-12-31', true);"""
        print_sql(scd_sql)
        conn.execute("""
            INSERT INTO dim_team_scd VALUES
                (1, 'ALPHA', 'Team Alpha', 'Engineering', 8,  '2025-01-01', '2025-06-30', false),
                (2, 'ALPHA', 'Team Alpha', 'Product',     6,  '2025-07-01', '9999-12-31', true),
                (3, 'BETA',  'Team Beta',  'Engineering', 10, '2025-01-01', '9999-12-31', true)
        """)

        rows = conn.execute("SELECT * FROM dim_team_scd ORDER BY team_key").fetchall()
        print_table("dim_team_scd",
                    ["team_key", "team_code", "team_name", "department", "headcount",
                     "valid_from", "valid_to", "is_current"], rows)

        # ── Questions (subset) ──
        conn.execute("""
            INSERT INTO dim_question VALUES
                (1, 'Q01', 'I know what is expected of me at work', 'Basic Needs'),
                (2, 'Q07', 'At work, my opinions seem to count', 'Teamwork'),
                (3, 'Q12', 'I have had opportunities to learn and grow', 'Growth')
        """)

        # ── Fact data: responses in Q1 (old structure) and Q3 (new structure) ──
        import random
        random.seed(42)
        resp_id = 0
        # Q1 responses: Team Alpha (team_key=1, Engineering era)
        for qk in [1, 2, 3]:
            for _ in range(8):
                resp_id += 1
                conn.execute("INSERT INTO fact_survey_responses VALUES (?,?,?,?,?)",
                             [resp_id, 1, qk, '2025-03-15', max(1, min(5, round(3.8 + random.gauss(0, 0.4), 1)))])
        # Q1 responses: Team Beta (team_key=3)
        for qk in [1, 2, 3]:
            for _ in range(10):
                resp_id += 1
                conn.execute("INSERT INTO fact_survey_responses VALUES (?,?,?,?,?)",
                             [resp_id, 3, qk, '2025-03-15', max(1, min(5, round(3.5 + random.gauss(0, 0.4), 1)))])
        # Q3 responses: Team Alpha (team_key=2, Product era — higher scores post-move)
        for qk in [1, 2, 3]:
            for _ in range(6):
                resp_id += 1
                conn.execute("INSERT INTO fact_survey_responses VALUES (?,?,?,?,?)",
                             [resp_id, 2, qk, '2025-09-15', max(1, min(5, round(4.2 + random.gauss(0, 0.3), 1)))])
        # Q3 responses: Team Beta (team_key=3)
        for qk in [1, 2, 3]:
            for _ in range(10):
                resp_id += 1
                conn.execute("INSERT INTO fact_survey_responses VALUES (?,?,?,?,?)",
                             [resp_id, 3, qk, '2025-09-15', max(1, min(5, round(3.6 + random.gauss(0, 0.4), 1)))])

        # ── Query 1: Results under OLD structure ──
        q1_sql = """\
-- "What was Team Alpha's score when they were in Engineering?"
-- Join to the historical version (team_key=1, valid_from='2025-01-01')
SELECT dt.team_name,
       dt.department,
       dt.headcount,
       dt.valid_from || ' to ' || dt.valid_to AS active_period,
       ROUND(AVG(f.score), 2) AS mean_score,
       COUNT(*) AS responses
FROM fact_survey_responses f
JOIN dim_team_scd dt ON f.team_key = dt.team_key
WHERE dt.team_key = 1   -- the Engineering-era version
GROUP BY dt.team_name, dt.department, dt.headcount, dt.valid_from, dt.valid_to;"""

        print_panel("QUERY 1", "Results under the OLD structure (Engineering era)")
        print_sql(q1_sql)
        rows = conn.execute(q1_sql).fetchall()
        print_table("Old Structure", ["Team", "Department", "Headcount", "Active Period", "Mean Score", "Responses"], rows)

        # ── Query 2: Results under CURRENT structure ──
        q2_sql = """\
-- "What is Team Alpha's score now that they're in Product?"
SELECT dt.team_name,
       dt.department,
       dt.headcount,
       dt.valid_from || ' to ' || dt.valid_to AS active_period,
       ROUND(AVG(f.score), 2) AS mean_score,
       COUNT(*) AS responses
FROM fact_survey_responses f
JOIN dim_team_scd dt ON f.team_key = dt.team_key
WHERE dt.team_key = 2   -- the Product-era version
GROUP BY dt.team_name, dt.department, dt.headcount, dt.valid_from, dt.valid_to;"""

        print_panel("QUERY 2", "Results under the CURRENT structure (Product era)")
        print_sql(q2_sql)
        rows = conn.execute(q2_sql).fetchall()
        print_table("Current Structure", ["Team", "Department", "Headcount", "Active Period", "Mean Score", "Responses"], rows)

        # ── Query 3: Full timeline ──
        q3_sql = """\
-- Full timeline: all versions of all teams, ordered by time
SELECT dt.team_name,
       dt.department,
       dt.valid_from,
       CASE WHEN dt.is_current THEN 'CURRENT' ELSE 'HISTORICAL' END AS status,
       ROUND(AVG(f.score), 2) AS mean_score,
       COUNT(*) AS responses
FROM fact_survey_responses f
JOIN dim_team_scd dt ON f.team_key = dt.team_key
GROUP BY dt.team_name, dt.department, dt.valid_from, dt.is_current
ORDER BY dt.team_name, dt.valid_from;"""

        print_panel("QUERY 3", "Full timeline — every version of every team")
        print_sql(q3_sql)
        rows = conn.execute(q3_sql).fetchall()
        print_table("Team Timeline", ["Team", "Department", "Valid From", "Status", "Mean Score", "Responses"], rows)

        # ── SCD Type comparison ──
        print_panel("SCD Types Compared",
                    "TYPE 1 — Overwrite: update the row, lose history.\n"
                    "  Use for: typo fixes, non-meaningful changes.\n\n"
                    "TYPE 2 — New row with valid_from/valid_to: preserve full history.\n"
                    "  Use for: restructures, re-orgs, anything where historical\n"
                    "  context matters. This is what we demonstrated above.\n\n"
                    "TYPE 3 — Add previous_value column: keep one level of history.\n"
                    "  Use for: rare cases where you only need the immediately prior value.\n\n"
                    "For survey analytics, Type 2 is essential. Restructures happen\n"
                    "every year, and stakeholders always ask 'what were the scores\n"
                    "under the old structure?'")


if __name__ == "__main__":
    main()
