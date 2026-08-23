"""Lab 04 -- Incremental Loading with dbt

Demonstrates what happens when new data arrives:
1. Start fresh -- seed + run with original data
2. Append new survey responses to the seed CSV
3. Re-seed + re-run -- dbt rebuilds tables with the new data
4. Compare before/after metrics to see the aggregation update

Since models use 'table' materialization, dbt does a full rebuild each run.
The lab ends with an explanation of how incremental materialization differs.
"""

import sys, os, subprocess, shutil
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_panel, print_sql

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "dbt_project"
DB_PATH = PROJECT_DIR / "dev.duckdb"
SEED_CSV = PROJECT_DIR / "seeds" / "raw_survey_responses.csv"
BACKUP_CSV = SEED_CSV.with_suffix(".csv.bak")

# 24 new rows: two new employees (EMP10 on T001, EMP11 on T002), all 12 questions.
# Submitted a month later than the original data.
NEW_ROWS = """\
R109,EMP10,T001,P01,Q01,5,2025-04-15 09:00:00
R110,EMP10,T001,P01,Q02,4,2025-04-15 09:01:00
R111,EMP10,T001,P01,Q03,5,2025-04-15 09:01:00
R112,EMP10,T001,P01,Q04,4,2025-04-15 09:02:00
R113,EMP10,T001,P01,Q05,5,2025-04-15 09:02:00
R114,EMP10,T001,P01,Q06,3,2025-04-15 09:03:00
R115,EMP10,T001,P01,Q07,4,2025-04-15 09:03:00
R116,EMP10,T001,P01,Q08,5,2025-04-15 09:04:00
R117,EMP10,T001,P01,Q09,4,2025-04-15 09:04:00
R118,EMP10,T001,P01,Q10,3,2025-04-15 09:05:00
R119,EMP10,T001,P01,Q11,4,2025-04-15 09:05:00
R120,EMP10,T001,P01,Q12,5,2025-04-15 09:06:00
R121,EMP11,T002,P02,Q01,3,2025-04-16 10:00:00
R122,EMP11,T002,P02,Q02,4,2025-04-16 10:01:00
R123,EMP11,T002,P02,Q03,3,2025-04-16 10:01:00
R124,EMP11,T002,P02,Q04,4,2025-04-16 10:02:00
R125,EMP11,T002,P02,Q05,3,2025-04-16 10:02:00
R126,EMP11,T002,P02,Q06,2,2025-04-16 10:03:00
R127,EMP11,T002,P02,Q07,3,2025-04-16 10:03:00
R128,EMP11,T002,P02,Q08,4,2025-04-16 10:04:00
R129,EMP11,T002,P02,Q09,3,2025-04-16 10:04:00
R130,EMP11,T002,P02,Q10,2,2025-04-16 10:05:00
R131,EMP11,T002,P02,Q11,3,2025-04-16 10:05:00
R132,EMP11,T002,P02,Q12,4,2025-04-16 10:06:00
"""


def run_dbt(command: str) -> tuple[int, str]:
    result = subprocess.run(
        f"uv run dbt {command} --profiles-dir .",
        shell=True, capture_output=True, text=True, cwd=PROJECT_DIR,
    )
    return result.returncode, result.stdout + result.stderr


def get_metrics(db: str) -> list[tuple]:
    """Return team_id, category, mean_score, response_count from gold."""
    with duckdb_conn(db) as con:
        return con.execute("""
            SELECT team_id, category, mean_score, response_count
            FROM team_overview_metrics ORDER BY team_id, category
        """).fetchall()


def main():
    print_panel(
        "Lab 04: New Data Arrives -- What Happens?",
        "We will:\n"
        "1. Start fresh with the original 111 seed rows\n"
        "2. Append 24 new survey responses (2 new employees)\n"
        "3. Re-run dbt and see how Gold aggregations update\n\n"
        "This demonstrates 'full refresh' -- dbt rebuilds every table."
    )

    # ── Step 1: Clean start ────────────────────────────────────────
    print_panel("Step 1: Clean Database", "Deleting dev.duckdb and rebuilding from scratch...")
    if DB_PATH.exists():
        DB_PATH.unlink()

    # Back up original CSV
    shutil.copy2(SEED_CSV, BACKUP_CSV)

    code, _ = run_dbt("seed")
    if code != 0:
        print_panel("SEED FAILED", "Check dbt output.")
        return
    code, _ = run_dbt("run")
    if code != 0:
        print_panel("RUN FAILED", "Check dbt output.")
        return

    before = get_metrics(str(DB_PATH))
    with duckdb_conn(str(DB_PATH)) as con:
        (before_total,) = con.execute("SELECT count(*) FROM survey_responses_cleaned").fetchone()

    print_table(
        f"BEFORE: Gold Metrics ({before_total} silver rows)",
        ["team_id", "category", "mean_score", "response_count"],
        before,
    )

    # ── Step 2: Append new data ────────────────────────────────────
    print_panel(
        "Step 2: New Data Arrives",
        "Appending 24 new survey responses to the seed CSV:\n"
        "  EMP10 joins T001 (Engineering Alpha) -- 12 questions\n"
        "  EMP11 joins T002 (Engineering Beta)  -- 12 questions\n\n"
        "In production, new data would arrive via an ingestion pipeline.\n"
        "Here, we simulate it by appending rows to the seed CSV."
    )

    with open(SEED_CSV, "a") as f:
        f.write(NEW_ROWS)

    with open(SEED_CSV) as f:
        new_count = sum(1 for _ in f) - 1  # subtract header
    print_panel("CSV Updated", f"Seed CSV now has {new_count} rows (was 111).")

    # ── Step 3: Re-run dbt ─────────────────────────────────────────
    print_panel("Step 3: Re-run Pipeline", "Running dbt seed + dbt run with the updated CSV...")
    code, _ = run_dbt("seed")
    code2, _ = run_dbt("run")
    if code != 0 or code2 != 0:
        shutil.copy2(BACKUP_CSV, SEED_CSV)
        BACKUP_CSV.unlink()
        print_panel("FAILED", "dbt commands failed. Original CSV restored.")
        return

    after = get_metrics(str(DB_PATH))
    with duckdb_conn(str(DB_PATH)) as con:
        (after_total,) = con.execute("SELECT count(*) FROM survey_responses_cleaned").fetchone()

    print_table(
        f"AFTER: Gold Metrics ({after_total} silver rows)",
        ["team_id", "category", "mean_score", "response_count"],
        after,
    )

    # ── Step 4: Compare ────────────────────────────────────────────
    print_panel("Step 4: What Changed?", "Comparing before/after for affected teams...")
    before_map = {(r[0], r[1]): r for r in before}
    diff_rows = []
    for row in after:
        key = (row[0], row[1])
        if key in before_map:
            old = before_map[key]
            if old[2] != row[2] or old[3] != row[3]:
                diff_rows.append((row[0], row[1], old[2], row[2], old[3], row[3]))
    print_table(
        "Changed Metrics (T001, T002 got new respondents)",
        ["team_id", "category", "old_mean", "new_mean", "old_count", "new_count"],
        diff_rows,
    )

    # ── Restore original CSV ───────────────────────────────────────
    shutil.copy2(BACKUP_CSV, SEED_CSV)
    BACKUP_CSV.unlink()
    # Rebuild with original data so project stays clean
    run_dbt("seed")
    run_dbt("run")
    print_panel("Cleanup", "Original CSV restored. Database rebuilt with original data.")

    # ── Incremental explanation ─────────────────────────────────────
    print_panel(
        "Full Refresh vs. Incremental",
        "What we just did was a FULL REFRESH -- dbt rebuilt every table\n"
        "from scratch each time. Simple, correct, but expensive at scale.\n\n"
        "INCREMENTAL materialization processes only new/changed rows:\n\n"
        "  Full Refresh:  DROP TABLE + CREATE TABLE AS (SELECT * FROM ...)\n"
        "  Incremental:   INSERT INTO ... SELECT * WHERE submitted_at > last_run\n\n"
        "To make a model incremental, you'd change the SQL like this:"
    )

    print_sql("""
-- survey_responses_cleaned.sql (incremental version)
{{ config(materialized='incremental', unique_key='response_id') }}

WITH deduped AS (
    SELECT *, row_number() OVER (
        PARTITION BY response_id ORDER BY submitted_at DESC
    ) AS row_num
    FROM {{ ref('stg_survey_responses') }}

    -- This block only activates on incremental runs (not first run)
    {% if is_incremental() %}
    WHERE submitted_at > (SELECT max(submitted_at) FROM {{ this }})
    {% endif %}
)
SELECT ... FROM deduped WHERE row_num = 1
    """)

    print_panel(
        "When to Use Each",
        "Full Refresh (materialized='table'):\n"
        "  + Simple, always correct, easy to debug\n"
        "  + Good for: small tables, reference data, Gold aggregates\n"
        "  - Slow on large tables (millions of rows)\n\n"
        "Incremental (materialized='incremental'):\n"
        "  + Fast: only processes new data\n"
        "  + Good for: large fact tables, append-heavy data\n"
        "  - More complex: must handle late-arriving data, schema changes\n"
        "  - Need 'dbt run --full-refresh' occasionally to fix drift\n\n"
        "Rule of thumb: start with 'table', switch to 'incremental'\n"
        "when rebuild time becomes a problem."
    )


if __name__ == "__main__":
    main()
