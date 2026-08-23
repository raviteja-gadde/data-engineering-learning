"""Lab 03: Materialized Views — Pre-Computing Results

Demonstrates the materialized view pattern using DuckDB's CREATE TABLE AS.
Shows pre-computed results, staleness after new data, and rebuild.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

QUESTIONS = [
    (1, "Q01", "I know what is expected of me at work", "Basic Needs"),
    (2, "Q02", "I have the materials and equipment I need", "Basic Needs"),
    (3, "Q03", "I have the opportunity to do what I do best every day", "Individual"),
    (4, "Q04", "I have received recognition for doing good work", "Individual"),
]

TEAMS = [(1, "Team Alpha", "Engineering"), (2, "Team Beta", "Engineering"), (3, "Team Gamma", "Product")]


def setup(conn):
    conn.execute("CREATE TABLE dim_team (team_key INT PRIMARY KEY, team_name VARCHAR, department VARCHAR)")
    conn.execute("CREATE TABLE dim_question (question_key INT PRIMARY KEY, question_id VARCHAR, question_text VARCHAR, category VARCHAR)")
    conn.execute("""
        CREATE TABLE fact_survey_responses (
            response_id INT PRIMARY KEY, team_key INT, question_key INT,
            score DECIMAL(2,1), response_count INT DEFAULT 1
        )
    """)
    for t in TEAMS:
        conn.execute("INSERT INTO dim_team VALUES (?,?,?)", list(t))
    for q in QUESTIONS:
        conn.execute("INSERT INTO dim_question VALUES (?,?,?,?)", list(q))

    # Initial batch of responses
    import random
    random.seed(42)
    adjs = {1: 0.3, 2: 0.0, 3: -0.2}
    bases = {1: 4.1, 2: 3.9, 3: 3.7, 4: 3.4}
    resp_id = 0
    for tk in [1, 2, 3]:
        for qk in [1, 2, 3, 4]:
            for _ in range(5):
                resp_id += 1
                score = max(1.0, min(5.0, round(bases[qk] + adjs[tk] + random.gauss(0, 0.3), 1)))
                conn.execute("INSERT INTO fact_survey_responses VALUES (?,?,?,?,1)", [resp_id, tk, qk, score])


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 03: Materialized Views",
                    "Pre-compute query results for fast reads.\n"
                    "Trade-off: speed vs staleness.")

        setup(conn)

        # ── Step 1: Create the materialized view ──
        mv_sql = """\
CREATE TABLE mv_team_category_scores AS
SELECT dt.team_name,
       dq.category,
       ROUND(AVG(f.score), 2)  AS mean_score,
       COUNT(*)                 AS num_responses
FROM fact_survey_responses f
JOIN dim_team dt     ON f.team_key = dt.team_key
JOIN dim_question dq ON f.question_key = dq.question_key
GROUP BY dt.team_name, dq.category;"""

        print_panel("STEP 1", "Create materialized view (CREATE TABLE AS SELECT)")
        print_sql(mv_sql)
        conn.execute(mv_sql)

        rows = conn.execute("SELECT * FROM mv_team_category_scores ORDER BY team_name, category").fetchall()
        print_table("Materialized View — Initial", ["Team", "Category", "Mean Score", "Responses"], rows)

        # ── Step 2: New data arrives ──
        print_panel("STEP 2", "New survey responses arrive — 5 very high scores for Team Gamma")
        import random
        random.seed(99)
        max_id = conn.execute("SELECT MAX(response_id) FROM fact_survey_responses").fetchone()[0]
        for i in range(5):
            conn.execute("INSERT INTO fact_survey_responses VALUES (?,?,?,?,1)",
                         [max_id + i + 1, 3, 1, 4.8])  # Team Gamma, Q01, high score

        # Show the MV is stale
        stale_rows = conn.execute("SELECT * FROM mv_team_category_scores WHERE team_name = 'Team Gamma' ORDER BY category").fetchall()
        fresh_sql = """\
SELECT dt.team_name, dq.category,
       ROUND(AVG(f.score), 2) AS mean_score, COUNT(*) AS num_responses
FROM fact_survey_responses f
JOIN dim_team dt ON f.team_key = dt.team_key
JOIN dim_question dq ON f.question_key = dq.question_key
WHERE dt.team_name = 'Team Gamma'
GROUP BY dt.team_name, dq.category ORDER BY category;"""
        fresh_rows = conn.execute(fresh_sql).fetchall()

        print_table("Materialized View (STALE)", ["Team", "Category", "Mean Score", "Responses"], stale_rows)
        print_table("Live Query (FRESH)", ["Team", "Category", "Mean Score", "Responses"], fresh_rows)
        print_panel("Staleness", "The materialized view still shows the OLD result.\n"
                    "Team Gamma's Basic Needs score hasn't changed in the MV,\n"
                    "but the live query shows the new high scores pulled it up.\n"
                    "The MV also shows fewer responses than the live query.")

        # ── Step 3: Rebuild ──
        print_panel("STEP 3", "Rebuild the materialized view to include new data")
        rebuild_sql = """\
-- Full rebuild: drop and recreate
DROP TABLE mv_team_category_scores;

CREATE TABLE mv_team_category_scores AS
SELECT dt.team_name, dq.category,
       ROUND(AVG(f.score), 2) AS mean_score,
       COUNT(*) AS num_responses
FROM fact_survey_responses f
JOIN dim_team dt ON f.team_key = dt.team_key
JOIN dim_question dq ON f.question_key = dq.question_key
GROUP BY dt.team_name, dq.category;"""
        print_sql(rebuild_sql)
        conn.execute("DROP TABLE mv_team_category_scores")
        conn.execute("""
            CREATE TABLE mv_team_category_scores AS
            SELECT dt.team_name, dq.category,
                   ROUND(AVG(f.score), 2) AS mean_score,
                   COUNT(*) AS num_responses
            FROM fact_survey_responses f
            JOIN dim_team dt ON f.team_key = dt.team_key
            JOIN dim_question dq ON f.question_key = dq.question_key
            GROUP BY dt.team_name, dq.category
        """)
        rebuilt = conn.execute("SELECT * FROM mv_team_category_scores WHERE team_name = 'Team Gamma' ORDER BY category").fetchall()
        print_table("Materialized View (REBUILT)", ["Team", "Category", "Mean Score", "Responses"], rebuilt)

        print_panel("Refresh Strategies",
                    "1. FULL REBUILD — Drop + recreate. Simple, correct, but slow for huge tables.\n"
                    "2. INCREMENTAL — Only process new rows. Fast, but complex to implement correctly.\n"
                    "3. SCHEDULED — Rebuild on a cron (nightly, hourly). Accept a known staleness window.\n\n"
                    "For survey data: surveys arrive in batches after a survey closes.\n"
                    "Full rebuild after each batch is usually the right strategy.")


if __name__ == "__main__":
    main()
