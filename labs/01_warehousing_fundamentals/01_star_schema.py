"""Lab 01: Star Schema — The Core Warehouse Pattern

Builds a star schema for employee engagement survey data using a 12-item
engagement survey. Demonstrates fact + dimension tables, analytical queries, and
query plans.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_sql, print_table
from shared.duck import duckdb_conn

# ── 12-item engagement questions with categories and realistic base scores ───
QUESTIONS = [
    ("Q01", "I know what is expected of me at work", "Basic Needs", 4.1),
    ("Q02", "I have the materials and equipment I need", "Basic Needs", 3.9),
    ("Q03", "I have the opportunity to do what I do best every day", "Individual", 3.7),
    ("Q04", "I have received recognition for doing good work", "Individual", 3.4),
    ("Q05", "My supervisor seems to care about me as a person", "Individual", 3.8),
    ("Q06", "Someone at work encourages my development", "Individual", 3.5),
    ("Q07", "At work, my opinions seem to count", "Teamwork", 3.6),
    ("Q08", "The mission makes me feel my job is important", "Teamwork", 3.7),
    ("Q09", "My associates are committed to quality work", "Teamwork", 3.8),
    ("Q10", "I have a best friend at work", "Teamwork", 3.0),
    ("Q11", "Someone has talked to me about my progress", "Growth", 3.3),
    ("Q12", "I have had opportunities to learn and grow", "Growth", 3.5),
]

# Team-specific score adjustments (some teams are clearly stronger/weaker)
TEAMS = {
    "Team Alpha":   {"dept": "Engineering", "loc": "New York",  "adj":  0.3},
    "Team Beta":    {"dept": "Engineering", "loc": "Austin",    "adj":  0.0},
    "Team Gamma":   {"dept": "Product",     "loc": "New York",  "adj": -0.2},
    "Team Delta":   {"dept": "Sales",       "loc": "Chicago",   "adj":  0.1},
    "Team Epsilon": {"dept": "Sales",       "loc": "Austin",    "adj": -0.4},
}

PROJECTS = ["Project Phoenix", "Project Atlas", "Project Orbit"]
PERIODS = [("2025-Q1", 2025, 1), ("2025-Q2", 2025, 2)]


def clamp(val, lo=1.0, hi=5.0):
    return max(lo, min(hi, round(val, 1)))


def build_schema(conn):
    conn.execute("""
        CREATE TABLE dim_team (
            team_key    INTEGER PRIMARY KEY,
            team_name   VARCHAR,
            department  VARCHAR,
            location    VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE dim_project (
            project_key  INTEGER PRIMARY KEY,
            project_name VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE dim_question (
            question_key INTEGER PRIMARY KEY,
            question_id  VARCHAR,
            question_text VARCHAR,
            category     VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE dim_time_period (
            time_period_key INTEGER PRIMARY KEY,
            period_label    VARCHAR,
            year            INTEGER,
            quarter         INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE fact_survey_responses (
            response_id     INTEGER PRIMARY KEY,
            team_key        INTEGER REFERENCES dim_team,
            project_key     INTEGER REFERENCES dim_project,
            question_key    INTEGER REFERENCES dim_question,
            time_period_key INTEGER REFERENCES dim_time_period,
            score           DECIMAL(2,1),
            response_count  INTEGER DEFAULT 1
        )
    """)


def load_data(conn):
    # Dimensions
    for i, (name, info) in enumerate(TEAMS.items(), 1):
        conn.execute(
            "INSERT INTO dim_team VALUES (?, ?, ?, ?)",
            [i, name, info["dept"], info["loc"]],
        )
    for i, proj in enumerate(PROJECTS, 1):
        conn.execute("INSERT INTO dim_project VALUES (?, ?)", [i, proj])
    for i, (qid, text, cat, _) in enumerate(QUESTIONS, 1):
        conn.execute(
            "INSERT INTO dim_question VALUES (?, ?, ?, ?)", [i, qid, text, cat]
        )
    for i, (label, year, quarter) in enumerate(PERIODS, 1):
        conn.execute(
            "INSERT INTO dim_time_period VALUES (?, ?, ?, ?)",
            [i, label, year, quarter],
        )

    # Facts: 3 simulated respondents per team/project/period
    resp_id = 0
    import random
    random.seed(42)
    for team_idx, (team_name, info) in enumerate(TEAMS.items(), 1):
        for proj_idx in range(1, len(PROJECTS) + 1):
            for period_idx in range(1, len(PERIODS) + 1):
                for q_idx, (_, _, _, base) in enumerate(QUESTIONS, 1):
                    for _ in range(3):  # 3 respondents
                        resp_id += 1
                        score = clamp(base + info["adj"] + random.gauss(0, 0.3))
                        conn.execute(
                            "INSERT INTO fact_survey_responses VALUES (?,?,?,?,?,?,1)",
                            [resp_id, team_idx, proj_idx, q_idx, period_idx, score],
                        )


def run_queries(conn):
    # ── Query 1: Mean score by team ──
    sql1 = """
SELECT dt.team_name,
       dt.department,
       ROUND(AVG(f.score), 2) AS mean_score,
       COUNT(*) AS num_responses
FROM fact_survey_responses f
JOIN dim_team dt ON f.team_key = dt.team_key
GROUP BY dt.team_name, dt.department
ORDER BY mean_score DESC;
"""
    print_panel("QUERY 1", "Mean engagement score by team — who's thriving, who's struggling?")
    print_sql(sql1)
    rows = conn.execute(sql1).fetchall()
    print_table("Mean Score by Team", ["Team", "Department", "Mean Score", "Responses"], rows)

    # ── Query 2: Mean score by engagement category ──
    sql2 = """
SELECT dq.category,
       ROUND(AVG(f.score), 2) AS mean_score,
       COUNT(*) AS num_responses
FROM fact_survey_responses f
JOIN dim_question dq ON f.question_key = dq.question_key
GROUP BY dq.category
ORDER BY mean_score DESC;
"""
    print_panel("QUERY 2", "Mean score by engagement category — where is the org strongest?")
    print_sql(sql2)
    rows = conn.execute(sql2).fetchall()
    print_table("Mean Score by Category", ["Category", "Mean Score", "Responses"], rows)

    # ── Query 3: Rollup — department-level aggregation ──
    sql3 = """
SELECT dt.department,
       dq.category,
       ROUND(AVG(f.score), 2) AS mean_score
FROM fact_survey_responses f
JOIN dim_team dt     ON f.team_key = dt.team_key
JOIN dim_question dq ON f.question_key = dq.question_key
GROUP BY dt.department, dq.category
ORDER BY dt.department, dq.category;
"""
    print_panel("QUERY 3", "Department x Category matrix — same fact table, different slicing")
    print_sql(sql3)
    rows = conn.execute(sql3).fetchall()
    print_table("Department x Category", ["Department", "Category", "Mean Score"], rows)

    # ── Query Plan ──
    print_panel("QUERY PLAN", "EXPLAIN shows how DuckDB executes the star-join query")
    plan_sql = """
EXPLAIN SELECT dt.team_name, ROUND(AVG(f.score), 2) AS mean_score
FROM fact_survey_responses f
JOIN dim_team dt ON f.team_key = dt.team_key
GROUP BY dt.team_name;
"""
    print_sql(plan_sql)
    plan = conn.execute(plan_sql).fetchall()
    plan_text = "\n".join(str(row[1]) for row in plan)
    print_panel("Execution Plan", plan_text)


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 01: Star Schema",
                    "Building a star schema for 12-item engagement survey data.\n"
                    "5 teams, 3 projects, 12 questions, 2 time periods.\n"
                    "Each combination has 3 simulated respondents.")
        build_schema(conn)
        load_data(conn)
        run_queries(conn)

        print_panel("Key Takeaway",
                    "The star schema pattern: one central fact table joined to "
                    "dimension tables.\nEvery analytical question becomes: "
                    "JOIN the dimensions you need, filter, GROUP BY, aggregate.\n"
                    "The same fact table answers all three queries above — only "
                    "the joins and groupings change.")


if __name__ == "__main__":
    main()
