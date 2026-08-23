"""Lab 03: The Serving Layer Pattern

Complete pipeline: compute Gold aggregates in DuckDB (OLAP) → write to
PostgreSQL serving table → query PostgreSQL by team_id (point lookup).

This is how analytics SaaS products achieve sub-100ms response times:
  1. Heavy computation in an analytical engine (DuckDB/BigQuery/Snowflake)
  2. Push pre-computed results to a fast transactional store (PostgreSQL/Redis)
  3. API serves from the transactional store — simple key lookups

Measures latency at each stage to make the pattern concrete.
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.pg import pg_conn
from shared.display import print_table, print_panel

QUESTIONS = [
    ("Q01", "Basic Needs", 4.1), ("Q02", "Basic Needs", 3.9),
    ("Q03", "Individual", 3.7),  ("Q04", "Individual", 3.4),
    ("Q05", "Individual", 3.8),  ("Q06", "Individual", 3.5),
    ("Q07", "Teamwork", 3.6),    ("Q08", "Teamwork", 3.7),
    ("Q09", "Teamwork", 3.8),    ("Q10", "Teamwork", 3.0),
    ("Q11", "Growth", 3.3),      ("Q12", "Growth", 3.5),
]

TEAMS = {f"Team_{str(i).zfill(2)}": ["Engineering", "Product", "Sales", "Marketing", "Support"][i % 5]
         for i in range(1, 21)}


def stage1_compute_in_duckdb(duck):
    """Generate data and compute Gold aggregates in DuckDB."""

    # Generate fact data
    duck.execute("CREATE TABLE dim_team (team_key INT, team_name VARCHAR, department VARCHAR)")
    for i, (name, dept) in enumerate(TEAMS.items(), 1):
        duck.execute("INSERT INTO dim_team VALUES (?,?,?)", [i, name, dept])

    duck.execute("CREATE TABLE dim_question (qkey INT, qid VARCHAR, category VARCHAR, base FLOAT)")
    for i, (qid, cat, base) in enumerate(QUESTIONS, 1):
        duck.execute("INSERT INTO dim_question VALUES (?,?,?,?)", [i, qid, cat, base])

    duck.execute("""
        CREATE TABLE fact_responses AS
        SELECT ROW_NUMBER() OVER () AS id,
               t.team_key, q.qkey AS question_key,
               GREATEST(1.0, LEAST(5.0, ROUND(q.base + (RANDOM()-0.5)*1.2, 1))) AS score
        FROM dim_team t CROSS JOIN dim_question q
        CROSS JOIN generate_series(1, 50) AS r(n)
    """)

    count = duck.execute("SELECT COUNT(*) FROM fact_responses").fetchone()[0]

    # Compute Gold-level aggregates: team x category
    duck.execute("""
        CREATE TABLE gold_team_category AS
        SELECT t.team_key, t.team_name, t.department,
               q.category,
               ROUND(AVG(f.score), 2)  AS mean_score,
               COUNT(*)                AS response_count
        FROM fact_responses f
        JOIN dim_team t     ON f.team_key = t.team_key
        JOIN dim_question q ON f.question_key = q.qkey
        GROUP BY t.team_key, t.team_name, t.department, q.category
    """)

    gold_rows = duck.execute("SELECT * FROM gold_team_category ORDER BY team_name, category").fetchall()
    return count, gold_rows


def stage2_push_to_postgres(gold_rows):
    """Write Gold aggregates to a PostgreSQL serving table."""
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()

        cur.execute("DROP TABLE IF EXISTS serving_team_category")
        cur.execute("""
            CREATE TABLE serving_team_category (
                team_key       INT,
                team_name      VARCHAR(50),
                department     VARCHAR(50),
                category       VARCHAR(30),
                mean_score     NUMERIC(4,2),
                response_count INT,
                PRIMARY KEY (team_key, category)
            )
        """)

        # Create index for fast point lookups
        cur.execute("CREATE INDEX idx_serving_team ON serving_team_category (team_key)")

        for row in gold_rows:
            cur.execute(
                "INSERT INTO serving_team_category VALUES (%s,%s,%s,%s,%s,%s)",
                row,
            )

        return len(gold_rows)


def stage3_serve_from_postgres(team_key=1, runs=20):
    """Point lookup from PostgreSQL — simulates what an API would do."""
    times = []
    result = None
    with pg_conn() as pg:
        cur = pg.cursor()
        for _ in range(runs):
            start = time.perf_counter()
            cur.execute(
                "SELECT team_name, category, mean_score, response_count "
                "FROM serving_team_category WHERE team_key = %s ORDER BY category",
                (team_key,),
            )
            result = cur.fetchall()
            times.append((time.perf_counter() - start) * 1000)
    mean_ms = sum(times) / len(times)
    return mean_ms, result


def main():
    print_panel("Lab 03: The Serving Layer Pattern",
                "OLAP compute (DuckDB) -> serving store (PostgreSQL) -> fast API reads.\n"
                "This is how analytics SaaS achieves sub-100ms responses.")

    # ── Stage 1: Compute in DuckDB ───────────────────────────────────
    with duckdb_conn() as duck:
        start = time.perf_counter()
        fact_count, gold_rows = stage1_compute_in_duckdb(duck)
        compute_ms = (time.perf_counter() - start) * 1000

        # Also benchmark the analytical query for comparison
        analytical_times = []
        for _ in range(20):
            s = time.perf_counter()
            duck.execute("""
                SELECT t.team_name, q.category, ROUND(AVG(f.score), 2) AS mean_score
                FROM fact_responses f
                JOIN dim_team t     ON f.team_key = t.team_key
                JOIN dim_question q ON f.question_key = q.qkey
                WHERE t.team_key = 1
                GROUP BY t.team_name, q.category ORDER BY q.category
            """).fetchall()
            analytical_times.append((time.perf_counter() - s) * 1000)
        analytical_ms = sum(analytical_times) / len(analytical_times)

    print_panel("STAGE 1: Compute Gold in DuckDB",
                f"Generated {fact_count:,} fact rows.\n"
                f"Computed {len(gold_rows)} Gold aggregate rows.\n"
                f"Compute time: {compute_ms:.1f} ms")

    print_table("Gold Aggregates (first 8 rows)",
                ["TeamKey", "Team", "Dept", "Category", "Mean", "N"],
                gold_rows[:8])

    # ── Stage 2: Push to PostgreSQL ──────────────────────────────────
    start = time.perf_counter()
    rows_pushed = stage2_push_to_postgres(gold_rows)
    push_ms = (time.perf_counter() - start) * 1000

    print_panel("STAGE 2: Push to PostgreSQL",
                f"Wrote {rows_pushed} rows to serving_team_category.\n"
                f"Push time: {push_ms:.1f} ms (includes table creation + indexing)")

    # ── Stage 3: Serve from PostgreSQL ───────────────────────────────
    serve_ms, serve_result = stage3_serve_from_postgres(team_key=1)

    print_panel("STAGE 3: Serve from PostgreSQL", "Point lookup: WHERE team_key = 1")
    print_table("API Response", ["Team", "Category", "Mean Score", "Responses"], serve_result)

    # ── Comparison ───────────────────────────────────────────────────
    print_table("Latency Comparison",
                ["Operation", "Avg Latency (ms)", "Where"],
                [
                    ("DuckDB analytical query (team_key=1)", f"{analytical_ms:.2f}", "OLAP engine"),
                    ("PostgreSQL point lookup (team_key=1)",  f"{serve_ms:.2f}",      "Serving store"),
                ])

    speedup = analytical_ms / serve_ms if serve_ms > 0 else float("inf")
    print_panel("Key Takeaway",
                f"PostgreSQL point lookup: {serve_ms:.2f} ms\n"
                f"DuckDB analytical query: {analytical_ms:.2f} ms\n"
                f"Speedup: {speedup:.1f}x\n\n"
                f"The serving layer pattern:\n"
                f"  1. COMPUTE in OLAP engine (DuckDB, BigQuery, Snowflake)\n"
                f"     Heavy joins + aggregations, batch schedule\n"
                f"  2. PUSH results to transactional store (PostgreSQL, Redis)\n"
                f"     Small, pre-aggregated tables with indexes\n"
                f"  3. SERVE from transactional store\n"
                f"     Simple key lookups, sub-millisecond, handles concurrency\n\n"
                f"This is where the '12-15 second query' vs 'sub-100ms dashboard' gap\n"
                f"comes from. Overview/dashboard data is served from cache/serving layer.\n"
                f"Ad-hoc drill-downs hit the OLAP engine and take longer.")


if __name__ == "__main__":
    main()
