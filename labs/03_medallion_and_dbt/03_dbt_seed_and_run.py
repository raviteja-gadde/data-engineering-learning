"""Lab 03 -- dbt Seed, Run, and Test

Executes the full dbt pipeline: seed -> run -> test,
then queries DuckDB to inspect data at each layer.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_table
from shared.duck import duckdb_conn

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "dbt_project"
DB_PATH = PROJECT_DIR / "dev.duckdb"


def run_dbt(command: str) -> tuple[int, str]:
    """Run a dbt command and return (exit_code, combined_output)."""
    result = subprocess.run(
        f"uv run dbt {command} --profiles-dir .",
        shell=True, capture_output=True, text=True, cwd=PROJECT_DIR, check=False,
    )
    return result.returncode, result.stdout + result.stderr


def main():
    print_panel(
        "Lab 03: Running the Full dbt Pipeline",
        "Three dbt commands in sequence:\n\n"
        "  dbt seed  -- load CSV files into DuckDB as tables\n"
        "  dbt run   -- build all models (bronze -> silver -> gold)\n"
        "  dbt test  -- run data quality assertions\n\n"
        "Then we connect to DuckDB directly and inspect each layer."
    )

    # ── Step 1: dbt seed ───────────────────────────────────────────
    print_panel("Step 1: dbt seed", "Loading CSV seed files into DuckDB...")
    code, output = run_dbt("seed")
    if code != 0:
        print_panel("SEED FAILED", output[-500:])
        return
    seed_lines = [l.strip() for l in output.split("\n") if "OK" in l]
    print_panel("Seed Results", "\n".join(seed_lines) or "Seeds loaded.")

    # ── Step 2: dbt run ────────────────────────────────────────────
    print_panel("Step 2: dbt run", "Building models: bronze views -> silver tables -> gold tables...")
    code, output = run_dbt("run")
    if code != 0:
        print_panel("RUN FAILED", output[-500:])
        return
    run_lines = [l.strip() for l in output.split("\n") if "OK" in l or "SUCCESS" in l]
    print_panel("Run Results", "\n".join(run_lines[-10:]) or "All models built.")

    # ── Step 3: dbt test ───────────────────────────────────────────
    print_panel("Step 3: dbt test", "Running data quality tests...")
    code, output = run_dbt("test")
    test_lines = [l.strip() for l in output.split("\n") if "Pass" in l or "Fail" in l or "pass=" in l.lower()]
    print_panel(
        "Test Results",
        "\n".join(test_lines[-12:]) or ("All tests passed." if code == 0 else output[-500:])
    )

    # ── Step 4: Query each layer ───────────────────────────────────
    print_panel("Step 4: Inspecting the Data", "Connecting to dev.duckdb to query each layer...")

    with duckdb_conn(str(DB_PATH)) as con:
        # Bronze
        rows = con.execute("""
            SELECT response_id, respondent_id, team_id, question_id, score
            FROM stg_survey_responses LIMIT 5
        """).fetchall()
        print_table(
            "Bronze: stg_survey_responses (first 5)",
            ["response_id", "respondent_id", "team_id", "question_id", "score"],
            rows,
        )

        # Silver
        rows = con.execute("""
            SELECT response_id, team_id, question_id, category,
                   raw_score, score, is_reverse_scored
            FROM survey_responses_cleaned LIMIT 5
        """).fetchall()
        print_table(
            "Silver: survey_responses_cleaned (first 5)",
            ["response_id", "team_id", "question_id", "category",
             "raw_score", "score", "is_reverse_scored"],
            rows,
        )

        rows = con.execute("SELECT * FROM suppression_flags ORDER BY team_id").fetchall()
        print_table(
            "Silver: suppression_flags",
            ["team_id", "respondent_count", "is_suppressed"],
            rows,
        )

        # Gold
        rows = con.execute("""
            SELECT team_id, team_name, category, mean_score,
                   response_count, is_suppressed
            FROM team_overview_metrics ORDER BY team_id, category LIMIT 10
        """).fetchall()
        print_table(
            "Gold: team_overview_metrics (first 10)",
            ["team_id", "team_name", "category", "mean_score",
             "response_count", "is_suppressed"],
            rows,
        )

        # ── Row count summary ──────────────────────────────────────
        tables = [
            ("Seeds", "raw_survey_responses"),
            ("Seeds", "raw_question_config"),
            ("Seeds", "raw_team_hierarchy"),
            ("Bronze", "stg_survey_responses"),
            ("Silver", "survey_responses_cleaned"),
            ("Silver", "suppression_flags"),
            ("Gold", "team_overview_metrics"),
            ("Gold", "category_summary"),
        ]
        count_rows = []
        for layer, table in tables:
            (cnt,) = con.execute(f"SELECT count(*) FROM {table}").fetchone()
            count_rows.append((layer, table, cnt))

        print_table("Row Counts by Layer", ["Layer", "Table", "Rows"], count_rows)

        seed_n = int(count_rows[0][2])
        silver_n = int(count_rows[4][2])
        gold_n = int(count_rows[6][2])

    print_panel(
        "Summary",
        f"Seeds: {seed_n} raw survey rows (including duplicates)\n"
        f"Silver: {silver_n} cleaned rows (deduped, validated, reverse-scaled)\n"
        f"Gold: {gold_n} team-category metric rows\n\n"
        f"Reduction: {seed_n} -> {silver_n} -> {gold_n}\n"
        f"  {seed_n - silver_n} rows removed by dedup + validation\n"
        f"  {silver_n} rows aggregated into {gold_n} pre-computed metrics\n\n"
        "Consumers query Gold -- no joins, no aggregations, dashboard-ready."
    )


if __name__ == "__main__":
    main()
