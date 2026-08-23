# Pre-Computation vs Runtime Computation

## The Fundamental Tradeoff

Every data system answers the same question differently: **when do you compute?**

You have a query — "show me mean engagement score by team for Q1 2025." Two options:

- **Runtime computation.** Run the full aggregation query when the user asks. Joins fact table to dimensions, scans all matching rows, computes AVG. Always returns the freshest data. Cost: latency and compute on every request.

- **Pre-computation.** Run the query once, store the result in a table. When the user asks, scan the pre-computed table — no joins, no aggregation. Fast. Cost: storage, staleness (the result is frozen until you rebuild), and engineering complexity to keep it updated.

This isn't a "pick one" decision. Real systems use a spectrum of strategies based on how each query maps onto three variables:

- **Latency requirement.** A dashboard overview card needs sub-100ms. An ad-hoc drill-down can tolerate 5-15 seconds.
- **Data freshness requirement.** Financial dashboards need real-time. Engagement surveys update quarterly — staleness of hours is fine.
- **Query predictability.** If you know the exact queries users will run (e.g., "team x period" on every dashboard), you can pre-compute them. If users can slice by any combination of 8 dimensions, you can't pre-compute them all.

### Worked Example: Survey Engagement Dashboard

The dashboard has two panels:

1. **Overview card:** "Your team's engagement score: 3.8 (up 0.2 from last quarter)." This query is predictable (team x period), requested every page load, and needs to feel instant. Pre-compute it.

2. **Drill-down explorer:** "Show me scores for Engineering teams in Austin, filtered to Teamwork questions." This is ad-hoc — arbitrary dimension combos. Pre-computing every combination is infeasible. Compute at runtime.

The system uses **both** strategies, matched to the access pattern.

## Aggregation Strategies

### Full Pre-Computation: The OLAP Cube

The theoretical extreme: pre-compute every possible combination of dimensions. In OLAP terminology, this is a "cube" — a multi-dimensional array where each cell holds a pre-computed aggregate.

For survey data with 6 dimensions (team, question, time_period, department, location, category), the cube contains a cell for every combination: "Engineering teams in Austin answering Teamwork questions in Q1 2025." Every conceivable query becomes a lookup.

This works when:
- Dimension cardinalities are small (5 departments, 4 categories)
- Dimensions are few (3-4)
- The cube is rebuilt in batch, not real-time

It breaks when dimensions multiply. This is the combinatorial explosion problem covered below.

### Partial Pre-Computation: Rollup Tables

Instead of computing every dimension combination, you identify the **hot paths** — the 2-3 dimension combos that cover ~80% of actual queries — and pre-compute only those.

For the survey dashboard:
- `rollup_team_period` — team x time_period: covers the overview card
- `rollup_dept_category` — department x category: covers the department comparison view
- `rollup_team_category_period` — team x category x time_period: covers the team detail page

Everything else (ad-hoc slicing, uncommon dimension combos) is computed at runtime against the fact table.

How do you pick which combos to pre-compute? Instrument your queries. Log which dimensions users actually group by and filter on. The top 2-3 combos dominate. This is the 80/20 rule applied to query patterns.

### Full Runtime Computation

No pre-computation at all. Every query hits the fact table directly. This is the simplest architecture — no rollup tables to maintain, no staleness to worry about, no rebuild pipelines.

When it works:
- DuckDB or a columnar engine can handle the data volume with acceptable latency
- Users tolerate 1-10 second response times
- The data changes frequently enough that any pre-computed result goes stale quickly

When it breaks:
- Millions of rows with multi-table joins push latency past 10 seconds
- Concurrent users multiply the compute load
- The dashboard needs to feel "instant"

## Combinatorial Explosion

> **Combinatorial Explosion** — The phenomenon where the number of possible combinations grows as the product (not the sum) of the options in each dimension. Adding a dimension with *k* values multiplies the total combinations by *k*, so even modestly-sized dimensions produce unmanageably large output spaces. It is a fundamental constraint in optimization, testing, and pre-computation strategies. ([Wikipedia](https://en.wikipedia.org/wiki/Combinatorial_explosion))

This is the core reason you can't "just pre-compute everything."

Survey data has these dimensions with their cardinalities:

```
team           20 values
question       12 values
time_period     8 values
department      5 values
location        5 values
category        4 values
project         6 values
reporting_type  3 values
```

Rollup table size as you add dimensions:

```
2 dims (team x question):          240 rows
4 dims (+ time_period, dept):    9,600 rows
6 dims (+ location, category): 192,000 rows
8 dims (+ project, type):   3,456,000 rows
```

Each new dimension **multiplies** the row count — it's not additive. By 8 dimensions, you're storing 3.5 million pre-computed cells, and that's with small cardinalities. Add a dimension with 100 values (e.g., individual respondents) and you're at 345 million cells.

The math is simple: total cells = product of all dimension cardinalities. But the implication is profound — it means full pre-computation is structurally infeasible beyond ~4-5 dimensions for any non-trivial cardinalities.

This is why the partial pre-computation approach (rollup tables for hot paths) exists. You can't beat combinatorics, so you pick your battles.

## Rollup Tables in Practice

> **Materialized View** — A database object that stores the result of a query as a physical table. Unlike a regular view (which re-executes its query on every access), a materialized view is computed once and read from storage until explicitly refreshed with `REFRESH MATERIALIZED VIEW`. Rollup tables are the manual equivalent of this concept. ([PostgreSQL Docs](https://www.postgresql.org/docs/current/sql-creatematerializedview.html))

A rollup table is a materialized aggregation over specific dimension combinations. It stores pre-computed results that would otherwise require joining the fact table to dimension tables and running GROUP BY on every query.

### Design Decisions

**Which dimensions to include.** Analyze your query logs. If 70% of queries group by `team + period`, that's your first rollup table. The next most common combo is your second. You rarely need more than 3 rollup tables to cover the dominant access patterns.

**What aggregates to store.** Store the building blocks, not derived metrics:
- `SUM(score)`, `COUNT(*)` — from these you can compute AVG downstream
- `MIN(score)`, `MAX(score)` — can't be derived from AVG
- Don't store percentiles — they can't be reaggregated (non-additive facts)

Storing SUM and COUNT instead of AVG lets you combine rollup rows correctly. If you need the average across two teams, you add their SUMs and COUNTs and divide. You can't average two averages (unless the groups are the same size).

**When to rebuild.** For survey data, the natural cadence is "after a survey closes." The fact table doesn't change between surveys, so the rollup stays fresh. For more dynamic data, a nightly or hourly rebuild with a known staleness window is typical.

### The Rollup Hierarchy

Some rollups derive from other rollups. If you have `rollup_team_category_period` (20 x 4 x 8 = 640 rows), you can derive `rollup_team_period` by aggregating across categories — no need to re-scan the fact table. This is faster and uses the already-computed intermediate result.

```
fact_responses (115K rows)
    ↓ aggregate
rollup_team_category_period (640 rows)
    ↓ aggregate across categories
rollup_team_period (160 rows)
    ↓ aggregate across teams
rollup_period (8 rows)
```

Each level is cheaper to compute than going back to the fact table.

## Caching Layers

Caching is pre-computation with an expiration date. Instead of storing results in a permanent rollup table, you store them in a fast-access layer (memory, Redis, application dict) with a time-to-live (TTL).

> **Cache Invalidation** — The problem of knowing when a cached value no longer reflects the source of truth and must be discarded or replaced. Phil Karlton's quip -- "the two hard things in computer science are cache invalidation and naming things" -- reflects that determining *when* cached data is stale is structurally difficult: the cache has no way to know the source changed unless you build an explicit notification mechanism. ([Wikipedia](https://en.wikipedia.org/wiki/Cache_invalidation))

### Cache States

Every cache entry cycles through three states:

- **Fresh hit** — fast and correct. Best case. The cached result matches what a live query would return.
- **Stale hit** — fast but wrong. The underlying data changed, but the cache hasn't expired yet. The user sees outdated results. This is the dangerous state because it's invisible — the cache returns confidently, and nobody knows the answer is wrong until they compare.
- **Miss** — slow but correct. The cache entry expired (or never existed). The system falls back to the expensive query, gets the right answer, and populates the cache.

### TTL: The Knob

> **TTL (Time To Live)** — A metadata value attached to a cached entry (or network packet, DNS record, etc.) that specifies how long it remains valid before automatic expiration. When the TTL elapses, the entry is treated as expired and either evicted or refreshed from the source. ([Wikipedia](https://en.wikipedia.org/wiki/Time_to_live))

Short TTL (seconds to minutes): more misses, fresher data, higher compute load. Use for data that changes frequently and where staleness is unacceptable.

Long TTL (hours to days): more hits, staler data, lower compute load. Use for data that changes in batches (like survey responses) where a known staleness window is acceptable.

### Beyond TTL

> **Write-Through vs Write-Behind Cache** — Two strategies for keeping a cache consistent with its backing store. Write-through updates the cache and the backing store synchronously on every write, guaranteeing consistency at the cost of write latency. Write-behind (also called write-back) updates only the cache immediately and asynchronously flushes to the backing store later -- faster writes, but risks data loss if the cache fails before flushing. ([Wikipedia](https://en.wikipedia.org/wiki/Cache_(computing)#Writing_policies))

- **Event-driven invalidation.** When new survey data loads, explicitly clear the cache. No stale window — the cache is fresh until data changes, then immediately invalidated. More complex to implement because you need the write path to know about the cache.
- **Versioned keys.** Include a data version in the cache key: `team_scores_v17`. When data changes, increment the version. Old keys naturally become unreachable (and expire via TTL). Clean separation between old and new results.
- **Write-through.** Update the cache at the same time as the database. The cache is always fresh. Cost: every write operation is slower because it updates two systems.

For survey data, event-driven invalidation is the natural fit. Surveys close on a known schedule. After each close, the ETL pipeline runs, loads new data, and invalidates the cache. Between surveys, the cache never goes stale because the underlying data doesn't change.

## The Serving Layer Pattern

This is the pattern that makes analytics SaaS products feel fast. It separates computation from serving:

```
OLAP Engine (DuckDB / BigQuery / Snowflake)
    Heavy joins, aggregations, batch processing
    Computes Gold-level aggregates on a schedule
        ↓
    Push pre-computed results
        ↓
Serving Store (PostgreSQL / Redis / DynamoDB)
    Small, indexed tables
    Optimized for point lookups by key
    Handles concurrent API requests
        ↓
    Simple key lookup
        ↓
API / Dashboard
    Sub-100ms response time
    No analytical computation in the request path
```

### Why Two Stores?

OLAP engines (DuckDB, BigQuery, Snowflake) are built for scanning millions of rows, joining large tables, and computing aggregates. They're bad at serving thousands of concurrent point lookups with sub-millisecond latency.

Transactional stores (PostgreSQL, Redis) are the opposite. They're built for concurrent point lookups (`WHERE team_key = 42`) with indexing. They're bad at scanning and joining large analytical tables.

The serving layer pattern uses each store for what it's good at:
1. Compute in the OLAP engine (batch, scheduled)
2. Push results to the transactional store (small, pre-aggregated)
3. Serve from the transactional store (fast, concurrent)

### Concrete Numbers

From the lab exercise:
- DuckDB analytical query (join + aggregate + filter): ~1ms (small dataset — at production scale with millions of rows, this becomes 1-15 seconds)
- PostgreSQL point lookup (indexed WHERE clause): ~0.3ms

The gap isn't dramatic at small scale because DuckDB is fast. At production scale with 10M+ rows, complex joins, and 100 concurrent dashboard users, the DuckDB query might take 12-15 seconds while the PostgreSQL lookup stays at 0.3ms.

### Where "12-15 Second Query" Fits

When someone says "the dashboard loads in 15 seconds," they're usually describing a system where the dashboard query hits the OLAP engine directly — full runtime computation with no serving layer.

When the same dashboard "loads instantly," it's because:
- Overview data is served from a pre-computed serving table or cache (sub-100ms)
- Only drill-down or ad-hoc queries hit the OLAP engine (and users accept the wait)

This is not a DuckDB vs PostgreSQL question. It's an architecture question: are you computing on the request path, or serving pre-computed results?

## The Freshness-Latency Spectrum

Every point on this spectrum is a valid design choice. The right one depends on your domain:

```
Full Runtime                                    Full Pre-Computation
(always fresh, slow)                            (instant, may be stale)
     |                                                    |
     |--- runtime query (1-15s, always correct)           |
     |                                                    |
     |-------- cached runtime (fast repeat, TTL stale) ---|
     |                                                    |
     |-------- materialized view (rebuild schedule) ------|
     |                                                    |
     |-------- rollup table (rebuild on data change) -----|
     |                                                    |
     |-------- serving layer (push after batch ETL) ------|
     |                                                    |
     |--- OLAP cube (all combos, instant, huge storage) --|
```

For the engagement survey agent architecture:
- **Overview dashboard:** serving layer pattern. Compute Gold aggregates in DuckDB after each survey batch, push to PostgreSQL serving table. Sub-100ms reads.
- **Team detail page:** rollup table for team x category x period. Pre-computed, covers the standard view. Sub-100ms.
- **Ad-hoc exploration:** runtime query against DuckDB. User slices by arbitrary dimension combos. 1-10 seconds acceptable with a loading spinner.
- **Repeat ad-hoc queries:** cache layer (Redis or in-memory dict with TTL). First hit is slow, repeat is instant.

This layered approach is how production analytics systems work. It's not one strategy — it's the right strategy for each access pattern.
