"""Lab 04: Sparse Wide Table

Every possible question gets its own column. Projects that don't use a
question leave it NULL. In columnar storage, NULLs compress to near-zero.
Simplest queries, best optimizer support, most AI-friendly.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

import random
random.seed(42)

Q12 = [
    ("q01_expectations", 4.1), ("q02_materials", 3.9),
    ("q03_do_best", 3.7),      ("q04_recognition", 3.4),
    ("q05_cares", 3.8),        ("q06_development", 3.5),
    ("q07_opinions", 3.6),     ("q08_mission", 3.7),
    ("q09_quality", 3.8),      ("q10_best_friend", 3.0),
    ("q11_progress", 3.3),     ("q12_learn_grow", 3.5),
]

CUSTOM_COLS = {
    "Phoenix": [("crw01_remote_tools", 3.6), ("crw02_remote_connection", 3.2)],
    "Atlas":   [("cld01_leadership_vision", 3.4), ("cld02_trust_leadership", 3.1),
                ("cld03_leadership_programs", 3.3), ("cld04_actionable_feedback", 3.5),
                ("cld05_values_modeled", 3.2)],
    "Orbit":   [("csf01_physical_safety", 4.2), ("csf02_safety_response", 3.9),
                ("csf03_safety_training", 3.7)],
}

ALL_CUSTOM = []
for qs in CUSTOM_COLS.values():
    for col, base in qs:
        if col not in [c for c, _ in ALL_CUSTOM]:
            ALL_CUSTOM.append((col, base))

ALL_COLS = Q12 + ALL_CUSTOM  # 22 total question columns
TEAMS = {"Alpha": 0.2, "Beta": -0.1, "Gamma": 0.0}


def clamp(v, lo=1.0, hi=5.0):
    return max(lo, min(hi, round(v, 1)))


def build_and_load(conn):
    col_defs = ", ".join(f"{col} DECIMAL(2,1)" for col, _ in ALL_COLS)
    conn.execute(f"""
        CREATE TABLE wide_responses (
            response_id INTEGER,
            project     VARCHAR,
            team        VARCHAR,
            {col_defs}
        )
    """)

    # Track which columns each project populates
    project_cols = {}
    for project, custom_qs in CUSTOM_COLS.items():
        cols = [c for c, _ in Q12] + [c for c, _ in custom_qs]
        project_cols[project] = set(cols)

    rid = 0
    for project, custom_qs in CUSTOM_COLS.items():
        active = project_cols[project]
        for team, adj in TEAMS.items():
            for _ in range(5):
                rid += 1
                values = []
                for col, base in ALL_COLS:
                    if col in active:
                        values.append(clamp(base + adj + random.gauss(0, 0.3)))
                    else:
                        values.append(None)
                placeholders = ", ".join("?" for _ in ALL_COLS)
                conn.execute(
                    f"INSERT INTO wide_responses VALUES (?, ?, ?, {placeholders})",
                    [rid, project, team] + values,
                )

    # Show sparsity pattern
    sql = """
SELECT project,
       COUNT(*) AS rows,
       COUNT(q01_expectations) AS q01_filled,
       COUNT(crw01_remote_tools) AS crw01_filled,
       COUNT(cld01_leadership_vision) AS cld01_filled,
       COUNT(csf01_physical_safety) AS csf01_filled
FROM wide_responses
GROUP BY project ORDER BY project;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Sparsity Pattern",
                ["Project", "Rows", "Q01 (all)", "CRW01 (Phoenix)", "CLD01 (Atlas)", "CSF01 (Orbit)"],
                rows)


def simple_queries(conn):
    """Show how simple analytical queries are with named columns."""
    print_panel("Simple Queries", "No joins, no JSON extraction, no PIVOT — just column names")

    sql1 = """
SELECT
    team,
    ROUND(AVG(q01_expectations), 2) AS q01_avg,
    ROUND(AVG(q05_cares), 2)        AS q05_avg,
    ROUND(AVG(q10_best_friend), 2)  AS q10_avg
FROM wide_responses
WHERE project = 'Phoenix'
GROUP BY team ORDER BY team;
"""
    print_sql(sql1)
    rows = conn.execute(sql1).fetchall()
    print_table("Mean by Team (Phoenix)", ["Team", "Q01 Avg", "Q05 Avg", "Q10 Avg"], rows)

    # Custom questions — just as simple
    sql2 = """
SELECT
    team,
    ROUND(AVG(crw01_remote_tools), 2)    AS remote_tools,
    ROUND(AVG(crw02_remote_connection), 2) AS remote_connect
FROM wide_responses
WHERE project = 'Phoenix'
GROUP BY team ORDER BY team;
"""
    print_panel("Custom Questions", "Same simplicity for project-specific questions")
    print_sql(sql2)
    rows = conn.execute(sql2).fetchall()
    print_table("Phoenix Custom Qs", ["Team", "Remote Tools", "Remote Connection"], rows)


def null_compression(conn):
    """Demonstrate that NULLs are cheap in columnar storage."""
    print_panel("NULL Compression in Columnar Storage",
                "Columnar engines store each column independently.\n"
                "A column of all NULLs compresses to near-zero bytes.")

    # Export to Parquet and check size
    conn.execute("COPY wide_responses TO '/tmp/wide_sparse.parquet' (FORMAT PARQUET)")

    # Compare: create a version with only populated columns for Phoenix
    conn.execute("""
        CREATE TABLE phoenix_only AS
        SELECT response_id, project, team,
               q01_expectations, q02_materials, q03_do_best, q04_recognition,
               q05_cares, q06_development, q07_opinions, q08_mission,
               q09_quality, q10_best_friend, q11_progress, q12_learn_grow,
               crw01_remote_tools, crw02_remote_connection
        FROM wide_responses WHERE project = 'Phoenix'
    """)
    conn.execute("COPY phoenix_only TO '/tmp/phoenix_only.parquet' (FORMAT PARQUET)")

    import os
    wide_size = os.path.getsize("/tmp/wide_sparse.parquet")
    phoenix_size = os.path.getsize("/tmp/phoenix_only.parquet")

    print_table("Storage Comparison",
                ["Table", "Columns", "Rows", "Parquet Size"],
                [("wide_responses (all projects)", f"{len(ALL_COLS)} question cols", "45", f"{wide_size:,} bytes"),
                 ("phoenix_only (no NULLs)", "14 question cols", "15", f"{phoenix_size:,} bytes")])

    print_panel("Observation",
                f"Wide table with {len(ALL_COLS)} columns (many NULL): {wide_size:,} bytes\n"
                f"Phoenix-only with 14 columns (no NULLs): {phoenix_size:,} bytes\n"
                f"The 8 extra mostly-NULL columns add minimal overhead in Parquet.\n"
                f"In row-stores, those NULLs would cost significantly more.")


def ai_friendly(conn):
    """Show why this is the best approach for AI/text-to-SQL."""
    print_panel("AI-Friendly: Column Names Are the Schema",
                "An AI agent can read column names and write correct SQL directly.\n"
                "Compare: 'AVG(q01_expectations)' vs EAV's CASE-WHEN PIVOT vs\n"
                "JSON's json_extract(responses, '$.Q01')")

    # Show what column metadata looks like to an AI
    sql = """
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'wide_responses'
  AND (column_name LIKE 'q%' OR column_name LIKE 'c%')
ORDER BY ordinal_position;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Schema Metadata (what an AI sees)",
                ["Column Name", "Data Type", "Nullable"], rows)


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 04: Sparse Wide Table",
                    "Every question gets its own named column.\n"
                    "22 question columns total — each project populates 12-17, rest NULL.")
        build_and_load(conn)
        simple_queries(conn)
        null_compression(conn)
        ai_friendly(conn)

        print_panel("Key Takeaway",
                    "Sparse wide tables have the simplest queries and best AI compatibility.\n"
                    "Column names carry semantic meaning: q01_expectations, not slot_7.\n"
                    "NULLs are free in columnar storage (Parquet, DuckDB, Snowflake).\n"
                    "Tradeoff: adding new questions requires ALTER TABLE ADD COLUMN.")


if __name__ == "__main__":
    main()
