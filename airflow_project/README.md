# Airflow Project -- Survey Pipeline Orchestration

Minimal Apache Airflow setup (LocalExecutor) to demonstrate DAG-based orchestration
of the dbt survey analytics pipeline.

## Prerequisites

- Podman (or Docker) with at least 4 GB memory allocated
- Port 8080 (webserver) and 5433 (Airflow Postgres) available

## Quick Start

```bash
# Set Podman socket (skip if using Docker)
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"

# Initialize database and create admin user (first time only)
podman compose up airflow-init

# Start Airflow
podman compose up -d

# Check status
podman compose ps
```

## Access the UI

- URL: http://localhost:8080
- Username: `airflow`
- Password: `airflow`

## Trigger the DAG

1. Open the Airflow UI
2. Find `survey_pipeline` in the DAG list
3. Toggle the DAG ON (unpause it)
4. Click the play button to trigger a run
5. Click into the DAG to see the task graph and logs

## The DAG

`dags/survey_pipeline_dag.py` chains five tasks:

```
dbt_seed -> dbt_run_bronze -> dbt_run_silver -> dbt_run_gold -> dbt_test
```

Each task is a BashOperator calling a dbt command. The `>>` operator sets
the dependency order -- Airflow ensures each task only runs after its
upstream dependency succeeds.

## Important Notes

- This setup is for **learning only**, not production
- The DAG demonstrates the orchestration pattern; to run dbt for real,
  you'd need to mount the dbt_project directory and install dbt-duckdb
  in the Airflow container (via `_PIP_ADDITIONAL_REQUIREMENTS` in docker-compose.yml)
- Airflow containers can take 30-60 seconds to start up
- If startup fails, check `podman compose logs` for errors

## Tear Down

```bash
podman compose down          # stop containers
podman compose down -v       # stop + delete volumes (postgres data)
```
