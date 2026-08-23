"""Lab 01: Row vs Column Storage — Same Query, Different Engines

Loads identical survey data into PostgreSQL (row-oriented) and DuckDB
(column-oriented). Runs analytical queries on both and compares execution
time and query plans.
"""

import sys, os, time, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.pg import pg_conn
from shared.display import print_table, print_sql, print_panel

random.seed(42)
NUM_ROWS = 100_000
TEAMS = ["Engineering", "Sales", "Product", "Support", "Marketing"]
CATEGORIES = {
    "Basic Needs": ["q01", "q02"],
    "Individual":  ["q03", "q04", "q05", "q06"],
    "Teamwork":    ["q07", "q08", "q09", "q10"],
    "Growth":      ["q11", "q12"],
}
Q_COLS = [f"q{i:02d}" for i in range(1, 13)]


def generate_rows(n):
    """Generate n survey response rows as list of tuples."""
    rows = []
    for i in range(n):
        team = TEAMS[i % len(TEAMS)]
        scores = [random.randint(1, 5) for _ in range(12)]
        rows.append((i, team, *scores))
    return rows


# ── Analytical queries to benchmark ──────────────────────────────────────
QUERIES = {
    "Narrow scan: AVG of 1 column": """
        SELECT AVG(q01) AS mean_q01 FROM survey_responses
    """,
    "Filtered aggregation": """
        SELECT team_id, AVG(q01) AS mean_q01, AVG(q07) AS mean_q07
        FROM survey_responses
        WHERE team_id = 'Engineering'
        GROUP BY team_id
    """,
    "Wide scan: AVG of all 12 columns": f"""
        SELECT {', '.join(f'AVG({q}) AS mean_{q}' for q in Q_COLS)}
        FROM survey_responses
    """,
}


def setup_postgres(rows):
    """Create table and load data into PostgreSQL."""
    cols_ddl = ", ".join(f"{q} INTEGER" for q in Q_COLS)
    with pg_conn(autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS survey_responses CASCADE")
        cur.execute(f"""
            CREATE TABLE survey_responses (
                response_id INTEGER PRIMARY KEY,
                team_id     VARCHAR(20),
                {cols_ddl}
            )
        """)
        # Batch insert
        placeholders = ", ".join(["%s"] * 14)
        for i in range(0, len(rows), 5000):
            batch = rows[i:i+5000]
            args_str = ", ".join(
                cur.mogrify(f"({placeholders})", row).decode() for row in batch
            )
            cur.execute(f"INSERT INTO survey_responses VALUES {args_str}")
        # Analyze for accurate plans
        cur.execute("ANALYZE survey_responses")


def setup_duckdb(conn, rows):
    """Create table and load data into DuckDB."""
    cols_ddl = ", ".join(f"{q} INTEGER" for q in Q_COLS)
    conn.execute(f"""
        CREATE TABLE survey_responses (
            response_id INTEGER PRIMARY KEY,
            team_id     VARCHAR,
            {cols_ddl}
        )
    """)
    conn.executemany(
        f"INSERT INTO survey_responses VALUES ({', '.join(['?']*14)})",
        rows,
    )


def benchmark_query(execute_fn, sql, label, runs=3):
    """Run a query multiple times and return median wall-clock time in ms."""
    times = []
    result = None
    for _ in range(runs):
        start = time.perf_counter()
        result = execute_fn(sql)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    times.sort()
    return times[len(times) // 2], result


def main():
    try:
        with pg_conn() as _test:
            pass
    except Exception:
        print_panel("Prerequisite: PostgreSQL Required",
                    "This lab compares PostgreSQL (row store) vs DuckDB (column store).\n"
                    "Start PostgreSQL via: docker compose -f docker/docker-compose.postgres.yml up -d\n"
                    "See labs 02-04 for DuckDB-only labs that don't require PostgreSQL.")
        return

    print_panel("Lab 01: Row vs Column Storage",
                f"Loading {NUM_ROWS:,} survey response rows into PostgreSQL "
                f"(row-oriented) and DuckDB (column-oriented).\n"
                "Running identical analytical queries on both.")

    rows = generate_rows(NUM_ROWS)

    # ── Setup both databases ──
    print_panel("Setup", "Loading data into PostgreSQL and DuckDB...")
    setup_postgres(rows)

    with duckdb_conn() as duck:
        setup_duckdb(duck, rows)

        # ── Benchmark each query ──
        comparison_rows = []
        for label, sql in QUERIES.items():
            print_sql(sql)

            # PostgreSQL
            with pg_conn() as pgc:
                pgcur = pgc.cursor()
                pg_time, _ = benchmark_query(
                    lambda s: pgcur.execute(s) or pgcur.fetchall(), sql, label
                )

            # DuckDB
            duck_time, _ = benchmark_query(
                lambda s: duck.execute(s).fetchall(), sql, label
            )

            speedup = pg_time / duck_time if duck_time > 0 else float("inf")
            comparison_rows.append((
                label, f"{pg_time:.1f} ms", f"{duck_time:.1f} ms", f"{speedup:.1f}x"
            ))

        print_table("Row (PostgreSQL) vs Column (DuckDB)",
                    ["Query", "PostgreSQL", "DuckDB", "Speedup"],
                    comparison_rows)

        # ── EXPLAIN ANALYZE comparison ──
        explain_sql = "SELECT team_id, AVG(q01) FROM survey_responses GROUP BY team_id"
        print_panel("Query Plans", "EXPLAIN ANALYZE on the grouped aggregation query")
        print_sql(explain_sql)

        # DuckDB plan
        duck_plan = duck.execute(f"EXPLAIN ANALYZE {explain_sql}").fetchall()
        duck_plan_text = "\n".join(str(r[1]) for r in duck_plan)
        print_panel("DuckDB Plan (columnar — reads only q01 + team_id columns)",
                    duck_plan_text)

        # PostgreSQL plan
        with pg_conn() as pgc:
            pgcur = pgc.cursor()
            pgcur.execute(f"EXPLAIN ANALYZE {explain_sql}")
            pg_plan = pgcur.fetchall()
            pg_plan_text = "\n".join(str(r[0]) for r in pg_plan)
            print_panel("PostgreSQL Plan (row — reads entire rows including q02-q12)",
                        pg_plan_text)

        print_panel("Key Takeaway",
                    "Columnar (DuckDB) reads only the columns the query needs.\n"
                    "Row (PostgreSQL) reads entire rows, wasting I/O on unused columns.\n"
                    "The wider the table and the fewer columns queried, the bigger "
                    "the columnar advantage.\n\n"
                    "Note: PostgreSQL uses a sequential scan because there is no "
                    "index to help with aggregation — it must read every row page.\n"
                    "DuckDB's columnar layout means it physically skips the columns "
                    "it doesn't need.")


if __name__ == "__main__":
    main()
