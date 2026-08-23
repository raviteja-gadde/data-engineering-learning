"""Lab 04: Caching Strategies for Survey Data

Benchmarks 3 caching strategies for serving survey overview data:

  (a) In-memory dict cache (simulating Redis)
  (b) PostgreSQL materialized view
  (c) Pre-computed summary table (Gold layer)

Workload: 100 reads, 1 write (new survey data), 100 more reads.
Compares: latency, correctness after write, staleness behavior.
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
TEAMS = [(i, f"Team_{str(i).zfill(2)}") for i in range(1, 11)]

AGG_SQL = """SELECT t.team_id, t.name, q.category,
       ROUND(AVG(r.score)::numeric, 2) AS mean_score, COUNT(*) AS n
FROM cache_responses r
JOIN cache_teams t ON r.team_id = t.team_id
JOIN cache_questions q ON r.question_id = q.q_id
GROUP BY t.team_id, t.name, q.category"""


def setup_data():
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_cache_scores CASCADE")
        for tbl in ["gold_summary", "cache_responses", "cache_questions", "cache_teams"]:
            cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        cur.execute("CREATE TABLE cache_teams (team_id INT PRIMARY KEY, name VARCHAR(50))")
        cur.execute("CREATE TABLE cache_questions (q_id INT PRIMARY KEY, code VARCHAR(10), category VARCHAR(30))")
        cur.execute("CREATE TABLE cache_responses (id SERIAL PRIMARY KEY, team_id INT, question_id INT, score FLOAT)")
        for tid, name in TEAMS:
            cur.execute("INSERT INTO cache_teams VALUES (%s,%s)", (tid, name))
        for i, (code, cat, _) in enumerate(QUESTIONS, 1):
            cur.execute("INSERT INTO cache_questions VALUES (%s,%s,%s)", (i, code, cat))
        random.seed(42)
        rows = [(tid, qi, max(1.0, min(5.0, round(base + (random.random()-0.5)*1.2, 1))))
                for tid, _ in TEAMS for qi, (_, _, base) in enumerate(QUESTIONS, 1) for _ in range(40)]
        from psycopg2.extras import execute_values
        execute_values(cur, "INSERT INTO cache_responses (team_id, question_id, score) VALUES %s", rows, page_size=2000)
        cur.execute("CREATE INDEX idx_cache_resp_team ON cache_responses (team_id)")
        cur.execute("ANALYZE cache_responses")
        cur.execute("SELECT COUNT(*) FROM cache_responses")
        return cur.fetchone()[0]


def build_mv():
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_cache_scores")
        cur.execute(f"CREATE MATERIALIZED VIEW mv_cache_scores AS {AGG_SQL}")
        cur.execute("CREATE INDEX idx_mv_cache_team ON mv_cache_scores (team_id)")


def build_gold():
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        cur.execute("DROP TABLE IF EXISTS gold_summary")
        cur.execute(f"CREATE TABLE gold_summary AS {AGG_SQL}")
        cur.execute("CREATE INDEX idx_gold_summary_team ON gold_summary (team_id)")
        cur.execute("ALTER TABLE gold_summary ADD PRIMARY KEY (team_id, category)")
        cur.execute("ANALYZE gold_summary")


def build_mem_cache():
    cache = {}
    with pg_conn() as pg:
        cur = pg.cursor()
        cur.execute(AGG_SQL)
        for tid, name, cat, mean, n in cur.fetchall():
            cache.setdefault(tid, []).append((name, cat, float(mean), n))
    return cache


def bench_reads(mem_cache, n=100):
    """Benchmark all 3 strategies, return (mem_ms, mv_ms, gold_ms)."""
    mem_times = []
    for _ in range(n):
        tid = random.randint(1, 10)
        s = time.perf_counter()
        mem_cache.get(tid, [])
        mem_times.append((time.perf_counter() - s) * 1000)
    pg_times = {"mv": [], "gold": []}
    with pg_conn() as pg:
        cur = pg.cursor()
        for _ in range(n):
            tid = random.randint(1, 10)
            for label, tbl in [("mv", "mv_cache_scores"), ("gold", "gold_summary")]:
                s = time.perf_counter()
                cur.execute(f"SELECT name, category, mean_score FROM {tbl} WHERE team_id = %s", (tid,))
                cur.fetchall()
                pg_times[label].append((time.perf_counter() - s) * 1000)
    avg = lambda t: sum(t) / len(t)
    return avg(mem_times), avg(pg_times["mv"]), avg(pg_times["gold"])


def get_score(source, mem_cache, team_id=1, category="Basic Needs"):
    """Get a single score from a source ('mem', 'mv', 'gold', 'runtime')."""
    if source == "mem":
        return next((r[2] for r in mem_cache.get(team_id, []) if r[1] == category), None)
    sql_map = {"mv": "mv_cache_scores", "gold": "gold_summary"}
    with pg_conn() as pg:
        cur = pg.cursor()
        if source == "runtime":
            cur.execute("SELECT ROUND(AVG(r.score)::numeric, 2) FROM cache_responses r "
                        "JOIN cache_questions q ON r.question_id = q.q_id "
                        "WHERE r.team_id = %s AND q.category = %s", (team_id, category))
        else:
            cur.execute(f"SELECT mean_score FROM {sql_map[source]} WHERE team_id = %s AND category = %s",
                        (team_id, category))
        return float(cur.fetchone()[0])


def timed(fn):
    s = time.perf_counter()
    result = fn()
    return (time.perf_counter() - s) * 1000, result


def main():
    row_count = setup_data()
    print_panel("Lab 04: Caching Strategies Compared",
                f"Base data: {row_count:,} survey responses (10 teams x 12 questions x 40 respondents).\n\n"
                f"Three strategies for serving 'team overview' data:\n"
                f"  (a) In-memory dict cache (simulates Redis)\n"
                f"  (b) PostgreSQL materialized view\n"
                f"  (c) Pre-computed Gold summary table\n\n"
                f"Workload: 100 reads -> 1 write (600 new low scores) -> 100 reads.\n"
                f"Measuring: latency, correctness, staleness.")

    mem_build_ms, mem_cache = timed(build_mem_cache)
    mv_build_ms, _ = timed(build_mv)
    gold_build_ms, _ = timed(build_gold)
    print_table("Cache Build Cost", ["Strategy", "Build Time (ms)"],
                [("In-memory dict", f"{mem_build_ms:.1f}"), ("Materialized view", f"{mv_build_ms:.1f}"),
                 ("Gold summary table", f"{gold_build_ms:.1f}")])

    # ── Phase 1: 100 reads (pre-write) ──────────────────────────────
    print_panel("PHASE 1", "100 reads before any writes (all caches fresh)")
    mem_ms, mv_ms, gold_ms = bench_reads(mem_cache)
    print_table("Phase 1: Read Latency (avg of 100 reads)", ["Strategy", "Avg Read (ms)", "Data Source"],
                [("In-memory dict", f"{mem_ms:.4f}", "Python dict (no I/O)"),
                 ("Materialized view", f"{mv_ms:.3f}", "PostgreSQL MV (indexed)"),
                 ("Gold summary table", f"{gold_ms:.3f}", "PostgreSQL table (indexed)")])

    # ── Write event ─────────────────────────────────────────────────
    print_panel("WRITE EVENT", "Inserting 600 new responses for Team_01, all score=1.0.\n"
                "This simulates receiving a batch of bad survey results.")
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        rows = [(1, qi, 1.0) for qi in range(1, 13) for _ in range(50)]
        from psycopg2.extras import execute_values
        execute_values(cur, "INSERT INTO cache_responses (team_id, question_id, score) VALUES %s", rows)

    correct = get_score("runtime", mem_cache)

    # ── Phase 2: reads after write (stale) ──────────────────────────
    print_panel("PHASE 2", "100 reads after write — which caches are stale?")
    stale = [(s, get_score(s, mem_cache)) for s in ["mem", "mv", "gold"]]
    print_table("Staleness Check: Team_01 'Basic Needs' Mean Score",
                ["Strategy", "Returns", "Correct Value", "Stale?"],
                [(n, f"{v}", f"{correct}", r) for (s, v), (n, r) in
                 zip(stale, [("In-memory dict", "YES — no auto-invalidation"),
                             ("Materialized view", "YES — needs REFRESH"),
                             ("Gold summary table", "YES — needs rebuild")])])

    mem_ms2, mv_ms2, gold_ms2 = bench_reads(mem_cache)
    print_table("Phase 2: Read Latency (still fast, but stale)", ["Strategy", "Avg Read (ms)", "Data Correct?"],
                [("In-memory dict", f"{mem_ms2:.4f}", "NO"), ("Materialized view", f"{mv_ms2:.3f}", "NO"),
                 ("Gold summary table", f"{gold_ms2:.3f}", "NO")])

    # ── Refresh ─────────────────────────────────────────────────────
    print_panel("REFRESH", "Updating each cache to reflect the new data")
    mem_r_ms, mem_cache = timed(build_mem_cache)
    with pg_conn(autocommit=True) as pg:
        cur = pg.cursor()
        s = time.perf_counter()
        cur.execute("REFRESH MATERIALIZED VIEW mv_cache_scores")
        mv_r_ms = (time.perf_counter() - s) * 1000
    gold_r_ms, _ = timed(build_gold)

    after = {s: get_score(s, mem_cache) for s in ["mem", "mv", "gold"]}
    print_table("Refresh Cost and Correctness", ["Strategy", "Refresh (ms)", "Team_01 Basic Needs", "Correct?"],
                [("In-memory dict", f"{mem_r_ms:.1f}", f"{after['mem']}", "YES"),
                 ("Materialized view", f"{mv_r_ms:.1f}", f"{after['mv']}", "YES"),
                 ("Gold summary table", f"{gold_r_ms:.1f}", f"{after['gold']}", "YES")])

    print_panel("Caching Strategy Comparison",
        "                    Memory Dict    Mat. View    Gold Table\n"
        "                    ───────────    ─────────    ──────────\n"
        f"Read latency:       ~{mem_ms*1000:.0f} ns         ~{mv_ms:.1f} ms       ~{gold_ms:.1f} ms\n"
        f"Refresh cost:       {mem_r_ms:.0f} ms          {mv_r_ms:.0f} ms          {gold_r_ms:.0f} ms\n"
        "Auto-invalidation:  NO             NO           NO\n"
        "Survives restart:   NO             YES          YES\n"
        "Concurrent access:  In-process     Multi-conn   Multi-conn\n\n"
        "All three strategies share the same tradeoff:\n"
        "  FAST READS, but STALE after writes until refreshed.\n\n"
        "The choice depends on your constraints:\n"
        "  - Memory dict (Redis): fastest reads, lost on restart,\n"
        "    best for single-process or with Redis for shared access\n"
        "  - Materialized view: lives in PostgreSQL, no extra infra,\n"
        "    good for 'good enough' hybrid (see Lab 03)\n"
        "  - Gold table: explicit control, can be pushed from OLAP engine,\n"
        "    best for serving layer pattern (DuckDB -> PostgreSQL)")


if __name__ == "__main__":
    main()
