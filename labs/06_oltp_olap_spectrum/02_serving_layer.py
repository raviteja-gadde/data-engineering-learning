"""Lab 02: Serving Layer — WHY Each Engine Plays Its Role

Goes deeper than Topic 4's pipeline: demonstrates the mismatch penalty when
you use PostgreSQL for heavy aggregation or DuckDB for point lookups.
"""

import sys, os, time, random
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
TEAMS = [f"Team_{str(i).zfill(2)}" for i in range(1, 51)]
DEPTS = ["Engineering", "Product", "Sales", "Marketing", "Support"]
NUM_RESPONDENTS = 80

GOLD_SQL = """\
SELECT t.team_id, t.name AS team_name, t.department, q.category,
       ROUND(AVG(r.score){cast}, 2), ROUND(STDDEV(r.score){cast}, 2),
       COUNT(*), ROUND(MIN(r.score){cast}, 1), ROUND(MAX(r.score){cast}, 1)
FROM {resp} r JOIN {teams} t ON r.team_id = t.team_id
JOIN {qs} q ON r.question_id = q.{qid}
GROUP BY t.team_id, t.name, t.department, q.category ORDER BY t.team_id, q.category"""

HEAVY_SQL = """\
SELECT t.department, q.category, ROUND(AVG(r.score){cast}, 2),
       ROUND(STDDEV(r.score){cast}, 2), COUNT(*),
       ROUND(PERCENT_RANK() OVER (PARTITION BY q.category ORDER BY AVG(r.score) DESC){cast}, 3)
FROM {resp} r JOIN {teams} t ON r.team_id = t.team_id
JOIN {qs} q ON r.question_id = q.{qid}
GROUP BY t.department, q.category ORDER BY q.category, t.department"""


def timed(fn, runs=20):
    times, result = [], None
    for _ in range(runs):
        s = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - s) * 1000)
    return sum(times) / len(times), result


def main():
    with duckdb_conn() as duck:
        # ── Generate data in DuckDB ─────────────────────────────────
        duck.execute("CREATE TABLE teams (team_id INT, name VARCHAR, department VARCHAR)")
        for i, t in enumerate(TEAMS, 1):
            duck.execute("INSERT INTO teams VALUES (?,?,?)", [i, t, DEPTS[i % 5]])
        duck.execute("CREATE TABLE questions (q_id INT, code VARCHAR, category VARCHAR, base FLOAT)")
        for i, (code, cat, base) in enumerate(QUESTIONS, 1):
            duck.execute("INSERT INTO questions VALUES (?,?,?,?)", [i, code, cat, base])
        duck.execute(f"""CREATE TABLE responses AS
            SELECT ROW_NUMBER() OVER () AS id, t.team_id, q.q_id AS question_id,
                   GREATEST(1.0, LEAST(5.0, ROUND(q.base + (RANDOM()-0.5)*1.2, 1))) AS score
            FROM teams t CROSS JOIN questions q CROSS JOIN generate_series(1, {NUM_RESPONDENTS}) AS r(n)""")
        row_count = duck.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

        print_panel("Lab 02: Serving Layer — Why Each Engine",
                    f"Generated {row_count:,} raw response rows.\n"
                    f"50 teams x 12 questions x {NUM_RESPONDENTS} respondents.\n\n"
                    f"Goal: show WHY you use DuckDB for computation and\n"
                    f"PostgreSQL for serving — not just that you can.")

        # ── Step 1: Computation — DuckDB vs PostgreSQL ──────────────
        print_panel("STEP 1", "Heavy aggregation: DuckDB (OLAP) vs PostgreSQL (OLTP)")

        duck_sql = GOLD_SQL.format(cast="", resp="responses", teams="teams", qs="questions", qid="q_id")
        duck_ms, gold_rows = timed(lambda: duck.execute(duck_sql).fetchall())

        with pg_conn(autocommit=True) as pg:  # Load raw data into PG for comparison
            cur = pg.cursor()
            for tbl in ["gold_serving", "responses_raw", "teams_raw", "questions_raw"]:
                cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
            cur.execute("CREATE TABLE teams_raw (team_id INT PRIMARY KEY, name VARCHAR, department VARCHAR)")
            cur.execute("CREATE TABLE questions_raw (q_id INT PRIMARY KEY, code VARCHAR, category VARCHAR, base FLOAT)")
            cur.execute("CREATE TABLE responses_raw (id INT PRIMARY KEY, team_id INT, question_id INT, score FLOAT)")
            for r in duck.execute("SELECT * FROM teams").fetchall():
                cur.execute("INSERT INTO teams_raw VALUES (%s,%s,%s)", r)
            for r in duck.execute("SELECT * FROM questions").fetchall():
                cur.execute("INSERT INTO questions_raw VALUES (%s,%s,%s,%s)", r)
            from psycopg2.extras import execute_values
            execute_values(cur, "INSERT INTO responses_raw VALUES %s",
                           duck.execute("SELECT * FROM responses").fetchall(), page_size=5000)
            cur.execute("CREATE INDEX idx_raw_team ON responses_raw (team_id); ANALYZE responses_raw")

        pg_sql = GOLD_SQL.format(cast="::numeric", resp="responses_raw", teams="teams_raw", qs="questions_raw", qid="q_id")
        def pg_compute():
            with pg_conn() as pg:
                cur = pg.cursor(); cur.execute(pg_sql); return cur.fetchall()
        pg_ms, _ = timed(pg_compute)

        print_table("Computation: Heavy Aggregation", ["Engine", "Role", "Avg Latency (ms)", "Why"],
                    [("DuckDB", "OLAP (columnar)", f"{duck_ms:.2f}", "Columnar scan, vectorized batches"),
                     ("PostgreSQL", "OLTP (row-oriented)", f"{pg_ms:.2f}", "Full-row reads, row-by-row processing")])
        ratio = pg_ms / duck_ms if duck_ms > 0 else 1
        print_panel("Computation Verdict",
            f"DuckDB is {ratio:.1f}x faster. It reads only the 3 columns it needs\n"
            f"instead of full rows. At 48K rows the gap is visible;\n"
            f"at 10M rows it becomes dramatic.")

        # ── Step 2: Push Gold to PostgreSQL ─────────────────────────
        push_s = time.perf_counter()
        with pg_conn(autocommit=True) as pg:
            cur = pg.cursor()
            cur.execute("DROP TABLE IF EXISTS gold_serving")
            cur.execute("""CREATE TABLE gold_serving (
                team_id INT, team_name VARCHAR(50), department VARCHAR(50), category VARCHAR(30),
                mean_score NUMERIC(4,2), std_score NUMERIC(4,2), n INT,
                min_score NUMERIC(3,1), max_score NUMERIC(3,1), PRIMARY KEY (team_id, category))""")
            cur.execute("CREATE INDEX idx_gold_team ON gold_serving (team_id)")
            for row in gold_rows:
                cur.execute("INSERT INTO gold_serving VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
            cur.execute("ANALYZE gold_serving")
        push_ms = (time.perf_counter() - push_s) * 1000

        print_panel("STEP 2", f"Pushed {len(gold_rows)} Gold rows to PostgreSQL serving table.\n"
                    f"Push time: {push_ms:.1f} ms\n\n"
                    f"Volume reduction: {row_count:,} raw rows -> {len(gold_rows)} aggregated rows.\n"
                    f"This is why the serving layer works: the result is tiny.")

        # ── Step 3: Serving — PostgreSQL point lookup ───────────────
        print_panel("STEP 3", "Serving: simulate 3 agent tool calls (point lookups)\n"
                    "(Single connection reused — measures query time, not connect time)")

        team_ids = random.sample(range(1, 51), 3)
        serving_results = []
        with pg_conn() as pg:
            cur = pg.cursor()
            for tid in team_ids:
                ms, rows = timed(lambda t=tid: (cur.execute(
                    "SELECT team_name, department, category, mean_score, n "
                    "FROM gold_serving WHERE team_id = %s ORDER BY category", (t,)) or cur.fetchall()), runs=50)
                serving_results.append((f"Team {tid:02d}", f"{ms:.3f}", len(rows)))

        print_table("Serving: Point Lookups (simulated agent calls)",
                    ["Query", "Avg Latency (ms)", "Rows Returned"], serving_results)

        # ── Step 4: The mismatch — wrong engine for each role ───────
        print_panel("STEP 4", "What if you use the WRONG engine for each role?")

        # Mismatch A: DuckDB runtime aggregation per request (no serving table)
        duck_lookup_ms, _ = timed(lambda: duck.execute(f"""
            SELECT t.name, t.department, q.category, ROUND(AVG(r.score),2)
            FROM responses r JOIN teams t ON r.team_id = t.team_id
            JOIN questions q ON r.question_id = q.q_id
            WHERE t.team_id = {random.randint(1,50)}
            GROUP BY t.name, t.department, q.category ORDER BY q.category""").fetchall(), runs=30)

        # Mismatch B: PostgreSQL heavy scan vs DuckDB heavy scan
        heavy_duck = HEAVY_SQL.format(cast="", resp="responses", teams="teams", qs="questions", qid="q_id")
        heavy_pg = HEAVY_SQL.format(cast="::numeric", resp="responses_raw", teams="teams_raw", qs="questions_raw", qid="q_id")

        duck_heavy_ms, _ = timed(lambda: duck.execute(heavy_duck).fetchall(), runs=10)
        with pg_conn() as pg:
            cur = pg.cursor()
            pg_heavy_ms, _ = timed(lambda: (cur.execute(heavy_pg), cur.fetchall()), runs=10)

        avg_serving = sum(float(r[1]) for r in serving_results) / len(serving_results)
        print_table("Engine Mismatch Penalty",
                    ["Task", "Right Engine", "Wrong Engine", "Penalty"],
                    [("Serve 1 team overview",
                      f"PG serving table: {avg_serving:.3f} ms",
                      f"DuckDB runtime agg: {duck_lookup_ms:.2f} ms",
                      f"{duck_lookup_ms/avg_serving:.0f}x slower" if avg_serving > 0.001 else "N/A"),
                     ("Heavy analytical scan",
                      f"DuckDB: {duck_heavy_ms:.2f} ms",
                      f"PostgreSQL: {pg_heavy_ms:.2f} ms",
                      f"{pg_heavy_ms/duck_heavy_ms:.1f}x slower" if duck_heavy_ms > 0.001 else "N/A")])

        print_panel("Key Takeaway",
            "The serving layer pattern works because each engine does its job:\n\n"
            "  DuckDB (OLAP) for COMPUTATION:\n"
            "    - Columnar storage reads only needed columns\n"
            "    - Vectorized execution processes batches, not rows\n"
            "    - One heavy query runs once, produces small output\n\n"
            "  PostgreSQL (OLTP) for SERVING:\n"
            "    - B-tree index on team_id: O(log n) lookup\n"
            "    - Row-oriented storage returns complete records fast\n"
            "    - Handles concurrent connections (100 agents querying)\n"
            "    - ACID guarantees for consistent reads\n\n"
            "Using the wrong engine works but wastes resources.\n"
            "The mismatch penalty grows with data size and concurrency.")


if __name__ == "__main__":
    main()
