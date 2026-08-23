# Medallion Architecture, dbt, and Airflow

## What Problem Are We Solving?

You have raw data landing in your warehouse from various sources -- survey responses, HR systems, project trackers. The data is messy: duplicate records from retries, inconsistent types, missing values, questions that need reverse-scoring. You need a systematic way to clean, transform, and aggregate this data so analysts can trust it.

Two problems, actually:

1. **How do you organize transformations?** If you just write a giant SQL script that does everything, you get a 500-line monolith nobody can debug. When the reverse-scoring logic changes, you grep through that script and hope you found every place.

2. **How do you make transformations reproducible and testable?** If transformations live in someone's Jupyter notebook or a series of manual SQL commands, you have no version control, no tests, and no way to re-run them reliably.

The medallion architecture solves problem 1 (organization). dbt solves problem 2 (reproducibility and testing). Airflow solves scheduling -- making it all run automatically.

## Medallion Architecture: Bronze, Silver, Gold

The medallion architecture organizes your data warehouse into three layers, each with specific guarantees about what the data looks like.

### Bronze Layer (Raw Ingestion)

**What it is:** The raw data exactly as it arrived. Nothing is lost, nothing is changed. If the source system sent duplicates, bronze has duplicates. If a score field came in as a string, bronze stores it as a string.

**Why it exists:** Bronze is your insurance policy. When a transformation bug corrupts silver data, you rebuild from bronze. When someone asks "what did the raw data actually say?", bronze has the answer.

**What goes here:**
- Raw CSV/JSON dumps loaded as-is
- Cast to basic types at most (everything as VARCHAR if needed)
- Add metadata: `_loaded_at` timestamp, `_source_file` name
- Never delete, never update -- append only

**Survey example:** `raw_survey_responses` has every row from every CSV upload. Response ID 42 might appear three times because the ingestion job retried. Scores are strings. Timestamps are inconsistent formats.

### Silver Layer (Cleaned and Conformed)

**What it is:** Cleaned, deduplicated, typed, business-rule-applied data. This is where you enforce the shape of your data.

**Why it exists:** Silver is the "single source of truth" for analysts. Every downstream query or dashboard reads from silver, so cleaning logic lives in exactly one place.

**What happens here:**
- **Deduplication:** Pick the latest version of each response using `ROW_NUMBER() OVER (PARTITION BY response_id ORDER BY submitted_at DESC)`
- **Type enforcement:** Cast score to INTEGER, submitted_at to TIMESTAMP
- **Reverse-scale correction:** Some survey questions are reverse-scored (our project treats Q6 as one for demonstration). If the raw score is 5, the corrected score is `6 - raw_score = 1`. This logic is encoded here, version-controlled, and testable.
- **Suppression flags:** Compute whether a team has enough responses to report. If team_id='T003' has only 3 responses, mark it suppressed (threshold is typically 4-5 to protect anonymity).
- **Validation:** Reject rows with scores outside 1-5, missing required fields

**Survey example:** `survey_responses_cleaned` has exactly one row per response, scores are integers, reverse-scored questions have been corrected. `suppression_flags` has one row per team indicating whether that team's data can be shown.

### Gold Layer (Aggregated, Dashboard-Ready)

**What it is:** Pre-computed aggregates designed for specific analytical use cases. These tables map directly to dashboard widgets or report sections.

**Why it exists:** If every dashboard computes `AVG(score) GROUP BY team_id, category` on the fly, you're duplicating logic across dashboards and paying the computation cost repeatedly. Gold computes it once.

**What goes here:**
- Mean score per team per question category
- Response rates by department
- Period-over-period trends
- Pre-joined wide tables for specific dashboards

**Survey example:** `team_overview_metrics` has one row per team per category per survey period with the pre-computed mean score. An analyst or dashboard reads this directly -- no joins, no aggregations needed.

### Layer Guarantees Summary

- **Bronze:** "Everything that arrived, exactly as it arrived." No data loss.
- **Silver:** "One clean, typed, business-rule-applied version of each record." Trustworthy for analysis.
- **Gold:** "Pre-aggregated answers to common analytical questions." Fast and consistent across consumers.

### Where Business Rules Get Encoded

This is the critical insight: **business rules live in silver models, version-controlled and tested.**

- Reverse-scoring logic? Silver model, with a test asserting corrected scores are 1-5.
- Suppression threshold? Silver model, with the threshold as a configurable parameter.
- Deduplication strategy? Silver model, with a test asserting uniqueness on response_id.
- Category mappings? A seed file (CSV in version control), referenced by silver models.

When the business rule changes (say, suppression threshold moves from 4 to 5), you change one model, the test suite validates it, and the audit trail is a git commit.

## What Is dbt?

**dbt is `make` for your data warehouse.** That analogy is precise:

- `make` compiles source code into executables by running build commands in dependency order.
- `dbt` compiles SQL templates into executable queries and runs them against your warehouse in dependency order.

### What dbt Does

1. You write SQL files (called "models") that define transformations: `SELECT ... FROM ... JOIN ... WHERE ...`
2. dbt figures out the dependency order from `{{ ref() }}` calls between models
3. dbt runs each model's SQL against the warehouse, creating tables or views
4. dbt runs tests you defined to validate the results

> **ETL vs ELT** — ETL (Extract, Transform, Load) extracts data from sources, transforms it in a separate processing layer, then loads the result into the warehouse. ELT (Extract, Load, Transform) loads raw data into the warehouse first, then transforms it in place using the warehouse's own compute. Modern cloud warehouses (Snowflake, BigQuery, Redshift) have enough compute power to handle the transformation step, making ELT the dominant pattern: raw data lands fast, and transformation logic lives in SQL inside the warehouse. dbt is the canonical tool for the "T" in ELT. ([Wikipedia — Extract, Transform, Load](https://en.wikipedia.org/wiki/Extract,_transform,_load))

### What dbt Does NOT Do

- **dbt does not extract or load data.** It does not read CSVs from S3, hit APIs, or move data between systems. That's what tools like Fivetran, Airbyte, or custom scripts do.
- **dbt does not schedule itself.** It runs when you (or Airflow) tell it to.
- **dbt does not store data.** Your warehouse (DuckDB, Snowflake, BigQuery) stores the data. dbt just writes SQL that creates/updates tables in it.

dbt's tagline is "transform data already in your warehouse." Take that literally.

### Why dbt Exists

Before dbt, data transformation looked like this:

1. An analyst writes a SQL script with 30 CREATE TABLE statements
2. The script has implicit ordering -- table 5 depends on table 3, but you only know this by reading the SQL
3. When something breaks, you re-run the whole script
4. Testing means manually spot-checking query results
5. Version control is "email the latest script to your teammate"

dbt replaces this with: one SQL file per transformation, automatic dependency resolution, built-in testing, and git-based version control. The same workflow software engineers have had for decades.

## dbt Core Concepts

### Models

A **model** is a SQL file that defines one transformation. Each file in `models/` becomes a table or view in your warehouse.

```sql
-- models/silver/survey_responses_cleaned.sql
-- This file IS the transformation. dbt runs it.

SELECT
    response_id,
    respondent_id,
    team_id,
    question_id,
    CAST(score AS INTEGER) AS score,
    CAST(submitted_at AS TIMESTAMP) AS submitted_at
FROM {{ ref('stg_survey_responses') }}
WHERE score BETWEEN 1 AND 5
```

When you run `dbt run`, dbt executes this SQL as `CREATE TABLE survey_responses_cleaned AS (SELECT ...)` or `CREATE VIEW survey_responses_cleaned AS (SELECT ...)` depending on the materialization config.

**Key insight:** The model file contains a SELECT statement, not a CREATE TABLE. dbt wraps it in the appropriate DDL for you.

### Sources and Seeds

**Seeds** are CSV files committed to your dbt project. Running `dbt seed` loads them into your warehouse as tables. Use seeds for small, slowly-changing reference data -- question configurations, category mappings, team hierarchies.

```
seeds/
  raw_question_config.csv    -> becomes a table you can ref('raw_question_config')
  raw_team_hierarchy.csv     -> becomes a table you can ref('raw_team_hierarchy')
```

**Sources** declare external tables that exist in your warehouse but weren't created by dbt. You declare them in YAML and reference them with `{{ source('source_name', 'table_name') }}`. Sources are for data loaded by other tools (Fivetran, custom ETL).

In our learning project, we use seeds (CSVs) for our raw data since we don't have an external ETL pipeline.

### Refs: The Dependency Graph

`{{ ref('model_name') }}` is how one model references another. This does two things:

1. **Resolves the table name** -- dbt figures out the schema and table name
2. **Declares a dependency** -- dbt knows this model must run after the referenced model

```sql
-- models/gold/team_overview_metrics.sql
SELECT team_id, category, AVG(score) as mean_score
FROM {{ ref('survey_responses_cleaned') }}   -- depends on silver model
JOIN {{ ref('raw_question_config') }}         -- depends on seed
  USING (question_id)
GROUP BY team_id, category
```

> **DAG (Directed Acyclic Graph)** — A graph where edges have direction and no cycles exist — you can never follow edges from a node back to itself. In data engineering, DAGs model dependency relationships: node A must complete before node B can start. Both dbt (model dependencies via `ref()`) and Airflow (task dependencies via `>>`) represent their workflows as DAGs. The "acyclic" constraint is essential — a cycle would mean A depends on B depends on A, which is unresolvable. Topological sort produces a valid execution order for any DAG. ([Wikipedia — Directed Acyclic Graph](https://en.wikipedia.org/wiki/Directed_acyclic_graph))

dbt builds a DAG (directed acyclic graph) from all the `ref()` calls. It runs models in topological order: seeds first, then bronze, then silver, then gold. You never have to specify the order manually.

### Tests

dbt tests are assertions about your data. They are SQL queries that return rows when something is wrong -- zero rows returned means the test passes.

**Built-in generic tests** (declared in YAML):

```yaml
models:
  - name: survey_responses_cleaned
    columns:
      - name: response_id
        data_tests:
          - unique
          - not_null
      - name: score
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [1, 2, 3, 4, 5]
                quote: false
```

- `unique`: fails if any duplicate values exist in the column
- `not_null`: fails if any NULL values exist
- `accepted_values`: fails if any value is not in the provided list
- `relationships`: fails if a foreign key references a non-existent primary key

Running `dbt test` executes all declared tests and reports pass/fail.

### Materializations

> **Materialization** — In the database context, materialization means physically computing and storing the result of a query, as opposed to keeping it as a logical definition that re-executes on every read. A view is unmaterialized (a saved query); a table is fully materialized (stored result). The concept extends to materialized views (stored results that can be refreshed) and, in dbt, to the incremental strategy (store results, then append/merge only new data on subsequent runs). The choice trades off build cost, query speed, and data freshness. ([PostgreSQL docs — Materialized Views](https://www.postgresql.org/docs/current/rules-materializedviews.html))

> **CTE (Common Table Expression)** — A named subquery defined with the `WITH` keyword that exists only for the duration of the enclosing statement. CTEs make complex SQL readable by breaking it into named steps that reference each other sequentially. In dbt, an `ephemeral` model is compiled into a CTE that gets inlined into the downstream model's SQL rather than being created as a table or view in the warehouse. ([PostgreSQL docs — WITH Queries](https://www.postgresql.org/docs/current/queries-with.html))

A materialization controls *how* dbt creates the model in the warehouse:

- **`view`** (default): `CREATE VIEW AS (SELECT ...)`. No data stored. Query runs fresh every time someone reads from it. Fast to build, always current, but slow to query if the upstream data is large.
- **`table`**: `CREATE TABLE AS (SELECT ...)`. Data stored on disk. Fast to query, but rebuilds from scratch on every `dbt run`.
- **`incremental`**: On first run, acts like `table`. On subsequent runs, only processes new/changed rows using `{% if is_incremental() %}` logic. Fast for large tables where only recent data changes.
- **`ephemeral`**: Not created in the warehouse at all. The SQL is inlined as a CTE into downstream models. Use for simple helper transformations.

Typical pattern: bronze/silver as `table` or `incremental`, gold as `table`.

> **Idempotency** — An operation is idempotent if running it once produces the same result as running it multiple times. For data pipelines, this means re-running a transformation should not produce duplicates, corrupt existing data, or yield different results. Idempotent pipelines are safe to retry on failure — which is critical because failures in distributed systems are the norm, not the exception. dbt's `unique_key` merge strategy and full-refresh capability are mechanisms for achieving idempotency in incremental models. ([Wikipedia — Idempotence](https://en.wikipedia.org/wiki/Idempotence))

### Incremental Models

Incremental models avoid reprocessing your entire table on every run. They're the "speed vs. correctness" tradeoff.

```sql
-- models/silver/survey_responses_cleaned.sql
{{
  config(
    materialized='incremental',
    unique_key='response_id'
  )
}}

SELECT
    response_id,
    respondent_id,
    team_id,
    question_id,
    CAST(score AS INTEGER) AS score,
    CAST(submitted_at AS TIMESTAMP) AS submitted_at
FROM {{ ref('stg_survey_responses') }}

{% if is_incremental() %}
  WHERE submitted_at > (SELECT MAX(submitted_at) FROM {{ this }})
{% endif %}
```

**How it works:**
- First run: `is_incremental()` is false, so the WHERE clause is skipped. The entire result is loaded.
- Subsequent runs: `is_incremental()` is true, so only rows newer than the current max timestamp are selected.
- `unique_key='response_id'` tells dbt to MERGE (upsert) rather than append, preventing duplicates.

**The tradeoff:** If old data gets corrected in the source, an incremental model won't pick up the correction (it only looks at new timestamps). You can force a full rebuild with `dbt run --full-refresh`.

## dbt Execution Commands

```bash
dbt seed          # Load CSV seed files into the warehouse
dbt run           # Execute all models (build tables/views)
dbt test          # Run all data tests
dbt build         # seed + run + test in dependency order
dbt run --select silver.*   # Run only models in the silver directory
dbt run --select +team_overview_metrics  # Run this model and all upstream dependencies
dbt test --select survey_responses_cleaned  # Test only this model
dbt run --full-refresh      # Rebuild incremental models from scratch
```

`dbt build` is the most common command in practice -- it runs seeds, models, and tests in the correct order, stopping if a test fails so downstream models don't build on bad data.

## dbt Project Structure

```
dbt_project/
  dbt_project.yml         # Project configuration (name, paths, materializations)
  profiles.yml            # Connection configuration (which warehouse, credentials)
  models/
    bronze/               # Staging models (light cleaning of seeds/sources)
      stg_survey_responses.sql
      schema.yml          # Tests and docs for bronze models
    silver/               # Cleaned, business-rule-applied models
      survey_responses_cleaned.sql
      schema.yml
    gold/                 # Aggregated, dashboard-ready models
      team_overview_metrics.sql
      schema.yml
  seeds/                  # CSV reference data
    raw_survey_responses.csv
    raw_question_config.csv
```

### dbt_project.yml

The project-level config file. Sets the project name, where to find models and seeds, and default materializations per directory.

```yaml
name: 'survey_analytics'
version: '1.0.0'
config-version: 2
profile: 'survey_analytics'

model-paths: ["models"]
seed-paths: ["seeds"]
test-paths: ["tests"]

models:
  survey_analytics:
    bronze:
      +materialized: view
    silver:
      +materialized: table
    gold:
      +materialized: table
```

### profiles.yml

Tells dbt how to connect to the warehouse. For dbt-duckdb, this is minimal:

```yaml
survey_analytics:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: './dev.duckdb'
      schema: main
      threads: 4
```

## Data Contracts and Schema Tests

Data contracts are guarantees about what a model's output looks like. In dbt, you express them through schema YAML tests:

- **Uniqueness:** response_id is unique (no duplicates survived dedup)
- **Completeness:** team_id is never null (every response has a team)
- **Domain validity:** score is always 1, 2, 3, 4, or 5 (nothing outside the Likert scale)
- **Referential integrity:** question_id in responses always exists in question_config

These aren't just documentation -- `dbt test` actually runs SQL queries to verify them. If a transformation bug introduces nulls or duplicates, the test catches it before the data reaches dashboards.

## What Is Airflow?

**Airflow is `cron` that knows about task dependencies and has a monitoring UI.**

`cron` can schedule a script to run at 2 AM every day. But:
- If the script fails, cron doesn't retry or alert you
- If you have five scripts that depend on each other, cron doesn't know about the dependencies
- You have no visibility into what ran, what failed, and why

Airflow solves all three.

### Core Concepts

- **DAG (Directed Acyclic Graph):** A workflow defined in Python. Specifies what tasks to run and their dependencies. Not the same as dbt's DAG -- Airflow's DAG is about task scheduling, dbt's DAG is about SQL model dependencies.
- **Task:** A single unit of work within a DAG. "Run dbt seed", "Run dbt run", "Send a Slack notification".
- **Operator:** The type of work a task performs. `BashOperator` runs a shell command. `PythonOperator` runs a Python function. `DbtRunOperator` (from airflow-dbt) runs dbt commands.
- **Schedule:** When the DAG runs. `@daily`, `@hourly`, or a cron expression like `0 2 * * *` (2 AM daily).
- **Sensor:** A task that waits for a condition before proceeding. "Wait until the CSV file appears in S3, then start the pipeline."
- **Connection:** Stored credentials for external systems (databases, APIs, S3).

### A Typical DAG

```python
# Pseudocode -- Airflow orchestration is covered conceptually here
dag = DAG('survey_pipeline', schedule='@daily')

extract_task = BashOperator(task_id='extract', bash_command='python extract.py')
dbt_seed = BashOperator(task_id='dbt_seed', bash_command='dbt seed')
dbt_run = BashOperator(task_id='dbt_run', bash_command='dbt run')
dbt_test = BashOperator(task_id='dbt_test', bash_command='dbt test')
notify = BashOperator(task_id='notify', bash_command='python notify.py')

extract_task >> dbt_seed >> dbt_run >> dbt_test >> notify
```

The `>>` operator means "runs after." Airflow guarantees `dbt_run` won't start until `dbt_seed` succeeds. If `dbt_test` fails, `notify` doesn't run (or you configure it to send a failure alert instead).

## Airflow + dbt Together

The split of responsibilities:

- **dbt owns the "what"**
  - What transformations to run (SQL models)
  - In what order (ref-based DAG)
  - Whether results are correct (data tests)
- **Airflow owns the "when" and "what if"**
  - When it runs (schedule)
  - What if it fails (retry, alerting)
  - What ran yesterday (UI, logs)

Airflow doesn't know or care what dbt does internally. It just runs `dbt build` as a shell command and checks the exit code. dbt doesn't know or care who invoked it. This separation means you can run dbt locally during development and let Airflow run it in production -- same transformations, different trigger.
