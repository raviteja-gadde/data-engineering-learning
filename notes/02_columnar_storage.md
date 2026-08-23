# Columnar Storage and Modern Query Engines

## Row-Oriented vs Column-Oriented Storage

### Physical Layout on Disk

Every database stores data in pages (typically 4-8 KB blocks on disk). The difference is *what goes into each page*.

**Row-oriented** (PostgreSQL, MySQL, SQLite): each page contains complete rows, one after another.

```
Page 1: [emp_1: id, name, team_id, q01, q02, ... q12]
         [emp_2: id, name, team_id, q01, q02, ... q12]
         [emp_3: id, name, team_id, q01, q02, ... q12]

Page 2: [emp_4: id, name, team_id, q01, q02, ... q12]
         ...
```

**Column-oriented** (DuckDB, Snowflake, Redshift, Parquet files): each page contains values from a single column across many rows.

```
Page 1 (team_id column):  [eng, eng, eng, eng, sales, sales, sales, ...]
Page 2 (q01 column):      [4, 5, 3, 4, 5, 4, 3, ...]
Page 3 (q02 column):      [3, 4, 4, 5, 3, 4, 4, ...]
```

This physical layout difference is the root cause of everything that follows. It's not an abstraction — it determines which disk blocks the OS reads when you run a query.

### Why This Matters: "SELECT AVG(q01) FROM responses"

Row storage must read every page in the table because `q01` is scattered across all pages alongside `name`, `team_id`, `q02`-`q12`, and everything else. If the table has 15 columns, roughly 14/15 of the data read from disk is thrown away.

Column storage reads only the pages containing the `q01` column. That is 1/15th of the I/O. For a table with 50 columns where you query 3, that is 3/50th — a 16x reduction in data read.

> **B-tree Index** — The default index structure in most row-oriented databases (PostgreSQL, MySQL, SQLite). A B-tree is a self-balancing tree that keeps keys sorted and allows searches, insertions, and deletions in O(log n) time. For a point lookup like `WHERE id = 42`, the B-tree walks from root to leaf in 3-4 page reads regardless of table size, which is why row stores excel at these queries. Columnar stores rarely use B-trees because analytical queries scan ranges rather than seeking individual keys. ([PostgreSQL docs — B-tree Indexes](https://www.postgresql.org/docs/current/btree-intro.html))

For OLTP workloads — `SELECT * FROM responses WHERE id = 42` — row storage wins. The entire row lives in one page. Column storage must fetch one page per column and reassemble them, which is slower for point lookups.

## Why Columnar Is Faster for Analytics

Three reinforcing advantages stack on top of each other.

### Advantage 1: Read Only Needed Columns (Column Pruning)

As described above — columnar reads only the columns the query references. This is the most intuitive advantage and typically the largest contributor to speedup on wide tables.

Real-world survey tables can have 50-100 columns (12 Q12 scores, demographic fields, custom questions, metadata). A dashboard query that computes `AVG(q01)` grouped by `team_id` touches 2 columns out of 50+.

### Advantage 2: Better Compression (Homogeneous Data)

A column page contains values of the same type and often the same domain. `team_id` values are all short strings from a small set. `q01` values are all integers 1-5. `survey_date` values are all dates within a narrow range.

Same-type, similar-value data compresses dramatically better than mixed-type row data. Compression ratios of 5-10x are common for columnar storage vs 2-3x for row storage.

Less data on disk means less data to read, which means faster queries — compression compounds the column-pruning advantage.

### Advantage 3: Vectorized / SIMD Processing

> **SIMD (Single Instruction, Multiple Data)** — A CPU instruction-level parallelism model where one instruction operates on multiple data elements simultaneously. For example, an AVX-256 SIMD instruction can add eight 32-bit integers in a single cycle, versus eight separate ADD instructions in scalar code. Modern x86 CPUs (SSE, AVX2, AVX-512) and ARM (NEON) all provide SIMD registers and instructions. Database engines exploit SIMD by processing contiguous arrays of same-typed column values in bulk. ([Wikipedia — SIMD](https://en.wikipedia.org/wiki/Single_instruction,_multiple_data))

> **Vectorized Execution** — A query processing model where operators consume and produce batches (vectors) of values rather than one row at a time. This eliminates per-row function-call overhead (the main cost in the traditional Volcano/iterator model), enables SIMD, and keeps data in CPU cache across an entire batch. DuckDB, Velox, and DataFusion all use this model. ([DuckDB docs — Why DuckDB](https://duckdb.org/why_duckdb.html))

Modern CPUs have SIMD instructions (Single Instruction, Multiple Data) that can process multiple values in one CPU cycle. Adding 8 integers at once instead of one at a time.

Column storage naturally feeds SIMD: a contiguous array of `q01` integer values can be processed in batches. Row storage interleaves different types (`name` string, then `team_id` string, then `q01` integer), breaking the pattern SIMD needs.

DuckDB processes columns in batches of ~2048 values — sized to fit in the CPU's L2 cache (~256 KB). Each batch stays in fast cache memory while the CPU runs through it. This is called **vectorized execution**.

## Compression Techniques

> **Run-Length Encoding (RLE)** — A lossless compression algorithm that replaces consecutive runs of the same value with a single (value, count) pair. It is one of the simplest compression schemes and is most effective when input data has long runs of repeated values — which is exactly what happens in sorted low-cardinality columns in columnar storage. RLE can also operate on top of other encodings (e.g., delta-encoded values that produce runs of zeros). ([Wikipedia — Run-Length Encoding](https://en.wikipedia.org/wiki/Run-length_encoding))

### Run-Length Encoding (RLE)

Replace consecutive identical values with (value, count) pairs.

```
Raw:        [eng, eng, eng, eng, eng, sales, sales, sales]
RLE:        [(eng, 5), (sales, 3)]
```

Works best on **low-cardinality columns** — `team_id` (5-20 distinct values), `department` (3-10 values), `survey_period` (4-8 values). If data is sorted by that column, RLE is devastating: thousands of consecutive identical values collapse to a single pair.

#### Why NULLs Are Free in Columnar Storage

This is the insight that changes how you think about sparse survey data.

Consider a survey where only 5% of respondents answer an optional custom question. In a row-oriented table, every row still carries a NULL in that column — and every page read includes that NULL alongside real data.

In columnar storage with RLE, a column that is 95% NULL compresses to near-zero storage. The NULL runs collapse: `[(NULL, 950), (4, 1), (NULL, 12), (3, 1), (NULL, 36), ...]`. Long NULL runs are essentially free.

This challenges the common concern about "ever-widening tables" in survey data. Adding 20 custom questions that only 5% of respondents answer barely affects storage or query performance in a columnar warehouse. The NULLs compress away. In row storage, those 20 extra columns bloat every page whether they contain data or not.

> **Dictionary Encoding** — A compression technique that maps each distinct value in a column to a compact integer code and stores the mapping in a separate dictionary. Queries operate on the integer codes (smaller, faster to compare) and only decode back to the original value for output. This is particularly effective for string columns with low-to-moderate cardinality — a `department` column with 10 distinct values across 1 million rows shrinks dramatically. Most columnar formats (Parquet, ORC) and engines (DuckDB, Snowflake) apply dictionary encoding automatically. ([Apache Parquet — Encodings](https://parquet.apache.org/docs/file-format/data-pages/encodings/))

### Dictionary Encoding

Replace string values with integer codes, store the dictionary separately.

```
Dictionary: {0: "Engineering", 1: "Sales", 2: "Product"}
Raw:        ["Engineering", "Sales", "Engineering", "Product", "Engineering"]
Encoded:    [0, 1, 0, 2, 0]
```

Benefits beyond storage: integer comparisons are faster than string comparisons, so `WHERE department = 'Engineering'` becomes `WHERE dept_code = 0` internally. DuckDB and Parquet apply dictionary encoding automatically to string columns.

### Delta Encoding

Store the first value and then differences from each predecessor.

```
Timestamps: [1000, 1001, 1002, 1005, 1006]
Delta:      [1000, +1, +1, +3, +1]
```

The deltas are small numbers that compress well with further encoding. Works best on **sorted or naturally sequential columns** — timestamps, auto-increment IDs, dates.

### How They Combine

Real columnar engines layer these techniques. A `survey_date` column might be:
1. Delta-encoded (dates become small offsets)
2. Then bit-packed (small numbers need fewer bits)
3. Then optionally RLE'd if consecutive deltas are identical

The engine chooses automatically based on data statistics.

## Predicate Pushdown and Late Materialization

> **Predicate Pushdown** — An optimization where filter conditions (predicates) from a query's WHERE clause are pushed down from the query engine to the storage/scan layer, so rows that fail the filter are discarded before they consume memory or network bandwidth. In columnar formats with block-level min/max metadata (zone maps), pushdown can skip entire blocks without reading them. In distributed systems, pushdown reduces the data shipped between nodes. The term comes from relational algebra, where "pushing" a selection operator below a join reduces the number of rows the join processes. ([Wikipedia — Query Optimization](https://en.wikipedia.org/wiki/Query_optimization))

### Predicate Pushdown

"Push the WHERE clause down to the storage layer" — filter rows before they leave disk, rather than reading everything and filtering in memory.

```sql
SELECT AVG(q01) FROM responses WHERE team_id = 'Engineering'
```

Without pushdown: read all rows, then filter to Engineering, then average.
With pushdown: the storage layer skips pages that cannot contain 'Engineering' rows. Only matching rows are read into memory.

This is especially powerful with **min/max metadata** (also called zone maps). Columnar engines store the minimum and maximum value for each data block. If a block's team_id min is "Sales" and max is "Sales", the engine knows no "Engineering" rows exist in that block and skips it entirely without reading a single value.

### Late Materialization

Traditional query processing: read all columns for matching rows, then evaluate expressions. Late materialization: read only the column(s) needed for the WHERE clause first, identify which row positions match, then read the other columns only for those positions.

```sql
SELECT name, q01, q02 FROM responses WHERE team_id = 'Engineering'
```

1. Scan only `team_id` column, find positions [0, 1, 2, 5, 8, ...] that match
2. Read `name`, `q01`, `q02` only at those positions
3. If the WHERE clause eliminates 80% of rows, you just avoided reading 80% of the other columns

This compounds with column pruning: you read fewer columns, and for each column, you read fewer values.

### Clustering and Sort Keys

Predicate pushdown is most effective when the data is physically sorted by the column you filter on. If `team_id` is sorted, all "Engineering" rows are contiguous, and entire blocks can be skipped or included without checking individual values.

In Snowflake, this is done with **clustering keys**. In DuckDB, you can sort data on creation. In Parquet files, the row group ordering determines which predicates can skip effectively.

## Snowflake Micro-Partitions

Snowflake (the cloud warehouse) stores data in **micro-partitions**: immutable chunks of 50-150 MB, internally columnar and compressed.

Key properties:
- **Immutable.** Updates and deletes create new micro-partitions; old ones are garbage-collected. No in-place modification.
- **Automatic.** You don't manage partitions. Snowflake decides how to split data into micro-partitions based on ingestion order.
- **Min/max pruning.** Each micro-partition stores metadata about the min and max value of every column within that partition. A query like `WHERE survey_date = '2025-Q1'` can skip micro-partitions whose date range doesn't include Q1 2025 — potentially skipping 75% of data for a 4-quarter table.
- **Clustering keys.** If you define a clustering key (e.g., `CLUSTER BY (team_id)`), Snowflake physically reorganizes micro-partitions so rows with similar `team_id` values land in the same partitions. This maximizes pruning effectiveness for queries that filter on `team_id`.

The analogy: micro-partitions are like chapters in a book, and min/max metadata is like the index at the back. Instead of reading every page to find "Chapter 7," you check the index and jump directly to it.

## Vectorized Execution: DuckDB's Approach

Traditional row-at-a-time processing (Volcano model): each operator processes one row, calls the next operator for one row, and so on. This means a function call per row per operator — enormous overhead for millions of rows.

Vectorized execution: each operator processes a **batch** of values (DuckDB uses ~2048). The batch is a contiguous array of one column's values — essentially a vector.

Why 2048? DuckDB sizes batches to fit in the CPU's **L2 cache** (~256 KB). A batch of 2048 int32 values is 8 KB. A batch of 2048 float64 values is 16 KB. Multiple columns' batches fit in L2 simultaneously. Data stays in fast cache memory throughout processing — no slow main-memory fetches mid-computation.

The processing hierarchy from slowest to fastest:
- Python for-loop: interpreter overhead per element, no SIMD, no cache optimization
- pandas/NumPy: compiled C loops over arrays, some SIMD, but copies data into Python objects
- DuckDB: compiled vectorized execution, full SIMD, cache-optimized batch sizes, zero-copy

Orders-of-magnitude differences are typical: a mean over 10M values might take ~2 seconds in a Python loop, ~20 ms in pandas, and ~5 ms in DuckDB.

## What Columnar Does NOT Solve

Columnar storage is not a silver bullet. It optimizes data *access patterns* for analytical queries, but:

- **Schema design still matters.** A poorly designed star schema in a columnar warehouse is still slow and hard to query. Columnar doesn't fix bad grain decisions or missing dimensions.
- **Terrible for OLTP.** Inserting one survey response in a columnar store requires updating every column's storage separately. Row stores write one contiguous block. Point lookups (`WHERE id = 42`) require reassembling columns. Production applications should not use columnar databases as their primary store.
- **Clustering/sort key choices matter.** Predicate pushdown only helps if the data is physically organized to enable skipping. Random data layout gets no pruning benefit.
- **Not a replacement for indexing in all cases.** Columnar pruning works on ranges and equality. Complex predicates (full-text search, spatial queries) still need specialized indexes.
- **Compression can backfire for updates.** Compressed blocks must be decompressed, modified, and recompressed for writes. This is why columnar stores like Snowflake use immutable micro-partitions instead of in-place updates.
