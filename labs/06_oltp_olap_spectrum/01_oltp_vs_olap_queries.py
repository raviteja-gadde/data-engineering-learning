"""Lab 01: OLTP vs OLAP Query Patterns — Same Data, Different Engines

Same 108K-row survey dataset in PostgreSQL (row-oriented) and DuckDB (columnar).
Runs OLTP queries (lookup, INSERT, UPDATE) and OLAP queries (aggregation, window)
on both. Shows where each engine shines.
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
TEAMS = [f"Team_{str(i).zfill(2)}" for i in range(1, 21)]


def load_duckdb(conn):
    conn.execute("CREATE TABLE teams (team_id INT PRIMARY KEY, name VARCHAR)")
    for i, t in enumerate(TEAMS, 1):
        conn.execute("INSERT INTO teams VALUES (?,?)", [i, t])
    conn.execute("CREATE TABLE questions (q_id INT PRIMARY KEY, code VARCHAR, category VARCHAR, base FLOAT)")
    for i, (code, cat, base) in enumerate(QUESTIONS, 1):
        conn.execute("INSERT INTO questions VALUES (?,?,?,?)", [i, code, cat, base])
    conn.execute("""
        CREATE TABLE responses AS
        SELECT ROW_NUMBER() OVER () AS response_id, t.team_id, q.q_id AS question_id,
               GREATEST(1.0, LEAST(5.0, ROUND(q.base + (RANDOM()-0.5)*1.2, 1))) AS score
        FROM teams t CROSS JOIN questions q CROSS JOIN generate_series(1, 450) AS r(n)
    """)
    return conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]


def load_postgres(duck):
    from psycopg2.extras import execute_values
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        for tbl in ["responses", "teams", "questions"]:
            cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        cur.execute("CREATE TABLE teams (team_id INT PRIMARY KEY, name VARCHAR(50))")
        cur.execute("CREATE TABLE questions (q_id INT PRIMARY KEY, code VARCHAR(10), category VARCHAR(30), base FLOAT)")
        cur.execute("CREATE TABLE responses (response_id INT PRIMARY KEY, team_id INT, question_id INT, score FLOAT)")
        for row in duck.execute("SELECT * FROM teams").fetchall():
            cur.execute("INSERT INTO teams VALUES (%s,%s)", row)
        for row in duck.execute("SELECT * FROM questions").fetchall():
            cur.execute("INSERT INTO questions VALUES (%s,%s,%s,%s)", row)
        execute_values(cur, "INSERT INTO responses VALUES %s",
                       duck.execute("SELECT * FROM responses").fetchall(), page_size=5000)
        cur.execute("CREATE INDEX idx_resp_team ON responses (team_id)")
        cur.execute("ANALYZE responses")


def bench_pg(sql, params=None, runs=10):
    times, result = [], None
    with pg_conn() as pg:
        cur = pg.cursor()
        for _ in range(runs):
            s = time.perf_counter()
            cur.execute(sql, params)
            result = cur.fetchall()
            times.append((time.perf_counter() - s) * 1000)
    return sum(times) / len(times)


def bench_duck(conn, sql, runs=10):
    times = []
    for _ in range(runs):
        s = time.perf_counter()
        conn.execute(sql).fetchall()
        times.append((time.perf_counter() - s) * 1000)
    return sum(times) / len(times)


def bench_write(sql, params, runs=5):
    times = []
    with pg_conn() as pg:
        cur = pg.cursor()
        for _ in range(runs):
            s = time.perf_counter()
            cur.execute(sql, params)
            times.append((time.perf_counter() - s) * 1000)
            pg.rollback()
    return sum(times) / len(times)


def main():
    with duckdb_conn() as duck:
        count = load_duckdb(duck)
        load_postgres(duck)
        max_id = duck.execute("SELECT MAX(response_id) FROM responses").fetchone()[0]

        print_panel("Lab 01: OLTP vs OLAP Query Patterns",
                    f"Loaded {count:,} rows into both DuckDB and PostgreSQL.\n"
                    f"Same data, same schema. Different engine architectures.")

        # ── OLTP: point lookup, INSERT, UPDATE ──────────────────────
        rid = random.randint(1, max_id)
        oltp = []
        p = bench_pg("SELECT * FROM responses WHERE response_id = %s", (rid,))
        d = bench_duck(duck, f"SELECT * FROM responses WHERE response_id = {rid}")
        winner = "PG" if p < d else "Duck*"
        oltp.append(("Point lookup (1 row by PK)", f"{p:.3f}", f"{d:.3f}", winner))

        p = bench_write("INSERT INTO responses VALUES (%s,%s,%s,%s)", (max_id+1, 1, 1, 4.0))
        d = bench_duck(duck, f"INSERT INTO responses VALUES ({max_id+9000000}, 1, 1, 4.0)", runs=5)
        winner = "PG" if p < d else "Duck*"
        oltp.append(("INSERT 1 row", f"{p:.3f}", f"{d:.3f}", winner))

        p = bench_write("UPDATE responses SET score=5.0 WHERE response_id=%s", (rid,))
        d = bench_duck(duck, f"UPDATE responses SET score=5.0 WHERE response_id={rid}", runs=5)
        winner = "PG" if p < d else "Duck*"
        oltp.append(("UPDATE 1 row", f"{p:.3f}", f"{d:.3f}", winner))

        print_table("OLTP Queries (* = misleading; DuckDB is in-process, PG is over TCP)",
                    ["Query", "PostgreSQL (ms)", "DuckDB (ms)", "Winner"], oltp)

        # ── OLAP: aggregation, filtered agg, window ─────────────────
        olap_queries = [
            ("Full-table AVG by category",
             "SELECT q.category, ROUND(AVG(r.score){cast},2), COUNT(*) FROM responses r "
             "JOIN questions q ON r.question_id=q.q_id GROUP BY q.category ORDER BY q.category"),
            ("Filtered AGG (5 teams x cat)",
             "SELECT t.name, q.category, ROUND(AVG(r.score){cast},2) FROM responses r "
             "JOIN teams t ON r.team_id=t.team_id JOIN questions q ON r.question_id=q.q_id "
             "WHERE t.name IN ('Team_01','Team_05','Team_10','Team_15','Team_20') "
             "GROUP BY t.name, q.category ORDER BY t.name, q.category"),
            ("Window: RANK teams by score",
             "SELECT t.name, ROUND(AVG(r.score){cast},2), RANK() OVER (ORDER BY AVG(r.score) DESC) "
             "FROM responses r JOIN teams t ON r.team_id=t.team_id GROUP BY t.name ORDER BY 3"),
        ]
        olap = []
        for label, sql_tmpl in olap_queries:
            p = bench_pg(sql_tmpl.format(cast="::numeric"))
            d = bench_duck(duck, sql_tmpl.format(cast=""))
            olap.append((label, f"{p:.2f}", f"{d:.2f}", "PG" if p < d else "DuckDB"))

        print_table("OLAP Queries (complex, scan-heavy operations)",
                    ["Query", "PostgreSQL (ms)", "DuckDB (ms)", "Winner"], olap)

        print_panel("Key Takeaway",
            "OLAP queries (aggregations, joins, window functions):\n"
            "  DuckDB wins decisively — columnar storage reads only needed\n"
            "  columns, vectorized execution processes batches of 1024+ values,\n"
            "  and automatic parallelism splits scans across cores.\n\n"
            "OLTP queries (point lookups, single-row writes):\n"
            "  DuckDB may also appear faster here, but that's misleading.\n"
            "  DuckDB runs in-process (no network hop), while PostgreSQL\n"
            "  is accessed over a TCP socket. At production scale with\n"
            "  concurrent users, PostgreSQL's B-tree indexes, row-level\n"
            "  locking, and MVCC give it the OLTP advantage DuckDB can't match.\n\n"
            "This is why real systems use BOTH:\n"
            "  - PostgreSQL serves the app (concurrent writes, row lookups)\n"
            "  - DuckDB/Snowflake handles analytics (fast scans, aggregations)\n"
            "  - The serving layer pattern bridges them (Lab 02)")


if __name__ == "__main__":
    main()
