# OLTP vs OLAP Spectrum

## Two Engines, One Database World

Every database is optimized for one of two workload patterns. Understanding which you're dealing with — and when to use each — is the single most important architectural decision in data engineering.

### OLTP: Online Transaction Processing

**What it does.** Serves your application. A user submits a survey response: INSERT one row. A manager updates a team name: UPDATE one row. The app fetches an employee profile: SELECT one row by primary key.

**Characteristics:**
- Many small, fast operations (thousands per second)
- Touches few rows per query (usually 1-10)
- Reads and writes interleaved — the same table gets INSERTs while other connections SELECT
- ACID transactions required — if two people submit surveys simultaneously, both must succeed without corrupting each other
- Latency target: <10ms per operation
- Row-oriented storage — the engine reads entire rows because it typically needs all columns for one record

> **ACID Properties** — The four guarantees that database transactions provide: Atomicity (the transaction is all-or-nothing), Consistency (the database moves from one valid state to another), Isolation (concurrent transactions don't interfere with each other), and Durability (committed data survives crashes). These guarantees are what make OLTP databases safe for concurrent writes -- without them, simultaneous survey submissions could corrupt each other's data. ([Wikipedia](https://en.wikipedia.org/wiki/ACID))

**Engines:** PostgreSQL, MySQL, SQL Server, DynamoDB, CockroachDB

**In the survey domain:** the application database where individual Q12 responses are stored as users submit them. When employee #4827 fills out the survey, 12 rows get INSERTed — one per question. The app reads them back to show "your submitted responses."

### OLAP: Online Analytical Processing

**What it does.** Answers analytical questions. "What is the mean engagement score by department for Q1 2025?" This query scans every response row for Q1, joins to team and question dimensions, groups by department, and computes AVG. One query touches hundreds of thousands of rows.

**Characteristics:**
- Few complex queries (tens to hundreds per day, not thousands per second)
- Each query scans massive row ranges — full table scans are normal, not pathological
- Mostly reads, with periodic bulk writes (batch loads, not individual INSERTs)
- Eventual consistency acceptable — if the latest survey batch hasn't loaded yet, the dashboard shows yesterday's data and nobody panics
- Latency target: seconds to minutes (interactive dashboards aim for 5-30 seconds)
- Column-oriented storage — the engine reads only the columns the query needs, then compresses identical values within each column (see Topic 2: Columnar Storage)

**Engines:** Snowflake, BigQuery, Redshift, DuckDB, ClickHouse, Apache Druid

**In the survey domain:** the warehouse where you compute "show me engagement trends by department over 8 quarters, broken down by Q12 category, compared to company-wide benchmarks." Scans 500K+ response rows, joins 3 dimension tables, groups by 3 axes.

### Side-by-Side: What the Engine Optimizes For

The difference isn't just "fast vs slow" — it's what each engine spends its optimization budget on.

**OLTP engine (PostgreSQL):**
- B-tree indexes on primary keys and foreign keys — finding one row by ID in O(log n)
- Row-level locking so concurrent transactions don't block each other
- Write-ahead log (WAL) for crash recovery — every committed transaction is durable
- Buffer pool sized for random access patterns (small reads from many different pages)
- Query planner optimized for index lookups, not full scans

> **MVCC (Multi-Version Concurrency Control)** — The mechanism most relational databases use to handle concurrent reads and writes without locking. Instead of blocking readers while a writer modifies a row, the database keeps multiple versions of each row -- readers see the version that was current when their transaction started, while writers create a new version. This is how PostgreSQL avoids "readers block writers" at the cost of needing periodic VACUUM to clean up old row versions. ([PostgreSQL Docs](https://www.postgresql.org/docs/current/mvcc-intro.html))

> **WAL (Write-Ahead Log)** — A durability mechanism where the database writes every change to a sequential log file *before* applying it to the actual data files. If the system crashes mid-operation, the database replays the log on restart to recover committed transactions. The sequential write pattern also improves performance -- appending to a log is faster than random writes to data pages. ([PostgreSQL Docs](https://www.postgresql.org/docs/current/wal-intro.html))

> **Index Scan vs Sequential Scan** — PostgreSQL's query planner chooses between two fundamental strategies for reading table data. A sequential scan reads every row in the table -- cheap per-row (sequential I/O) but scales linearly with table size. An index scan uses a B-tree (or other index) to jump directly to matching rows -- fast for selective queries (returning less than ~5-10% of rows) but more expensive per-row due to random I/O. The planner picks whichever has lower estimated cost, which is why adding an index does not guarantee it gets used. ([PostgreSQL Docs](https://www.postgresql.org/docs/current/using-explain.html))

**OLAP engine (DuckDB/Snowflake):**
- Columnar storage — only reads the columns you SELECT or filter on
- Vectorized execution — processes data in batches of 1024+ values instead of row-by-row (see Topic 2)
- Compression — run-length encoding, dictionary encoding, delta encoding on sorted columns
- Parallelism — splits large scans across CPU cores automatically
- Zone maps / min-max indexes — skips entire chunks of data when a filter eliminates them

Running an OLAP query on an OLTP engine works but wastes resources. Running OLTP workloads on an OLAP engine works but feels sluggish for point lookups. Neither is "better" — they're different tools.

## Why the Binary Is Blurring

The clean OLTP/OLAP split is a useful mental model, but real systems are more nuanced. Several forces are pushing the engines toward each other.

### DuckDB: Embedded OLAP

SQLite made it normal to embed a transactional database directly in your application — no server, no network hop. DuckDB does the same for analytics.

- **No server.** Runs in-process, same as SQLite. Your Python script imports it, creates an in-memory database, runs columnar analytics.
- **Columnar engine.** Vectorized execution, compression, parallel scans — all the OLAP optimizations, but in a library that fits in your process.
- **Use case.** Local analytics, data pipelines that transform data before loading to a warehouse, development/testing where you don't want to spin up Snowflake. Also: embedded analytics in applications (dashboards that compute client-side).

DuckDB doesn't replace PostgreSQL for serving your application — it has no row-level locking, no concurrent write transactions, no replication. But for analytical workloads that would traditionally require a warehouse, it's remarkably capable.

### PostgreSQL + Extensions: OLTP Reaching Toward OLAP

PostgreSQL itself has acquired analytical features over time:

- **Parallel query execution** (since v9.6) — can split sequential scans across workers
- **Partitioning** (since v10) — range-partition large tables so queries skip irrelevant partitions
- **Materialized views** — pre-compute and store query results; refresh on demand
- **BRIN indexes** — block range indexes that store min/max per block, useful for time-series scans
- **Columnar extensions (Citus/Hydra)** — add columnar storage to PostgreSQL tables

These help, but PostgreSQL is still fundamentally row-oriented at the storage layer. A full scan of 100M rows will always be slower than a purpose-built columnar engine because PostgreSQL reads entire rows from disk even if you only need 2 columns.

### Snowflake Hybrid Tables: OLAP Reaching Toward OLTP

Snowflake introduced Hybrid Tables — tables that support ACID transactions, unique constraints, and foreign keys within a column-oriented warehouse.

- Row-level locking for individual INSERTs/UPDATEs
- Single-digit millisecond point lookups
- Same Snowflake SQL, same ecosystem

The catch: it's expensive. You're paying warehouse compute prices for workloads that PostgreSQL handles for a fraction of the cost. This matters more for cost-sensitive workloads (serving an API with 10K requests/second) than for occasional transactional needs within an analytical workflow.

### Real-Time OLAP: ClickHouse, Apache Pinot, Apache Druid

These engines optimize for a specific hybrid: high-throughput writes (100K+ events/second) combined with low-latency analytical queries (sub-second).

- **ClickHouse:** columnar, append-optimized. Ingests event streams and serves dashboards with sub-second latency. Used for observability (logs, metrics, traces).
- **Apache Pinot:** distributed OLAP with real-time ingestion from Kafka. Powers LinkedIn's analytics dashboards.
- **Apache Druid:** similar niche — real-time ingestion + fast slicing. Used for user-facing analytics.

These aren't general-purpose OLTP engines — they don't do row-level updates well, and they don't support general transactions. But they blur the "OLAP is slow to ingest" assumption.

## The Serving Layer Pattern

This is the most practically important pattern in this entire topic.

### The Problem

Your analytics warehouse (Snowflake/BigQuery/DuckDB) can answer any question about your survey data. But the queries take 5-30 seconds. Your application — a dashboard, an AI agent, an API endpoint — needs answers in <100ms. You cannot have users waiting 15 seconds for a dashboard to load.

### The Solution

Separate **computation** from **serving**.

```
[OLAP Engine]                 [OLTP Store]              [Application]
 DuckDB / Snowflake   --->    PostgreSQL / Redis   --->  Dashboard / Agent
 Batch computation            Indexed serving table      Point lookups
 Runs on schedule             Handles concurrency        <10ms per query
 Seconds per query            Sub-ms per query
```

**Stage 1: Compute in OLAP.** Run the heavy aggregations in the analytical engine. Join fact tables to dimensions, group by team/category/period, compute means. This is the engine's strength — columnar scans, vectorized execution, parallel processing. Takes seconds, runs once.

**Stage 2: Push to OLTP.** Write the computed results (a small table — hundreds to thousands of rows) into a serving table in PostgreSQL (or Redis, or DynamoDB). Add indexes on the columns you'll query by.

**Stage 3: Serve from OLTP.** The application queries the serving table by primary key or index. `SELECT * FROM gold_team_scores WHERE team_id = 42`. B-tree index lookup, sub-millisecond. Handles thousands of concurrent requests.

> **Connection Pooling** — The practice of maintaining a pool of reusable database connections rather than creating a new one for every request. Each PostgreSQL connection spawns a separate OS process (~5-10 MB of memory), so a serving layer handling thousands of concurrent API requests would exhaust memory without pooling. Tools like PgBouncer or application-level pools (HikariCP, SQLAlchemy pool) maintain a fixed set of connections and multiplex requests across them. ([Wikipedia](https://en.wikipedia.org/wiki/Connection_pool))

### Why This Works

The insight is a **volume mismatch**. The raw data might be 10 million rows. The aggregated result is 800 rows. Reading 800 pre-computed rows by index is trivially fast on any engine. The expensive computation already happened offline.

This is how analytics SaaS works:

- **Looker:** defines a semantic layer over the warehouse, caches query results, serves from cache
- **Sigma Computing:** sends live queries to the warehouse but caches aggressively; the cache serves most dashboard loads
- **Hex:** materializes datasets during notebook runs; dashboards read materialized results
- **Mode Analytics:** query results cached and served from application storage

None of these make the user wait 15 seconds for every dashboard view. They all use some variant of compute-once-serve-many.

### The Survey Data Case

**Overview screen** (agent tool: `get_team_overview`): this is a serving layer problem. Pre-compute team-level aggregations in DuckDB, push to PostgreSQL, serve by team_id. The user sees results in <100ms.

**Drill-down screen** (agent tool: `get_drill_down`): this might be a runtime OLAP problem. The user picks a team, a question category, and a time range — there are too many combinations to pre-compute all of them. Options:

1. Accept higher latency (2-5 seconds) and query DuckDB at runtime
2. Use a fast OLAP engine (DuckDB embedded in the app) so "runtime OLAP" still means <1 second
3. Pre-compute the most common drill-down paths, fall back to runtime for rare ones

Option 2 is why DuckDB matters for application developers. It's OLAP that's fast enough to serve interactive drill-downs without a separate serving layer.

## When to Use What

**Pure OLTP (PostgreSQL, MySQL):**
- Your app database. Handles user authentication, form submissions, CRUD operations.
- The serving layer in an analytical pipeline. Pre-computed results indexed for fast reads.

**Pure OLAP (Snowflake, BigQuery, Redshift):**
- The warehouse. Batch computations, historical analysis, complex joins over large datasets.
- Used by data teams, not by application users directly.

**Embedded OLAP (DuckDB):**
- Local development and testing. Run analytics without provisioning a warehouse.
- Data pipeline stages. Transform data in Python scripts before loading.
- Application-embedded analytics. Drill-downs that need sub-second latency but can't be pre-computed.

**Hybrid/Real-time OLAP (ClickHouse, Pinot, Druid):**
- High-volume event ingestion + low-latency dashboards. Observability, real-time analytics.
- Not general-purpose — specific to append-heavy, time-series-style workloads.

**PostgreSQL as "good enough" hybrid:**
- Small to medium datasets (<10M rows) where you don't want operational complexity of two engines.
- Use materialized views for pre-computed analytics. Accept staleness between refreshes.
- Breaks down at scale: materialized views on 100M rows take minutes to refresh, and full-table scans compete with transactional workloads.
