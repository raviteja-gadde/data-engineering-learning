"""Lab 02: Snowflake Schema — Normalizing Dimensions Further

Extends the star schema by breaking dim_team into three normalized tables:
dim_team, dim_department, dim_location. Shows the same queries require
more joins, and compares the SQL side by side.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_comparison, print_panel, print_sql, print_table
from shared.duck import duckdb_conn

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

TEAMS = [
    (1, "Team Alpha",   1, 1,  0.3),
    (2, "Team Beta",    1, 2,  0.0),
    (3, "Team Gamma",   2, 1, -0.2),
    (4, "Team Delta",   3, 3,  0.1),
    (5, "Team Epsilon", 3, 2, -0.4),
]
DEPARTMENTS = [(1, "Engineering", "Technology"), (2, "Product", "Technology"), (3, "Sales", "Revenue")]
LOCATIONS = [(1, "New York", "Northeast"), (2, "Austin", "South"), (3, "Chicago", "Midwest")]
PERIODS = [(1, "2025-Q1", 2025, 1), (2, "2025-Q2", 2025, 2)]


def clamp(val):
    return max(1.0, min(5.0, round(val, 1)))


def build_snowflake(conn):
    conn.execute("CREATE TABLE dim_department (department_key INT PRIMARY KEY, department_name VARCHAR, division VARCHAR)")
    conn.execute("CREATE TABLE dim_location (location_key INT PRIMARY KEY, city VARCHAR, region VARCHAR)")
    conn.execute("CREATE TABLE dim_team (team_key INT PRIMARY KEY, team_name VARCHAR, department_key INT, location_key INT)")
    conn.execute("CREATE TABLE dim_question (question_key INT PRIMARY KEY, question_id VARCHAR, question_text VARCHAR, category VARCHAR)")
    conn.execute("CREATE TABLE dim_time_period (time_period_key INT PRIMARY KEY, period_label VARCHAR, year INT, quarter INT)")
    conn.execute("""
        CREATE TABLE fact_survey_responses (
            response_id INT PRIMARY KEY, team_key INT, question_key INT,
            time_period_key INT, score DECIMAL(2,1), response_count INT DEFAULT 1
        )
    """)


def build_star_schema(conn):
    """Build a parallel star schema for comparison (schema only)."""
    conn.execute("""
        CREATE TABLE star_dim_team (
            team_key INT PRIMARY KEY, team_name VARCHAR,
            department VARCHAR, division VARCHAR,
            city VARCHAR, region VARCHAR
        )
    """)


def copy_star_facts(conn):
    """Copy fact data into the star schema after loading."""
    conn.execute("CREATE TABLE star_fact AS SELECT * FROM fact_survey_responses")


def load_data(conn):
    import random
    random.seed(42)

    for dk, dn, div in DEPARTMENTS:
        conn.execute("INSERT INTO dim_department VALUES (?,?,?)", [dk, dn, div])
    for lk, city, region in LOCATIONS:
        conn.execute("INSERT INTO dim_location VALUES (?,?,?)", [lk, city, region])
    for tk, tn, dk, lk, _ in TEAMS:
        conn.execute("INSERT INTO dim_team VALUES (?,?,?,?)", [tk, tn, dk, lk])
    for i, (qid, text, cat, _) in enumerate(QUESTIONS, 1):
        conn.execute("INSERT INTO dim_question VALUES (?,?,?,?)", [i, qid, text, cat])
    for tpk, label, year, q in PERIODS:
        conn.execute("INSERT INTO dim_time_period VALUES (?,?,?,?)", [tpk, label, year, q])

    # Star dim (denormalized)
    for tk, tn, dk, lk, _ in TEAMS:
        dept = next(d for d in DEPARTMENTS if d[0] == dk)
        loc = next(l for l in LOCATIONS if l[0] == lk)
        conn.execute("INSERT INTO star_dim_team VALUES (?,?,?,?,?,?)",
                     [tk, tn, dept[1], dept[2], loc[1], loc[2]])

    resp_id = 0
    for tk, _, _, _, adj in TEAMS:
        for tpk, _, _, _ in PERIODS:
            for qi, (_, _, _, base) in enumerate(QUESTIONS, 1):
                for _ in range(3):
                    resp_id += 1
                    score = clamp(base + adj + random.gauss(0, 0.3))
                    conn.execute("INSERT INTO fact_survey_responses VALUES (?,?,?,?,?,1)",
                                 [resp_id, tk, qi, tpk, score])


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 02: Snowflake Schema",
                    "Normalizing dim_team into dim_team + dim_department + dim_location.\n"
                    "Same data, more joins. Compare the SQL.")

        build_snowflake(conn)
        build_star_schema(conn)
        load_data(conn)
        copy_star_facts(conn)

        # ── Query: Mean score by department ──
        star_sql = """\
-- STAR SCHEMA: 1 join to get department
SELECT sdt.department,
       ROUND(AVG(f.score), 2) AS mean_score
FROM star_fact f
JOIN star_dim_team sdt ON f.team_key = sdt.team_key
GROUP BY sdt.department
ORDER BY mean_score DESC;"""

        snow_sql = """\
-- SNOWFLAKE SCHEMA: 2 joins to get department
SELECT dd.department_name,
       ROUND(AVG(f.score), 2) AS mean_score
FROM fact_survey_responses f
JOIN dim_team dt       ON f.team_key = dt.team_key
JOIN dim_department dd ON dt.department_key = dd.department_key
GROUP BY dd.department_name
ORDER BY mean_score DESC;"""

        print_panel("QUERY", "Mean engagement score by department")
        print_comparison("Star vs Snowflake SQL", star_sql, snow_sql)

        star_rows = conn.execute(star_sql).fetchall()
        snow_rows = conn.execute(snow_sql).fetchall()
        print_table("Star Schema Result", ["Department", "Mean Score"], star_rows)
        print_table("Snowflake Schema Result", ["Department", "Mean Score"], snow_rows)

        # ── Query: Mean score by region (deeper normalization) ──
        star_region = """\
-- STAR: region is already on the team dimension
SELECT sdt.region,
       ROUND(AVG(f.score), 2) AS mean_score
FROM star_fact f
JOIN star_dim_team sdt ON f.team_key = sdt.team_key
GROUP BY sdt.region ORDER BY mean_score DESC;"""

        snow_region = """\
-- SNOWFLAKE: must join through dim_team -> dim_location
SELECT dl.region,
       ROUND(AVG(f.score), 2) AS mean_score
FROM fact_survey_responses f
JOIN dim_team dt     ON f.team_key = dt.team_key
JOIN dim_location dl ON dt.location_key = dl.location_key
GROUP BY dl.region ORDER BY mean_score DESC;"""

        print_panel("QUERY", "Mean score by region — snowflake adds another join hop")
        print_comparison("Star vs Snowflake SQL", star_region, snow_region)

        star_rows = conn.execute(star_region).fetchall()
        snow_rows = conn.execute(snow_region).fetchall()
        print_table("Star Result", ["Region", "Mean Score"], star_rows)
        print_table("Snowflake Result", ["Region", "Mean Score"], snow_rows)

        # ── Division rollup (only possible cleanly in snowflake) ──
        div_sql = """\
SELECT dd.division,
       ROUND(AVG(f.score), 2) AS mean_score,
       COUNT(*) AS responses
FROM fact_survey_responses f
JOIN dim_team dt       ON f.team_key = dt.team_key
JOIN dim_department dd ON dt.department_key = dd.department_key
GROUP BY dd.division ORDER BY mean_score DESC;"""

        print_panel("SNOWFLAKE ADVANTAGE", "Division rollup — department's parent in normalized hierarchy")
        print_sql(div_sql)
        rows = conn.execute(div_sql).fetchall()
        print_table("Division Rollup", ["Division", "Mean Score", "Responses"], rows)

        print_panel("Key Takeaway",
                    "Snowflake schema normalizes dimensions into sub-tables.\n"
                    "Same results, more joins. Star is simpler for most queries.\n"
                    "Snowflake shines when dimensions have deep hierarchies\n"
                    "(team -> department -> division) that change independently.")


if __name__ == "__main__":
    main()
