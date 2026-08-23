"""Lab 03: PostgreSQL as a Hybrid Engine

PostgreSQL can handle light analytics via materialized views — pre-computed
aggregations stored as tables, refreshed on demand. This lab shows:
  1. Transactional writes (INSERT/UPDATE/DELETE)
  2. Materialized view serves analytical queries fast... but is STALE after writes
  3. REFRESH to sync, then when hybrid is "good enough" vs dedicated OLAP
"""

import sys, os, time, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

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
TEAMS = [(i, f"Team_{str(i).zfill(2)}", ["Engineering", "Product", "Sales"][i % 3])
         for i in range(1, 16)]

MV_SQL = """CREATE MATERIALIZED VIEW mv_team_scores AS
    SELECT t.team_id, t.name AS team_name, t.department, q.category,
           ROUND(AVG(r.score)::numeric, 2) AS mean_score, COUNT(*) AS response_count
    FROM survey_responses r
    JOIN survey_teams t ON r.team_id = t.team_id
    JOIN survey_questions q ON r.question_id = q.q_id
    GROUP BY t.team_id, t.name, t.department, q.category"""

RUNTIME_SQL = """SELECT t.name, t.department, q.category,
       ROUND(AVG(r.score)::numeric, 2) AS mean_score, COUNT(*)
    FROM survey_responses r
    JOIN survey_teams t ON r.team_id = t.team_id
    JOIN survey_questions q ON r.question_id = q.q_id
    GROUP BY t.name, t.department, q.category ORDER BY t.name, q.category"""


def setup():
    random.seed(42)
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_team_scores CASCADE")
        for t in ["survey_responses", "survey_questions", "survey_teams"]:
            cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        cur.execute("CREATE TABLE survey_teams (team_id INT PRIMARY KEY, name VARCHAR(50), department VARCHAR(50))")
        cur.execute("CREATE TABLE survey_questions (q_id INT PRIMARY KEY, code VARCHAR(10), category VARCHAR(30))")
        cur.execute("""CREATE TABLE survey_responses (
            id SERIAL PRIMARY KEY, team_id INT REFERENCES survey_teams(team_id),
            question_id INT REFERENCES survey_questions(q_id),
            score FLOAT NOT NULL, submitted_at TIMESTAMP DEFAULT NOW())""")
        for tid, name, dept in TEAMS:
            cur.execute("INSERT INTO survey_teams VALUES (%s,%s,%s)", (tid, name, dept))
        for i, (code, cat, _) in enumerate(QUESTIONS, 1):
            cur.execute("INSERT INTO survey_questions VALUES (%s,%s,%s)", (i, code, cat))
        for tid, _, _ in TEAMS:
            for qi, (_, _, base) in enumerate(QUESTIONS, 1):
                for _ in range(50):
                    score = max(1.0, min(5.0, round(base + (random.random() - 0.5) * 1.2, 1)))
                    cur.execute("INSERT INTO survey_responses (team_id, question_id, score) VALUES (%s,%s,%s)",
                                (tid, qi, score))
        cur.execute("SELECT COUNT(*) FROM survey_responses")
        return cur.fetchone()[0]


def bench(cur, sql, params=None, runs=20):
    times, result = [], None
    for _ in range(runs):
        s = time.perf_counter()
        cur.execute(sql, params)
        result = cur.fetchall()
        times.append((time.perf_counter() - s) * 1000)
    return sum(times) / len(times), result


def main():
    row_count = setup()
    print_panel("Lab 03: PostgreSQL as a Hybrid Engine",
                f"Created {row_count:,} survey responses in PostgreSQL.\n"
                f"15 teams x 12 questions x 50 respondents.\n\n"
                f"Can PostgreSQL handle BOTH transactional writes\n"
                f"AND analytical reads without a separate OLAP engine?")

    # ── Step 1: OLTP operations ─────────────────────────────────────
    print_panel("STEP 1", "Transactional operations (OLTP strength)")
    ops = [("INSERT 1 response", "INSERT INTO survey_responses (team_id, question_id, score) VALUES (%s,%s,%s)", (1,1,4.5)),
           ("UPDATE 1 response", "UPDATE survey_responses SET score = 5.0 WHERE id = 1", None),
           ("DELETE 1 response", "DELETE FROM survey_responses WHERE id = 1", None)]
    op_rows = []
    with pg_conn() as pg:
        cur = pg.cursor()
        for label, sql, params in ops:
            s = time.perf_counter()
            for _ in range(100):
                cur.execute(sql, params) if params else cur.execute(sql)
            op_rows.append((label, f"{(time.perf_counter()-s)*1000/100:.3f}"))
            pg.rollback()
    print_table("Transactional Operations (avg per operation)", ["Operation", "Latency (ms)"], op_rows)

    # ── Step 2: Runtime analytical query ────────────────────────────
    print_panel("STEP 2", "Analytical query WITHOUT materialized view (runtime)")
    with pg_conn() as pg:
        cur = pg.cursor()
        runtime_ms, runtime_result = bench(cur, RUNTIME_SQL)
    print_table("Runtime Aggregation Result (first 8 rows)",
                ["Team", "Dept", "Category", "Mean", "Count"], runtime_result[:8])

    # ── Step 3: Create MV and query it ──────────────────────────────
    print_panel("STEP 3", "Create materialized view, then query it")
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_team_scores CASCADE")
        s = time.perf_counter()
        cur.execute(MV_SQL)
        cur.execute("CREATE INDEX idx_mv_team ON mv_team_scores (team_id)")
        mv_build_ms = (time.perf_counter() - s) * 1000

    with pg_conn() as pg:
        cur = pg.cursor()
        mv_ms, mv_result = bench(cur,
            "SELECT team_name, department, category, mean_score, response_count "
            "FROM mv_team_scores ORDER BY team_name, category")
    print_table("MV Query Result (first 8 rows)",
                ["Team", "Dept", "Category", "Mean", "Count"], mv_result[:8])
    print_table("Runtime vs Materialized View",
                ["Approach", "Query Latency (ms)", "Build Cost (ms)"],
                [("Runtime aggregation", f"{runtime_ms:.2f}", "0 (computed on demand)"),
                 ("Materialized view", f"{mv_ms:.2f}", f"{mv_build_ms:.1f} (one-time)")])

    # ── Step 4: Staleness ───────────────────────────────────────────
    print_panel("STEP 4", "The staleness problem: INSERT new data, MV is stale")
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        for qi in range(1, 13):
            for _ in range(20):
                cur.execute("INSERT INTO survey_responses (team_id, question_id, score) VALUES (%s,%s,%s)", (1, qi, 5.0))
        cur.execute("SELECT mean_score FROM mv_team_scores WHERE team_id = 1 AND category = 'Basic Needs'")
        stale_score = cur.fetchone()[0]
        cur.execute("""SELECT ROUND(AVG(r.score)::numeric, 2)
            FROM survey_responses r JOIN survey_questions q ON r.question_id = q.q_id
            WHERE r.team_id = 1 AND q.category = 'Basic Needs'""")
        fresh_score = cur.fetchone()[0]
    print_table("Staleness After 240 New Responses (Team_01, all score=5.0)",
                ["Source", "Basic Needs Mean", "Status"],
                [("Materialized View", str(stale_score), "STALE — doesn't see new data"),
                 ("Runtime Query", str(fresh_score), "FRESH — always current")])

    # ── Step 5: REFRESH ─────────────────────────────────────────────
    print_panel("STEP 5", "REFRESH MATERIALIZED VIEW — sync with current data")
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        s = time.perf_counter()
        cur.execute("REFRESH MATERIALIZED VIEW mv_team_scores")
        refresh_ms = (time.perf_counter() - s) * 1000
        cur.execute("SELECT mean_score FROM mv_team_scores WHERE team_id = 1 AND category = 'Basic Needs'")
        refreshed_score = cur.fetchone()[0]
    print_table("After REFRESH", ["Source", "Basic Needs Mean", "Status"],
                [("Materialized View", str(refreshed_score), "NOW FRESH"),
                 ("Runtime Query", str(fresh_score), "Always fresh")])
    print_panel("REFRESH Cost", f"REFRESH MATERIALIZED VIEW took {refresh_ms:.1f} ms.\n"
                f"This re-runs the full aggregation query and replaces the stored result.\n"
                f"At 9K rows, fast. At 100M rows, this can take minutes and block reads\n"
                f"(unless you use REFRESH MATERIALIZED VIEW CONCURRENTLY, which requires\n"
                f"a UNIQUE index and holds no lock but still takes the same compute time).")

    print_panel("When Is Hybrid 'Good Enough'?",
        "PostgreSQL + materialized views works when ALL of these hold:\n\n"
        "  1. Dataset is small-to-medium (<10M rows)\n"
        "     MV refresh takes seconds, not minutes\n\n"
        "  2. Staleness is acceptable\n"
        "     Engagement surveys update quarterly — refreshing MVs\n"
        "     hourly or daily is fine\n\n"
        "  3. Query patterns are predictable\n"
        "     You know which aggregations to materialize\n\n"
        "  4. No heavy concurrent analytical load\n"
        "     A few dashboard queries per minute, not thousands\n\n"
        "When you outgrow this:\n"
        "  - Data > 10M rows: MV refresh gets slow, competes with OLTP\n"
        "  - Need ad-hoc analytics: can't pre-materialize every question\n"
        "  - Concurrent analytical users: full-table scans block writes\n"
        "  -> Move analytical workload to DuckDB/Snowflake (dedicated OLAP)")


if __name__ == "__main__":
    main()
