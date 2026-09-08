"""Lab 05 -- dbt Tests and Data Contracts

Explores dbt's testing system:
1. List all tests defined in the project
2. Run tests and show results
3. Run the custom suppression-logic test
4. Demonstrate what a test FAILURE looks like (inject bad data, catch it, clean up)
5. Explain data contracts and test-driven data quality
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_sql, print_table
from shared.duck import duckdb_conn

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "dbt_project"
DB_PATH = PROJECT_DIR / "dev.duckdb"


def run_dbt(command: str) -> tuple[int, str]:
    result = subprocess.run(
        f"uv run dbt {command} --profiles-dir .",
        shell=True, capture_output=True, text=True, cwd=PROJECT_DIR, check=False,
    )
    return result.returncode, result.stdout + result.stderr


def main():
    print_panel(
        "Lab 05: dbt Tests and Data Contracts",
        "Tests are dbt's quality enforcement layer.\n"
        "A test is a SELECT that returns FAILING rows.\n"
        "Zero rows returned = PASS. Any rows returned = FAIL."
    )

    # ── Ensure models are built ────────────────────────────────────
    print_panel("Setup", "Running dbt build to ensure all models and seeds are current...")
    code, output = run_dbt("build")
    if code != 0:
        print_panel("BUILD FAILED", output[-500:])
        return

    # ── Step 1: List all tests ─────────────────────────────────────
    print_panel("Step 1: What Tests Exist?", "Listing all tests in the project...")
    code, output = run_dbt("ls --resource-type test")
    # Each line like: survey_analytics.silver.not_null_survey_responses_cleaned_score
    tests = [l.strip() for l in output.split("\n")
             if l.strip().startswith("survey_analytics.")]
    test_rows = []
    for t in tests:
        name = t.split(".")[-1]
        if "unique" in name:
            kind = "unique"
        elif "not_null" in name:
            kind = "not_null"
        elif "accepted_values" in name:
            kind = "accepted_values"
        elif "assert_" in name:
            kind = "custom (SQL file)"
        else:
            kind = "schema"
        # Extract layer from the qualified name
        parts = t.split(".")
        layer = parts[1] if len(parts) >= 3 else "project"
        test_rows.append((name, kind, layer))

    print_table(f"All Tests ({len(test_rows)} total)", ["Test Name", "Type", "Layer"], test_rows)

    # ── Step 2: Run all tests ──────────────────────────────────────
    print_panel("Step 2: Run All Tests", "Running dbt test...")
    code, output = run_dbt("test")
    result_lines = [l.strip() for l in output.split("\n") if "Pass" in l or "Fail" in l or "pass=" in l.lower()]
    summary = [l.strip() for l in output.split("\n") if "pass=" in l.lower() or "Done" in l]

    print_panel(
        "Test Results" + (" -- ALL PASSED" if code == 0 else " -- SOME FAILED"),
        "\n".join(result_lines[-15:]) + ("\n\n" + "\n".join(summary) if summary else ""),
    )

    # ── Step 3: The custom suppression test ────────────────────────
    print_panel(
        "Step 3: Custom Test -- Suppression Logic",
        "We wrote a SQL test file: tests/assert_suppressed_teams_low_count.sql\n\n"
        "It checks: if a team has < 4 respondents, is_suppressed must be true.\n"
        "The query returns any rows that VIOLATE this rule."
    )

    test_sql = (PROJECT_DIR / "tests" / "assert_suppressed_teams_low_count.sql").read_text()
    print_sql(test_sql)

    code, output = run_dbt("test --select assert_suppressed_teams_low_count")
    passed = code == 0
    status_lines = [l.strip() for l in output.split("\n") if "Pass" in l or "Fail" in l]
    print_panel(
        f"Custom Test: {'PASSED' if passed else 'FAILED'}",
        "\n".join(status_lines) or ("Test passed -- suppression logic is correct." if passed else output[-300:])
    )

    # ── Step 4: Demonstrate test failure ───────────────────────────
    print_panel(
        "Step 4: What Does a Test FAILURE Look Like?",
        "We will inject bad data directly into the Silver table:\n"
        "  - Insert a row with score=7 (violates accepted_values: 1-5)\n"
        "  - Run dbt test to see it fail\n"
        "  - Then rebuild models to restore clean data"
    )

    # Inject bad data
    with duckdb_conn(str(DB_PATH)) as con:
        con.execute("""
            INSERT INTO survey_responses_cleaned
                (response_id, respondent_id, team_id, project_id,
                 question_id, category, raw_score, score,
                 is_reverse_scored, submitted_at)
            VALUES
                ('R999', 'EMP99', 'T001', 'P01',
                 'Q01', 'Basic Needs', 7, 7,
                 false, '2025-05-01 12:00:00')
        """)
        (bad_count,) = con.execute(
            "SELECT count(*) FROM survey_responses_cleaned WHERE score > 5"
        ).fetchone()

    print_panel("Bad Data Injected", f"Inserted 1 row with score=7. Rows with score > 5: {bad_count}")

    # Run tests -- should fail on accepted_values for score
    code, output = run_dbt("test")
    fail_lines = [l.strip() for l in output.split("\n")
                  if "Fail" in l or "FAIL" in l or "fail" in l.lower() or "ERROR" in l]
    summary = [l.strip() for l in output.split("\n") if "pass=" in l.lower() or "fail=" in l.lower()]

    print_panel(
        "Test Results After Bad Data",
        ("TESTS FAILED (expected!):\n\n" if code != 0 else "Results:\n\n")
        + "\n".join(fail_lines[:8])
        + ("\n\n" + "\n".join(summary) if summary else "")
        + "\n\nThe accepted_values test caught the invalid score=7."
    )

    # Rebuild to restore clean data
    print_panel("Cleanup", "Running dbt run to rebuild clean tables from seeds...")
    run_dbt("run")

    # Verify clean
    code, _ = run_dbt("test")
    print_panel("After Rebuild", "All tests pass again." if code == 0 else "Some tests still failing -- check models.")

    # ── Step 5: Explain data contracts ─────────────────────────────
    print_panel(
        "Data Contracts: What and Why",
        "A data contract is a formal agreement about what data looks like.\n"
        "dbt tests enforce contracts automatically on every run.\n\n"
        "SCHEMA TESTS (in schema.yml):\n"
        "  unique        -- no duplicate response_ids in Silver\n"
        "  not_null      -- every row has a score, team_id, etc.\n"
        "  accepted_values -- scores are 1-5, categories are known values\n"
        "  relationships -- every team_id in responses exists in hierarchy\n\n"
        "CUSTOM TESTS (SQL files in tests/):\n"
        "  Business rules that built-in tests can't express.\n"
        "  Our suppression test: 'if count < 4 then must be suppressed.'\n\n"
        "WHY THIS MATTERS:\n"
        "  Without tests, bad data flows silently to dashboards.\n"
        "  The score=7 we injected would have inflated team T001's average.\n"
        "  Tests catch this before it reaches anyone.\n\n"
        "TEST-DRIVEN DATA QUALITY:\n"
        "  1. Write the test (what SHOULD be true)\n"
        "  2. Build the model (make it true)\n"
        "  3. Run tests on every pipeline execution\n"
        "  4. If tests fail, the pipeline stops -- bad data never ships"
    )


if __name__ == "__main__":
    main()
