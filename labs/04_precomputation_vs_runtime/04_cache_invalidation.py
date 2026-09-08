"""Lab 04: Cache Invalidation — Freshness vs Latency

Demonstrates a TTL-based cache that shows the fundamental tradeoff:
  (a) Fresh cache hit — fast AND correct
  (b) Stale after data change — fast but WRONG
  (c) Cache miss after expiry — slow but correct

Uses a simple dict cache with TTL to make the mechanics visible.
No external dependencies — the cache is just a Python dict with timestamps.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_table
from shared.duck import duckdb_conn

# ── Simple TTL cache ─────────────────────────────────────────────────

class TTLCache:
    """Minimal cache: key -> (value, expiry_time). Illustrative, not production."""

    def __init__(self, ttl_seconds: float):
        self.ttl = ttl_seconds
        self._store: dict = {}
        self.stats = {"hits": 0, "misses": 0}

    def get(self, key: str):
        if key in self._store:
            value, expiry = self._store[key]
            if time.time() < expiry:
                self.stats["hits"] += 1
                return value, "HIT"
            else:
                del self._store[key]
                self.stats["misses"] += 1
                return None, "EXPIRED"
        self.stats["misses"] += 1
        return None, "MISS"

    def put(self, key: str, value):
        self._store[key] = (value, time.time() + self.ttl)

    def peek(self, key: str):
        """Check what's cached without evicting — reveals staleness."""
        if key in self._store:
            value, expiry = self._store[key]
            remaining = expiry - time.time()
            return value, remaining
        return None, 0


def expensive_query(conn, team_name: str):
    """Simulate an expensive analytical query with a deliberate sleep."""
    time.sleep(0.05)  # Simulate 50ms query latency
    result = conn.execute("""
        SELECT dt.team_name, dq.category,
               ROUND(AVG(f.score), 2) AS mean_score, COUNT(*) AS n
        FROM fact_responses f
        JOIN dim_team dt     ON f.team_key = dt.team_key
        JOIN dim_question dq ON f.question_key = dq.question_key
        WHERE dt.team_name = ?
        GROUP BY dt.team_name, dq.category
        ORDER BY dq.category
    """, [team_name]).fetchall()
    return result


def cached_query(conn, cache: TTLCache, team_name: str):
    """Query with cache layer. Returns (result, source, latency_ms)."""
    start = time.perf_counter()
    value, status = cache.get(team_name)

    if value is not None:
        latency = (time.perf_counter() - start) * 1000
        return value, f"CACHE {status}", latency

    # Cache miss — run the expensive query
    result = expensive_query(conn, team_name)
    cache.put(team_name, result)
    latency = (time.perf_counter() - start) * 1000
    return result, f"CACHE {status} -> COMPUTED", latency


def setup_data(conn):
    conn.execute("CREATE TABLE dim_team (team_key INT, team_name VARCHAR, department VARCHAR)")
    conn.execute("INSERT INTO dim_team VALUES (1, 'Team_Alpha', 'Engineering')")
    conn.execute("INSERT INTO dim_team VALUES (2, 'Team_Beta', 'Product')")

    conn.execute("CREATE TABLE dim_question (question_key INT, qid VARCHAR, category VARCHAR, base FLOAT)")
    questions = [("Q01","Basic Needs",4.1),("Q03","Individual",3.7),("Q07","Teamwork",3.6),("Q11","Growth",3.3)]
    for i, (qid, cat, base) in enumerate(questions, 1):
        conn.execute("INSERT INTO dim_question VALUES (?,?,?,?)", [i, qid, cat, base])

    conn.execute("""
        CREATE TABLE fact_responses AS
        SELECT ROW_NUMBER() OVER () AS id, t.team_key, q.question_key,
               GREATEST(1.0, LEAST(5.0, ROUND(q.base + (RANDOM()-0.5)*0.8, 1))) AS score
        FROM dim_team t CROSS JOIN dim_question q
        CROSS JOIN generate_series(1, 30) AS r(n)
    """)
    return conn.execute("SELECT COUNT(*) FROM fact_responses").fetchone()[0]


def main():
    with duckdb_conn() as conn:
        count = setup_data(conn)
        cache = TTLCache(ttl_seconds=0.5)  # 500ms TTL — short for demo

        print_panel("Lab 04: Cache Invalidation",
                    f"TTL-based cache with {count} fact rows.\n"
                    f"Cache TTL: 500ms (short for demonstration).\n"
                    f"Shows the freshness-latency tradeoff in three scenarios.")

        # ── Scenario A: Cold miss, then warm hit ─────────────────────
        print_panel("SCENARIO A", "First query = cache miss (slow). Second query = cache hit (fast).")

        result_a1, source_a1, lat_a1 = cached_query(conn, cache, "Team_Alpha")
        _result_a2, source_a2, lat_a2 = cached_query(conn, cache, "Team_Alpha")

        timeline = []
        timeline.append(("A1: First query", source_a1, f"{lat_a1:.2f}", "Correct"))
        timeline.append(("A2: Repeat query", source_a2, f"{lat_a2:.2f}", "Correct"))

        print_table("Scenario A: Cache Miss -> Hit",
                    ["Step", "Source", "Latency (ms)", "Data Correct?"], timeline)

        print_table("A1 Query Result (computed)",
                    ["Team", "Category", "Mean Score", "N"], result_a1)

        speedup_a = lat_a1 / lat_a2 if lat_a2 > 0 else float("inf")
        print_panel("Observation", f"Cache hit is {speedup_a:.0f}x faster. Both return correct data.")

        # ── Scenario B: Data changes, cache is stale ─────────────────
        print_panel("SCENARIO B", "Data changes underneath the cache. Cache serves STALE data.")

        # Mutate underlying data — Team Alpha gets very high scores
        conn.execute("""
            UPDATE fact_responses SET score = 5.0
            WHERE team_key = 1 AND question_key = 1
        """)

        # Cache still has the old result
        result_b, source_b, lat_b = cached_query(conn, cache, "Team_Alpha")
        # What the real answer is now
        fresh_result = expensive_query(conn, "Team_Alpha")

        stale_basic = next(r for r in result_b if r[1] == "Basic Needs")[2]
        fresh_basic = next(r for r in fresh_result if r[1] == "Basic Needs")[2]

        timeline.append(("B: After data change", source_b, f"{lat_b:.2f}",
                         f"STALE (Basic Needs: cached={stale_basic}, actual={fresh_basic})"))

        print_table("Cached Result (STALE)", ["Team", "Category", "Mean Score", "N"], result_b)
        print_table("Fresh Query Result",    ["Team", "Category", "Mean Score", "N"], fresh_result)

        print_panel("Observation",
                    f"Cache returned Basic Needs = {stale_basic}, but actual is {fresh_basic}.\n"
                    f"The cache is FAST ({lat_b:.2f} ms) but WRONG.\n"
                    f"This is the fundamental cache invalidation problem.")

        # ── Scenario C: TTL expires, cache refreshes ─────────────────
        print_panel("SCENARIO C", "Wait for TTL to expire. Next query re-computes fresh data.")

        time.sleep(0.6)  # Wait for 500ms TTL to expire

        result_c, source_c, lat_c = cached_query(conn, cache, "Team_Alpha")
        next(r for r in result_c if r[1] == "Basic Needs")[2]

        timeline.append(("C: After TTL expiry", source_c, f"{lat_c:.2f}", "Correct (recomputed)"))

        print_table("Result After Expiry", ["Team", "Category", "Mean Score", "N"], result_c)

        # ── Full timeline ────────────────────────────────────────────
        print_table("Complete Timeline",
                    ["Step", "Source", "Latency (ms)", "Data Correct?"], timeline)

        print_panel("Key Takeaway",
                    "Every cache has three states:\n\n"
                    "  FRESH HIT  — fast + correct  (best case)\n"
                    "  STALE HIT  — fast + WRONG    (the dangerous case)\n"
                    "  MISS       — slow + correct  (cold start or after expiry)\n\n"
                    "The TTL controls the tradeoff:\n"
                    "  Short TTL (seconds)  → more misses, fresher data\n"
                    "  Long TTL (hours)     → more hits, staler data\n\n"
                    "Strategies beyond TTL:\n"
                    "  - Event-driven invalidation: clear cache when data changes\n"
                    "  - Versioned keys: include data version in cache key\n"
                    "  - Write-through: update cache and DB together\n\n"
                    "For survey data: surveys are batched (quarterly), so a long TTL\n"
                    "with explicit invalidation after each survey close is ideal.\n"
                    "'There are only two hard things in CS: cache invalidation\n"
                    " and naming things.' — Phil Karlton")


if __name__ == "__main__":
    main()
