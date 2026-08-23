"""Lab 01: Pre-Computed Rollup vs Runtime Aggregation

Generates a large survey dataset (100K+ rows) and compares:
  (a) Runtime aggregation — compute AVG on every query
  (b) Pre-computed rollup table — one-time cost, instant reads

DuckDB is fast enough to mask the gap at small scale. At 100K+ rows with
multi-dimension joins, the difference starts showing — and it grows
dramatically with data size and query complexity.
"""

import sys, os, time, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

QUESTIONS = [
    ("Q01", "Basic Needs", 4.1), ("Q02", "Basic Needs", 3.9),
    ("Q03", "Individual", 3.7),  ("Q04", "Individual", 3.4),
    ("Q05", "Individual", 3.8),  ("Q06", "Individual", 3.5),
    ("Q07", "Teamwork", 3.6),    ("Q08", "Teamwork", 3.7),
    ("Q09", "Teamwork", 3.8),    ("Q10", "Teamwork", 3.0),
    ("Q11", "Growth", 3.3),      ("Q12", "Growth", 3.5),
]

TEAMS = ["Team_" + str(i).zfill(2) for i in range(1, 21)]       # 20 teams
DEPARTMENTS = ["Engineering", "Product", "Sales", "Marketing", "Support"]
LOCATIONS = ["New York", "Austin", "Chicago", "London", "Berlin"]
PERIODS = [f"2024-Q{q}" for q in range(1, 5)] + [f"2025-Q{q}" for q in range(1, 5)]


def generate_data(conn):
    """Generate ~115K fact rows using DuckDB's generate_series + cross join."""

    # Dimensions
    conn.execute("CREATE TABLE dim_team (team_key INT, team_name VARCHAR, department VARCHAR, location VARCHAR)")
    random.seed(42)
    for i, t in enumerate(TEAMS, 1):
        conn.execute("INSERT INTO dim_team VALUES (?,?,?,?)",
                     [i, t, DEPARTMENTS[i % len(DEPARTMENTS)], LOCATIONS[i % len(LOCATIONS)]])

    conn.execute("CREATE TABLE dim_question (question_key INT, question_id VARCHAR, category VARCHAR, base_score FLOAT)")
    for i, (qid, cat, base) in enumerate(QUESTIONS, 1):
        conn.execute("INSERT INTO dim_question VALUES (?,?,?,?)", [i, qid, cat, base])

    conn.execute("CREATE TABLE dim_period (period_key INT, period_label VARCHAR)")
    for i, p in enumerate(PERIODS, 1):
        conn.execute("INSERT INTO dim_period VALUES (?,?)", [i, p])

    # Fact table: cross join dimensions, 60 respondents per combo, randomized scores
    conn.execute("""
        CREATE TABLE fact_responses AS
        SELECT ROW_NUMBER() OVER () AS response_id,
               t.team_key, q.question_key, p.period_key,
               GREATEST(1.0, LEAST(5.0,
                   ROUND(q.base_score + (RANDOM() - 0.5) * 1.2, 1)
               )) AS score
        FROM dim_team t
        CROSS JOIN dim_question q
        CROSS JOIN dim_period p
        CROSS JOIN generate_series(1, 60) AS respondent(n)
    """)

    count = conn.execute("SELECT COUNT(*) FROM fact_responses").fetchone()[0]
    return count


def benchmark(conn, label, sql, runs=5):
    """Run a query multiple times, return (mean_ms, rows, result_sample)."""
    result = None
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        result = conn.execute(sql).fetchall()
        times.append((time.perf_counter() - start) * 1000)
    mean_ms = sum(times) / len(times)
    return mean_ms, len(result), result[:5]


def main():
    with duckdb_conn() as conn:
        count = generate_data(conn)
        print_panel("Lab 01: Pre-Computed Rollup vs Runtime",
                    f"Generated {count:,} fact rows.\n"
                    f"20 teams x 12 questions x 8 periods x 60 respondents.\n"
                    f"Compare runtime aggregation vs pre-computed rollup table.")

        # ── Runtime aggregation ──────────────────────────────────────────
        runtime_sql = """\
SELECT dt.team_name, dt.department, dq.category, dp.period_label,
       ROUND(AVG(f.score), 2) AS mean_score, COUNT(*) AS n
FROM fact_responses f
JOIN dim_team dt     ON f.team_key = dt.team_key
JOIN dim_question dq ON f.question_key = dq.question_key
JOIN dim_period dp   ON f.period_key = dp.period_key
GROUP BY dt.team_name, dt.department, dq.category, dp.period_label
ORDER BY dt.team_name, dp.period_label, dq.category;"""

        print_panel("STEP 1", "Runtime aggregation — compute every time")
        print_sql(runtime_sql)
        rt_ms, rt_rows, rt_sample = benchmark(conn, "runtime", runtime_sql)
        print_table("Runtime Result (first 5 rows)",
                    ["Team", "Dept", "Category", "Period", "Mean", "N"], rt_sample)

        # ── Build rollup table ───────────────────────────────────────────
        rollup_sql = """\
CREATE TABLE rollup_team_category_period AS
SELECT dt.team_name, dt.department, dq.category, dp.period_label,
       SUM(f.score) AS sum_score, COUNT(*) AS n
FROM fact_responses f
JOIN dim_team dt     ON f.team_key = dt.team_key
JOIN dim_question dq ON f.question_key = dq.question_key
JOIN dim_period dp   ON f.period_key = dp.period_key
GROUP BY dt.team_name, dt.department, dq.category, dp.period_label;"""

        print_panel("STEP 2", "Build pre-computed rollup table — stores SUM + COUNT, not AVG.\n"
                    "You can't average averages, but you can always derive AVG from SUM/COUNT.")
        print_sql(rollup_sql)
        build_start = time.perf_counter()
        conn.execute(rollup_sql)
        build_ms = (time.perf_counter() - build_start) * 1000

        # ── Query the rollup ────────────────────────────────────────────
        rollup_query = """\
SELECT team_name, department, category, period_label,
       ROUND(sum_score * 1.0 / n, 2) AS mean_score, n
FROM rollup_team_category_period
ORDER BY team_name, period_label, category;"""

        print_panel("STEP 3", "Query pre-computed rollup — just a table scan, no joins")
        print_sql(rollup_query)
        ro_ms, ro_rows, ro_sample = benchmark(conn, "rollup", rollup_query)
        print_table("Rollup Result (first 5 rows)",
                    ["Team", "Dept", "Category", "Period", "Mean", "N"], ro_sample)

        # ── Comparison ──────────────────────────────────────────────────
        rollup_size = conn.execute(
            "SELECT COUNT(*) FROM rollup_team_category_period"
        ).fetchone()[0]

        print_table("Timing Comparison", ["Approach", "Avg Query (ms)", "Result Rows"], [
            ("Runtime aggregation", f"{rt_ms:.2f}", rt_rows),
            ("Pre-computed rollup", f"{ro_ms:.2f}", ro_rows),
            ("Rollup build cost",   f"{build_ms:.2f}", f"{rollup_size} rows stored"),
        ])

        speedup = rt_ms / ro_ms if ro_ms > 0 else float("inf")
        print_panel("Key Takeaway",
                    f"Rollup query is {speedup:.1f}x faster than runtime aggregation.\n\n"
                    f"DuckDB is already very fast — at 100K rows the gap may be small.\n"
                    f"The gap grows dramatically with:\n"
                    f"  - Data size (10M+ rows)\n"
                    f"  - Query complexity (more joins, more dimensions)\n"
                    f"  - Concurrent users (100 dashboards hitting the same query)\n\n"
                    f"The tradeoff: the rollup table is STALE the moment new data arrives.\n"
                    f"You pay storage ({rollup_size} rows) and must rebuild after data changes.")


if __name__ == "__main__":
    main()
