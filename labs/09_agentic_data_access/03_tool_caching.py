"""Lab 03: Tool-Level Caching with TTL

Simulates a conversation where the agent makes multiple data requests.
Shows cache hits vs misses, latency impact, and why tool-level caching
guarantees intra-conversation consistency.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import time
from dataclasses import dataclass

from shared.display import print_panel, print_table

# ── Simulated Data Store ─────────────────────────────────────────────────────

TEAM_SCORES = {
    ("t1", "p1", "2025-Q2"): {"team": "Team Alpha", "score": 3.85, "responses": 18},
    ("t2", "p1", "2025-Q2"): {"team": "Team Beta",  "score": 3.35, "responses": 12},
    ("t1", "p1", "2025-Q1"): {"team": "Team Alpha", "score": 3.65, "responses": 16},
}

QUERY_LATENCY_MS = 50  # Simulated database query time


def simulate_db_query(team_id: str, project_id: str, period: str) -> dict | None:
    """Simulate a database query with realistic latency."""
    time.sleep(QUERY_LATENCY_MS / 1000)
    return TEAM_SCORES.get((team_id, project_id, period))


# ── Tool Cache ───────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    data: dict
    cached_at: float
    hits: int = 0

class ToolCache:
    """Simple dict-based cache with TTL for tool responses."""

    def __init__(self, ttl_seconds: float = 300):
        self.ttl = ttl_seconds
        self._store: dict[tuple, CacheEntry] = {}
        self.log: list[dict] = []  # Records every access for the timeline

    def get(self, key: tuple) -> dict | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() - entry.cached_at > self.ttl:
            del self._store[key]
            return None  # Expired
        entry.hits += 1
        return entry.data

    def put(self, key: tuple, data: dict):
        self._store[key] = CacheEntry(data=data, cached_at=time.time())

    @property
    def size(self):
        return len(self._store)


def get_team_overview_cached(cache: ToolCache, team_id: str, project_id: str, period: str) -> dict:
    """Tool function with caching layer."""
    key = (team_id, project_id, period)
    start = time.perf_counter()

    cached = cache.get(key)
    if cached is not None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        cache.log.append({"key": key, "hit": True, "ms": elapsed_ms})
        return cached

    # Cache miss — query the database
    result = simulate_db_query(team_id, project_id, period)
    elapsed_ms = (time.perf_counter() - start) * 1000

    if result:
        cache.put(key, result)
    else:
        result = {"team": "unknown", "score": 0, "responses": 0, "status": "no_data"}

    cache.log.append({"key": key, "hit": False, "ms": elapsed_ms})
    return result


# ── Simulated Conversation ───────────────────────────────────────────────────

def simulate_conversation():
    cache = ToolCache(ttl_seconds=300)

    print_panel("Simulated Agent Conversation", "Cache TTL: 300 seconds")

    # Turn 1: User asks about Team Alpha
    print('\n[User]: "How is Team Alpha doing on engagement?"')
    print("[Agent]: Let me look that up...")
    r1 = get_team_overview_cached(cache, "t1", "p1", "2025-Q2")
    print(f"  -> {r1['team']}: {r1['score']} ({r1['responses']} responses)")

    # Turn 2: Follow-up about same team (cache hit)
    print('\n[User]: "What about last quarter for comparison?"')
    print("[Agent]: Checking Q1 data...")
    r2 = get_team_overview_cached(cache, "t1", "p1", "2025-Q1")
    print(f"  -> {r2['team']}: {r2['score']} ({r2['responses']} responses)")

    # Turn 3: Back to Q2 (cache hit — same as Turn 1)
    print('\n[User]: "So the current quarter is better?"')
    print("[Agent]: Let me confirm the current numbers...")
    r3 = get_team_overview_cached(cache, "t1", "p1", "2025-Q2")
    print(f"  -> {r3['team']}: {r3['score']} (cache hit — same data as Turn 1)")

    # Turn 4: Different team (cache miss)
    print('\n[User]: "How does Team Beta compare?"')
    print("[Agent]: Looking up Team Beta...")
    r4 = get_team_overview_cached(cache, "t2", "p1", "2025-Q2")
    print(f"  -> {r4['team']}: {r4['score']} ({r4['responses']} responses)")

    # Turn 5: Back to Alpha again (still cached)
    print('\n[User]: "Remind me what Alpha\'s score was?"')
    r5 = get_team_overview_cached(cache, "t1", "p1", "2025-Q2")
    print(f"  -> {r5['team']}: {r5['score']} (cache hit)")

    return cache


def display_timeline(cache: ToolCache):
    """Show the cache hit/miss timeline."""
    rows = []
    for i, entry in enumerate(cache.log, 1):
        team_id, _proj_id, period = entry["key"]
        status = "HIT" if entry["hit"] else "MISS"
        latency = f"{entry['ms']:.2f} ms"
        rows.append((i, team_id, period, status, latency))

    print_table(
        "Cache Timeline",
        ["Call #", "Team", "Period", "Status", "Latency"],
        rows,
    )


def display_latency_comparison(cache: ToolCache):
    """Compare total latency with vs without caching."""
    total_calls = len(cache.log)
    hits = sum(1 for e in cache.log if e["hit"])
    misses = total_calls - hits

    actual_ms = sum(e["ms"] for e in cache.log)
    uncached_ms = total_calls * QUERY_LATENCY_MS  # Every call would be a miss

    print_table(
        "Latency Comparison",
        ["Metric", "Value"],
        [
            ("Total tool calls", total_calls),
            ("Cache hits", f"{hits} ({100*hits/total_calls:.0f}%)"),
            ("Cache misses", misses),
            ("Actual total latency", f"{actual_ms:.1f} ms"),
            ("Without caching (estimated)", f"{uncached_ms} ms"),
            ("Savings", f"{uncached_ms - actual_ms:.1f} ms ({100*(1-actual_ms/uncached_ms):.0f}%)"),
        ],
    )


if __name__ == "__main__":
    cache = simulate_conversation()
    print()
    display_timeline(cache)
    display_latency_comparison(cache)

    print_panel(
        "Key Insight",
        "Tool-level caching serves two purposes:\n"
        "1. Performance: repeated queries in one conversation skip the database\n"
        "2. Consistency: same team queried twice in one conversation\n"
        "   always returns the same numbers, even if the source data\n"
        "   refreshes between calls. The cache is the consistency boundary.",
    )
