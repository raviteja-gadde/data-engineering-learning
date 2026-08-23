"""Lab 03: Predicate Pushdown and Late Materialization

Builds a large DuckDB table, then compares queries with and without WHERE
clauses. EXPLAIN ANALYZE shows how the engine skips data it doesn't need,
especially when the table is sorted on the filter column.
"""

import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

random.seed(42)
NUM_ROWS = 1_000_000
TEAMS = ["Engineering", "Sales", "Product", "Support", "Marketing",
         "Finance", "Legal", "HR", "Operations", "Research"]


def build_tables(conn):
    """Create an unsorted and a sorted version of the same data."""
    # Generate data via SQL for speed
    conn.execute(f"""
        CREATE TABLE responses_unsorted AS
        SELECT
            row_number() OVER () AS response_id,
            (LIST_VALUE{tuple(TEAMS)})[1 + (row_number() OVER () % {len(TEAMS)})] AS team_id,
            1 + (hash(row_number() OVER ()) % 5)::INTEGER AS q01,
            1 + (hash(row_number() OVER () + 1) % 5)::INTEGER AS q02,
            1 + (hash(row_number() OVER () + 2) % 5)::INTEGER AS q03,
            1 + (hash(row_number() OVER () + 3) % 5)::INTEGER AS q04
        FROM range({NUM_ROWS})
    """)

    # Sorted copy — physically ordered by team_id
    conn.execute("""
        CREATE TABLE responses_sorted AS
        SELECT * FROM responses_unsorted
        ORDER BY team_id
    """)


def explain_query(conn, sql, label):
    """Run EXPLAIN ANALYZE and print the plan."""
    print_sql(sql)
    plan = conn.execute(f"EXPLAIN ANALYZE {sql}").fetchall()
    plan_text = "\n".join(str(r[1]) for r in plan)
    print_panel(label, plan_text)
    return plan_text


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 03: Predicate Pushdown",
                    f"Building {NUM_ROWS:,} rows in two tables:\n"
                    "- responses_unsorted: random row order\n"
                    "- responses_sorted: physically sorted by team_id\n\n"
                    "Predicate pushdown lets the engine skip data blocks that\n"
                    "can't match the WHERE clause. Sorting maximizes this effect.")

        build_tables(conn)

        # Verify row counts and team distribution
        dist = conn.execute("""
            SELECT team_id, COUNT(*) AS n
            FROM responses_sorted
            GROUP BY team_id ORDER BY team_id
        """).fetchall()
        print_table("Team Distribution", ["Team", "Rows"], dist)

        # ── Query 1: Full scan (no WHERE) ──
        print_panel("TEST 1", "Full table scan — no predicate, reads everything")
        explain_query(conn,
            "SELECT AVG(q01) FROM responses_sorted",
            "Full scan — baseline")

        # ── Query 2: Filtered on sorted column ──
        print_panel("TEST 2",
                    "Filtered on sorted column (team_id). Because the table is sorted\n"
                    "by team_id, all 'Engineering' rows are contiguous. DuckDB can\n"
                    "skip entire row groups that don't contain 'Engineering'.")
        explain_query(conn,
            "SELECT AVG(q01) FROM responses_sorted WHERE team_id = 'Engineering'",
            "Predicate on SORTED column (team_id) — expect row group skipping")

        # ── Query 3: Same filter on unsorted table ──
        print_panel("TEST 3",
                    "Same filter on UNSORTED table. 'Engineering' rows are scattered\n"
                    "across all row groups, so fewer groups can be skipped.")
        explain_query(conn,
            "SELECT AVG(q01) FROM responses_unsorted WHERE team_id = 'Engineering'",
            "Predicate on UNSORTED table — less effective pruning")

        # ── Query 4: Late materialization demo ──
        print_panel("TEST 4",
                    "Late materialization: the engine reads team_id first to find\n"
                    "matching positions, then reads q01-q04 only for those rows.\n"
                    "With 10 teams, ~90% of rows are skipped for the other columns.")
        explain_query(conn,
            """SELECT team_id, AVG(q01), AVG(q02), AVG(q03), AVG(q04)
               FROM responses_sorted
               WHERE team_id = 'Engineering'
               GROUP BY team_id""",
            "Late materialization — reads q01-q04 only for matching rows")

        # ── Timing comparison ──
        import time

        timings = []
        for label, sql in [
            ("No filter (full scan)",
             "SELECT AVG(q01) FROM responses_sorted"),
            ("Filter on sorted col",
             "SELECT AVG(q01) FROM responses_sorted WHERE team_id = 'Engineering'"),
            ("Filter on unsorted",
             "SELECT AVG(q01) FROM responses_unsorted WHERE team_id = 'Engineering'"),
        ]:
            times = []
            for _ in range(5):
                start = time.perf_counter()
                conn.execute(sql).fetchall()
                times.append((time.perf_counter() - start) * 1000)
            times.sort()
            median = times[len(times) // 2]
            timings.append((label, f"{median:.2f} ms"))

        print_table("Execution Time Comparison (median of 5 runs)",
                    ["Query", "Time"], timings)

        print_panel("Key Takeaway",
                    "Predicate pushdown skips data blocks whose min/max metadata\n"
                    "proves no rows can match the WHERE clause.\n\n"
                    "Sorting the table by the filter column clusters matching rows\n"
                    "together, maximizing the number of blocks that can be skipped.\n\n"
                    "This is the same principle behind Snowflake's clustering keys:\n"
                    "physically organize data by the columns you filter most often.\n\n"
                    "Late materialization adds another layer: even for matching blocks,\n"
                    "the engine reads the filter column first, then fetches other\n"
                    "columns only for rows that pass the predicate.")


if __name__ == "__main__":
    main()
