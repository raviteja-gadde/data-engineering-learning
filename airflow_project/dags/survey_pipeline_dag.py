"""Survey Analytics Pipeline DAG

Orchestrates the dbt pipeline: seed -> run (by layer) -> test.

Uses BashOperator to call dbt commands. In a production setup, you'd
use the dbt Cloud operator or cosmos (astronomer-cosmos) for tighter
Airflow-dbt integration.

NOTE: This DAG demonstrates orchestration patterns. To run it for real,
you'd need dbt-duckdb installed in the Airflow container and the
dbt_project directory mounted. See the README for details.
"""

from datetime import datetime
from airflow import DAG
from airflow.operators.bash import BashOperator

# In production, this path would be a mounted volume or cloned repo.
DBT_DIR = "/opt/airflow/dbt_project"
DBT_CMD = f"cd {DBT_DIR} && dbt"
PROFILES = "--profiles-dir ."

with DAG(
    dag_id="survey_pipeline",
    description="Survey analytics dbt pipeline: seed -> bronze -> silver -> gold -> test",
    schedule=None,       # Manual trigger only (for learning)
    start_date=datetime(2025, 1, 1),
    catchup=False,       # Don't backfill past dates
    tags=["dbt", "survey", "medallion"],
    doc_md="""
    ## Survey Analytics Pipeline

    Runs the full dbt medallion pipeline:
    1. **Seed** -- Load CSV reference data into DuckDB
    2. **Bronze** -- Stage raw data with type casting
    3. **Silver** -- Clean, deduplicate, apply business rules
    4. **Gold** -- Pre-aggregate for dashboards
    5. **Test** -- Validate data quality contracts
    """,
) as dag:

    seed = BashOperator(
        task_id="dbt_seed",
        bash_command=f"{DBT_CMD} seed {PROFILES}",
        doc="Load CSV seed files (survey responses, question config, team hierarchy) into DuckDB.",
    )

    run_bronze = BashOperator(
        task_id="dbt_run_bronze",
        bash_command=f"{DBT_CMD} run --select bronze {PROFILES}",
        doc="Build bronze views: light type casting over raw seeds.",
    )

    run_silver = BashOperator(
        task_id="dbt_run_silver",
        bash_command=f"{DBT_CMD} run --select silver {PROFILES}",
        doc="Build silver tables: dedup, reverse-scale correction, suppression flags.",
    )

    run_gold = BashOperator(
        task_id="dbt_run_gold",
        bash_command=f"{DBT_CMD} run --select gold {PROFILES}",
        doc="Build gold tables: pre-aggregated team metrics and category summaries.",
    )

    test = BashOperator(
        task_id="dbt_test",
        bash_command=f"{DBT_CMD} test {PROFILES}",
        doc="Run all data quality tests (unique, not_null, accepted_values, custom).",
    )

    # Task dependency chain: seed -> bronze -> silver -> gold -> test
    seed >> run_bronze >> run_silver >> run_gold >> test
