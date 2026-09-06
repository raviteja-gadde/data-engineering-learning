# Data engineering labs

Learn data engineering by building. Runnable labs, dbt models, and Airflow pipelines, all built around a practical survey analytics example.

Each topic has a **note** (concepts + worked examples), **labs** (runnable Python scripts), and an **HTML page** (visual synthesis).

## Setup

```bash
uv sync                        # install dependencies
uv sync --extra dbt             # install dbt (for Topic 3)
```

PostgreSQL labs (Topics 2, 4, 6, 7, 8, 9) need a local container:

```bash
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"
podman compose -f docker/docker-compose.postgres.yml up -d
```

## Run a Lab

```bash
uv run python labs/01_warehousing_fundamentals/01_star_schema.py
```

## Topics

**1. Warehousing Fundamentals**
Star/snowflake schemas, SCD Type 2, grain, measures vs dimensions

**2. Columnar Storage**
Row vs column layout, compression, predicate pushdown, vectorized execution

**3. Medallion & dbt**
Bronze/silver/gold layers, dbt models, incremental loads, testing

**4. Precomputation vs Runtime**
Rollup tables, materialized views, serving layer, cache invalidation

**5. Dynamic Schema & EAV**
EAV, slot mechanism, JSON/VARIANT, sparse wide tables

**6. OLTP/OLAP Spectrum**
PostgreSQL vs DuckDB benchmarking, hybrid patterns, caching

**7. Suppression & Access Control**
k-anonymity, row-level security, dynamic masking

**8. Frontier Survey Platforms**
Qualtrics, Culture Amp, Medallia architecture patterns

**9. Agentic Data Access**
Tool contracts, scope resolution, context window sizing, non-contradiction

## Structure

```
notes/           # concept notes with prerequisite refreshers and citation links
labs/            # runnable Python labs (DuckDB + PostgreSQL)
html/            # visual HTML synthesis pages (open directly in browser)
dbt_project/     # bronze/silver/gold medallion models
airflow_project/ # DAG-based pipeline orchestration
shared/          # DuckDB, PostgreSQL, and display helpers
```

## Attribution

Built with [Claude Code](https://claude.ai/code).

## License

[MIT](LICENSE)
