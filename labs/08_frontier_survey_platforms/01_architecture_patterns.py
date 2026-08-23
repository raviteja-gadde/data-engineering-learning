"""Lab 01: Survey Analytics Architecture Patterns — End-to-End Stack

The common multi-tier serving pattern used by Qualtrics, Culture Amp, and
Medallia: raw responses flow Bronze -> Silver -> Gold in DuckDB, Gold pushes
to a PostgreSQL serving table with indexes, and an in-memory cache sits on top.
A request is traced through the stack showing latency at each tier.
"""

import sys, os, time, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.pg import pg_conn
from shared.display import print_table, print_panel

CACHE: dict[str, dict] = {}  # simulates Redis/memcached


def ingest_bronze(conn):
    """Raw survey responses land in Bronze — no transformation."""
    conn.execute("""
        CREATE TABLE bronze_responses (
            response_id INTEGER, employee_id INTEGER, team VARCHAR,
            location VARCHAR, question_id VARCHAR, raw_score INTEGER,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    rows, rid = [], 1
    random.seed(42)
    for team, loc, n in [("Engineering","New York",12), ("Engineering","Austin",8),
                         ("Sales","Chicago",15), ("Product","New York",6),
                         ("Product","Austin",3)]:  # Austin Product: suppressed
        for eid in range(1, n + 1):
            for q in range(1, 13):
                rows.append((rid, eid + hash(team) % 1000, team, loc,
                             f"Q{q:02d}", random.randint(1, 5)))
                rid += 1
    conn.executemany(
        "INSERT INTO bronze_responses VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)", rows)
    print_panel("Bronze: Ingest",
                f"{conn.execute('SELECT COUNT(*) FROM bronze_responses').fetchone()[0]} raw rows")


def transform_silver(conn):
    """Silver: attach group sizes for suppression checks."""
    conn.execute("""
        CREATE TABLE silver_responses AS
        SELECT *, COUNT(DISTINCT employee_id) OVER (PARTITION BY team, location) AS group_size
        FROM bronze_responses
    """)
    suppressed = conn.execute(
        "SELECT DISTINCT team, location, group_size FROM silver_responses WHERE group_size < 4"
    ).fetchall()
    labels = [f"{t}/{l} (n={g})" for t, l, g in suppressed]
    print_panel("Silver: Suppress", f"Groups below threshold: {', '.join(labels) or 'none'}")


def aggregate_gold(conn):
    """Gold: pre-aggregated team+location scores with suppression applied."""
    conn.execute("""
        CREATE TABLE gold_team_scores AS
        SELECT team, location, COUNT(DISTINCT employee_id) AS n_employees, question_id,
               CASE WHEN COUNT(DISTINCT employee_id) >= 4
                    THEN ROUND(AVG(raw_score), 2) END AS avg_score,
               CASE WHEN COUNT(DISTINCT employee_id) >= 4
                    THEN ROUND(STDDEV(raw_score), 2) END AS std_score
        FROM silver_responses
        GROUP BY team, location, question_id
        ORDER BY team, location, question_id
    """)
    total = conn.execute("SELECT COUNT(*) FROM gold_team_scores").fetchone()[0]
    nulls = conn.execute("SELECT COUNT(*) FROM gold_team_scores WHERE avg_score IS NULL").fetchone()[0]
    print_panel("Gold: Aggregate", f"{total} rows ({nulls} suppressed)")


def push_to_serving(duck_conn):
    """Push Gold to PostgreSQL serving layer with composite index."""
    rows = duck_conn.execute("SELECT * FROM gold_team_scores").fetchall()
    with pg_conn(autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS serving_team_scores")
        cur.execute("""
            CREATE TABLE serving_team_scores (
                team VARCHAR(64), location VARCHAR(64), n_employees INTEGER,
                question_id VARCHAR(8), avg_score NUMERIC(4,2), std_score NUMERIC(4,2)
            )
        """)
        for row in rows:
            cur.execute("INSERT INTO serving_team_scores VALUES (%s,%s,%s,%s,%s,%s)", row)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_serving_team
            ON serving_team_scores (team, location, question_id)
        """)
        cur.close()
    print_panel("Serve: PostgreSQL", f"{len(rows)} rows pushed with index")


def cache_hot_queries():
    """Pre-warm cache from serving table (like a dashboard refresh)."""
    with pg_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT team, location, question_id, avg_score "
                    "FROM serving_team_scores WHERE avg_score IS NOT NULL")
        for team, loc, qid, score in cur.fetchall():
            CACHE[f"{team}|{loc}|{qid}"] = {"avg_score": float(score)}
        cur.close()
    print_panel("Cache: Pre-Warm", f"{len(CACHE)} entries in memory")


def api_query(team: str, location: str, question_id: str) -> dict:
    """Simulated tool call: cache -> serving table -> analytical fallback."""
    key = f"{team}|{location}|{question_id}"

    # Tier 1: In-memory cache
    t0 = time.perf_counter()
    if key in CACHE:
        return {"tier": "cache", "latency_ms": round((time.perf_counter()-t0)*1000, 3),
                **CACHE[key]}

    # Tier 2: PostgreSQL serving table
    t0 = time.perf_counter()
    with pg_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT avg_score FROM serving_team_scores "
                    "WHERE team=%s AND location=%s AND question_id=%s",
                    (team, location, question_id))
        row = cur.fetchone()
        cur.close()
    elapsed = (time.perf_counter() - t0) * 1000
    if row and row[0] is not None:
        CACHE[key] = {"avg_score": float(row[0])}
        return {"tier": "serving_table", "latency_ms": round(elapsed, 3), **CACHE[key]}

    # Tier 3: Analytical fallback (cold path)
    t0 = time.perf_counter()
    with duckdb_conn() as conn:
        elapsed = (time.perf_counter() - t0) * 1000
        return {"tier": "analytical_fallback", "latency_ms": round(elapsed, 3),
                "avg_score": None, "reason": "suppressed or no data"}


def trace_request():
    """Trace requests through all tiers and show latency comparison."""
    CACHE.clear()
    cache_hot_queries()

    # Evict one entry so the serving-table tier gets exercised
    evicted_key = next(iter(CACHE))
    evicted_parts = evicted_key.split("|")
    del CACHE[evicted_key]

    queries = [
        ("Engineering", "New York", "Q01", "cached (large group)"),
        ("Engineering", "New York", "Q01", "cache hit (repeat)"),
        (evicted_parts[0], evicted_parts[1], evicted_parts[2], "serving table (cache evicted)"),
        ("Product",     "Austin",   "Q01", "suppressed (n=3)"),
    ]
    results = []
    for team, loc, qid, note in queries:
        r = api_query(team, loc, qid)
        score = r.get("avg_score")
        results.append((f"{team}/{loc}", qid, r["tier"],
                        f"{r['latency_ms']:.3f}ms",
                        str(score) if score else "NULL (suppressed)", note))

    print_table("Request Trace: Cache -> Serving -> Analytical",
                ["Group", "Q", "Tier", "Latency", "Score", "Note"], results)

    print_panel("Architecture Summary",
        "Bronze -> Silver -> Gold -> Serving Table -> Cache -> API\n\n"
        "Production targets:  Cache <1ms | Serving <50ms | Analytical: seconds\n"
        "Cache vs analytical = ~500x speedup — why every platform pre-computes.")


def main():
    with duckdb_conn() as conn:
        ingest_bronze(conn)
        transform_silver(conn)
        aggregate_gold(conn)
        push_to_serving(conn)
    trace_request()


if __name__ == "__main__":
    main()
