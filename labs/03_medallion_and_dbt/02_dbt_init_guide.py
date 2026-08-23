"""Lab 02 — dbt Project Setup Guide

Walks through the dbt project structure, explains each file,
verifies configuration, and runs dbt debug to confirm connectivity.
"""

import sys, os, subprocess
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_table

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "dbt_project"


def check_file(path: Path, description: str) -> bool:
    """Check if a file exists and print its status."""
    exists = path.exists()
    status = "EXISTS" if exists else "MISSING"
    return exists


def run_dbt(command: str) -> tuple[int, str]:
    """Run a dbt command in the project directory."""
    result = subprocess.run(
        f"uv run dbt {command} --profiles-dir .",
        shell=True, capture_output=True, text=True, cwd=PROJECT_DIR,
    )
    return result.returncode, result.stdout + result.stderr


def main():
    print_panel(
        "What Is dbt?",
        "dbt (data build tool) is 'make' for your data warehouse.\n\n"
        "It compiles SQL templates into executable queries and runs them\n"
        "against your warehouse in dependency order. It does NOT move data --\n"
        "it transforms data already in the warehouse.\n\n"
        "Think of it as: you write SELECT statements, dbt turns them into\n"
        "CREATE TABLE / CREATE VIEW statements and runs them for you."
    )

    # ── Check project structure ──────────────────────────────────
    print_panel(
        "dbt Project Structure",
        "dbt_project/\n"
        "  dbt_project.yml   -- Project config: name, paths, materializations\n"
        "  profiles.yml      -- Connection config: which database, how to connect\n"
        "  models/\n"
        "    bronze/          -- Staging models (light type casting)\n"
        "    silver/          -- Business rules (dedup, reverse-scale, suppression)\n"
        "    gold/            -- Pre-aggregated dashboard-ready tables\n"
        "  seeds/             -- CSV files loaded as tables via 'dbt seed'\n"
        "  tests/             -- Custom test SQL (built-in tests live in schema.yml)"
    )

    files_to_check = [
        (PROJECT_DIR / "dbt_project.yml", "Project configuration"),
        (PROJECT_DIR / "profiles.yml", "Connection profile (dbt-duckdb)"),
        (PROJECT_DIR / "seeds" / "raw_survey_responses.csv", "Survey response seed data"),
        (PROJECT_DIR / "seeds" / "raw_question_config.csv", "Question config seed data"),
        (PROJECT_DIR / "seeds" / "raw_team_hierarchy.csv", "Team hierarchy seed data"),
        (PROJECT_DIR / "models" / "bronze" / "stg_survey_responses.sql", "Bronze: staged responses"),
        (PROJECT_DIR / "models" / "bronze" / "stg_question_config.sql", "Bronze: staged questions"),
        (PROJECT_DIR / "models" / "bronze" / "stg_team_hierarchy.sql", "Bronze: staged teams"),
        (PROJECT_DIR / "models" / "silver" / "survey_responses_cleaned.sql", "Silver: cleaned responses"),
        (PROJECT_DIR / "models" / "silver" / "team_hierarchy_scd.sql", "Silver: team dimension"),
        (PROJECT_DIR / "models" / "silver" / "suppression_flags.sql", "Silver: suppression"),
        (PROJECT_DIR / "models" / "gold" / "team_overview_metrics.sql", "Gold: team metrics"),
        (PROJECT_DIR / "models" / "gold" / "category_summary.sql", "Gold: category summary"),
        (PROJECT_DIR / "models" / "bronze" / "schema.yml", "Bronze tests & docs"),
        (PROJECT_DIR / "models" / "silver" / "schema.yml", "Silver tests & docs"),
        (PROJECT_DIR / "models" / "gold" / "schema.yml", "Gold tests & docs"),
    ]

    rows = []
    all_ok = True
    for path, desc in files_to_check:
        exists = check_file(path, desc)
        rows.append((desc, path.relative_to(PROJECT_DIR), "OK" if exists else "MISSING"))
        if not exists:
            all_ok = False

    print_table("Project File Check", ["Description", "Path", "Status"], rows)

    if not all_ok:
        print_panel("ERROR", "Some files are missing. Create them before continuing.")
        return

    # ── Explain key config files ─────────────────────────────────
    print_panel(
        "dbt_project.yml -- What Each Key Means",
        "name: 'survey_analytics'        -- Project name (used in ref paths)\n"
        "config-version: 2               -- Config format version\n"
        "profile: 'survey_analytics'     -- Which profile in profiles.yml to use\n"
        "model-paths: ['models']         -- Where to find SQL model files\n"
        "seed-paths: ['seeds']           -- Where to find CSV seed files\n\n"
        "models:\n"
        "  survey_analytics:\n"
        "    bronze:\n"
        "      +materialized: view       -- Bronze = views (cheap, always current)\n"
        "    silver:\n"
        "      +materialized: table      -- Silver = tables (persisted, fast to query)\n"
        "    gold:\n"
        "      +materialized: table      -- Gold = tables (pre-computed aggregates)"
    )

    print_panel(
        "profiles.yml -- Database Connection",
        "survey_analytics:               -- Must match 'profile' in dbt_project.yml\n"
        "  target: dev                   -- Active target (dev/prod/staging)\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: duckdb              -- Adapter type\n"
        "      path: './dev.duckdb'      -- Local DuckDB file\n"
        "      schema: main              -- Default schema\n"
        "      threads: 4                -- Parallel model execution"
    )

    print_panel(
        "Models -- How SQL Files Become Tables",
        "Each .sql file in models/ contains a SELECT statement.\n"
        "dbt wraps it: CREATE TABLE <name> AS (SELECT ...)\n\n"
        "Dependencies are declared with {{ ref('other_model') }}.\n"
        "dbt builds a DAG and runs models in the right order:\n"
        "  seeds -> bronze (views) -> silver (tables) -> gold (tables)\n\n"
        "You never write CREATE TABLE yourself. You never specify run order."
    )

    print_panel(
        "Seeds -- CSV Files as Tables",
        "CSV files in seeds/ are loaded into the warehouse by 'dbt seed'.\n"
        "Reference them in models with {{ ref('seed_name') }}.\n\n"
        "Use for small, slowly-changing reference data:\n"
        "  - Question configurations (12 rows)\n"
        "  - Team hierarchies (5 rows)\n"
        "  - Test survey responses (111 rows, 3 are duplicates)\n\n"
        "NOT for large datasets -- use proper ETL for those."
    )

    print_panel(
        "Tests -- Data Quality Assertions",
        "Declared in schema.yml next to model SQL files.\n"
        "dbt generates SQL queries that return failing rows.\n\n"
        "Built-in tests:\n"
        "  - unique:           no duplicate values in column\n"
        "  - not_null:         no NULL values in column\n"
        "  - accepted_values:  all values in a known list\n"
        "  - relationships:    foreign key exists in another table\n\n"
        "'dbt test' runs all tests. Zero failing rows = PASS."
    )

    # ── Run dbt debug ────────────────────────────────────────────
    print_panel("Running dbt debug", "Verifying dbt can connect to DuckDB...")

    code, output = run_dbt("debug")

    # Extract key lines
    lines = output.split("\n")
    key_lines = [
        l.strip() for l in lines
        if any(k in l for k in ["profiles.yml", "dbt_project.yml", "Connection test", "adapter"])
    ]

    if code == 0:
        print_panel(
            "dbt debug: SUCCESS",
            "\n".join(key_lines) + "\n\nConnection to DuckDB verified."
        )
    else:
        print_panel("dbt debug: FAILED", output[-500:])
        return

    # ── Run dbt build (seed + run + test) ────────────────────────
    print_panel(
        "Running dbt build",
        "'dbt build' runs seeds, models, and tests in dependency order.\n"
        "This is the most common command in day-to-day dbt work."
    )

    code, output = run_dbt("build")

    # Parse results
    result_lines = [l for l in output.split("\n") if "PASS=" in l or "ERROR=" in l]
    model_lines = [
        l.strip() for l in output.split("\n")
        if "OK" in l and ("seed" in l or "model" in l or "PASS" in l)
    ]

    if code == 0:
        summary = "\n".join(model_lines[-15:]) if model_lines else "All steps completed."
        summary += "\n\n" + "\n".join(result_lines) if result_lines else ""
        print_panel("dbt build: SUCCESS", summary)
    else:
        # Show last 800 chars on failure
        print_panel("dbt build: FAILED", output[-800:])
        return

    # ── Summary ──────────────────────────────────────────────────
    print_panel(
        "What You Now Have",
        "A working dbt project that:\n\n"
        "1. Loads 3 CSV seed files into DuckDB (dbt seed)\n"
        "2. Builds 3 Bronze views (type casting)\n"
        "3. Builds 3 Silver tables (dedup, reverse-scale, suppression)\n"
        "4. Builds 2 Gold tables (team metrics, category summary)\n"
        "5. Runs 27 data tests (uniqueness, nulls, value ranges)\n\n"
        "Key commands:\n"
        "  cd dbt_project\n"
        "  uv run dbt build --profiles-dir .   # seed + run + test\n"
        "  uv run dbt run --profiles-dir .     # just build models\n"
        "  uv run dbt test --profiles-dir .    # just run tests\n"
        "  uv run dbt run --select silver.* --profiles-dir .  # only silver models"
    )


if __name__ == "__main__":
    main()
