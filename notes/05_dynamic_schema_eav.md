# Dynamic Schema and EAV Patterns

## The Problem: Surveys With Different Question Counts

A standard engagement survey has exactly 12 questions. If every survey project used exactly those 12 questions, you'd design a straightforward fact table with a `question_key` foreign key pointing to 12 rows in `dim_question`. Simple, clean, done.

But real survey deployments add custom questions. Project Phoenix runs the 12 standard items plus 2 custom questions about remote work. Project Atlas runs the same 12 plus 5 custom questions about leadership development. Project Orbit runs the 12 standard plus 3 about workplace safety. The question set is **variable per project**.

This creates a schema problem with no single right answer. The core tension: relational databases expect you to declare your schema upfront (columns, types, constraints), but the "shape" of your data varies across projects. Every approach trades something off.

## Approach 1: Entity-Attribute-Value (EAV)

> **EAV (Entity-Attribute-Value)** — A data modeling pattern that stores each fact as a triple: an entity identifier, an attribute name, and a value. Instead of one column per attribute (wide table), every attribute becomes a row. This trades query simplicity for schema flexibility -- no DDL changes to add attributes, but analytical queries require pivoting rows back into columns. ([Wikipedia](https://en.wikipedia.org/wiki/Entity%E2%80%93attribute%E2%80%93value_model))

EAV decomposes every data point into three columns: which entity has the value, which attribute it is, and what the value is.

```
response_id | attribute       | value
------------|-----------------|------
1001        | Q01_expectations| 4
1001        | Q02_materials   | 3
1001        | custom_remote_1 | 5
```

The mechanism: instead of one column per question, you have one *row* per question-answer pair. The `attribute` column holds the question identifier, the `value` column holds the answer. Adding a new question means inserting rows, not altering the table schema. Zero DDL changes, ever.

### Where EAV shines

- **Infinite flexibility.** New question types, new projects, entirely new survey instruments -- just insert more rows. No migrations, no downtime, no coordinator approval.
- **Uniform storage.** Every attribute looks the same to the storage engine. Easy to back up, replicate, partition by entity.
- **Works well for sparse data.** If Project A has 14 questions and Project B has 17, no wasted space on NULL columns.

### Where EAV hurts

- **Analytical queries become painful.** Computing "mean score per question per team" requires conditional aggregation or PIVOT:

```sql
-- EAV: getting a "wide" view requires one CASE per question
SELECT
    team_id,
    AVG(CASE WHEN attribute = 'Q01_expectations' THEN value END) AS q01_avg,
    AVG(CASE WHEN attribute = 'Q02_materials'    THEN value END) AS q02_avg
    -- ... repeat for every question
FROM eav_responses
GROUP BY team_id;
```

Compare to a wide table where this is just `AVG(q01_score), AVG(q02_score)`.

- **No type enforcement.** The `value` column is typically VARCHAR or DOUBLE -- it can't enforce that Q01 is a 1-5 integer while a custom text question is a string. You lose the database's type safety net.
- **Poor query performance at scale.** A query touching 1M responses across 15 questions scans 15M EAV rows instead of 1M wide rows. Indexes help but don't close the gap.
- **AI/text-to-SQL struggles.** An LLM generating SQL has to know the valid attribute names (they're data, not schema) and construct the PIVOT pattern correctly. This is brittle and error-prone.

### When to use EAV

Systems where schema changes are genuinely unpredictable and frequent, the primary access pattern is "get all attributes for entity X" (not analytics), and you can afford the query complexity tax. Classic examples: medical records (each patient has a different set of test results), product catalogs (each product category has different attributes), CMS field builders.

## Approach 2: Slot Mechanism

The slot mechanism pre-allocates a fixed number of generic columns: `question_slot_1`, `question_slot_2`, ... `question_slot_20`. A separate mapping table records what each slot means for each project.

```
-- Response table (fixed width)
response_id | project_id | slot_1 | slot_2 | ... | slot_20

-- Mapping table (gives meaning to slots)
project_id | slot_number | question_id  | question_text
-----------|-------------|--------------|---------------------------
Phoenix    | 1           | Q01          | I know what is expected...
Phoenix    | 14          | CUSTOM_RW_01 | Remote work is supported...
Atlas      | 1           | Q01          | I know what is expected...
Atlas      | 14          | CUSTOM_LD_01 | Leaders communicate vision...
```

The mechanism: slot columns are generic containers. The mapping table is the Rosetta Stone that translates "slot_7 in Project Phoenix" to "Q07: My opinions seem to count." The schema never changes; only the mapping data changes per project.

### Where slots work

- **Deterministic table width.** The response table always has 20 value columns. Query planners love this. Storage is predictable.
- **Relational queries are straightforward.** `AVG(slot_1)` works like any column aggregate -- no PIVOT gymnastics.
- **Schema stability.** Adding a new survey project means inserting rows in the mapping table, not running ALTER TABLE.

### Where slots break

- **AI/text-to-SQL cannot reason about "slot_7".** A human or AI looking at the response table sees `slot_7 = 3.5` and has no idea what question that answers without consulting the mapping table. Every query requires joining the mapping to decode the slot.
- **Slot exhaustion.** If you allocated 20 slots and a project needs 25 questions, you need a schema change. The "fixed width" advantage becomes a ceiling.
- **Maintenance burden.** The mapping table must be kept perfectly in sync with how data is loaded. A bug that maps Q03 to slot_4 in one project silently produces wrong answers with no type error.

### When to use slots

Legacy systems where schema changes are politically or technically expensive (mainframe-era survey platforms, heavily governed enterprise databases), and the maximum number of attributes is bounded and known.

## Approach 3: Semi-Structured / JSON Column

> **JSON/JSONB in PostgreSQL** — PostgreSQL supports two JSON column types: `JSON` stores the raw text and re-parses it on every access; `JSONB` stores a decomposed binary representation that is slower to ingest but faster to query, supports indexing (via GIN indexes), and strips duplicate keys and whitespace. For analytical access patterns, JSONB is almost always the right choice. ([PostgreSQL Docs](https://www.postgresql.org/docs/current/datatype-json.html))

> **Snowflake VARIANT** — Snowflake's semi-structured data type that can hold any value: objects, arrays, strings, numbers, booleans, and NULL. Internally stored in a columnar, compressed format that Snowflake can query without schema definition -- the structure is inferred at read time. It is the Snowflake equivalent of storing JSON/JSONB. ([Snowflake Docs](https://docs.snowflake.com/en/sql-reference/data-types-semistructured))

> **GIN (Generalized Inverted Index)** — A PostgreSQL index type designed for values that contain multiple elements (arrays, JSONB documents, full-text vectors). Instead of indexing a single scalar per row, GIN builds an inverted lookup from each contained element back to the rows that contain it -- the same structure a search engine uses to map words to documents. This is what makes JSONB key-existence and containment queries (`@>`, `?`, `?|`) fast. ([PostgreSQL Docs](https://www.postgresql.org/docs/current/gin-intro.html))

Modern analytical databases (DuckDB, Snowflake, BigQuery) support semi-structured data natively. Store responses as a JSON object:

```sql
-- Each row is one respondent's full response
response_id | project_id | team_id | responses_json
------------|------------|---------|----------------------------------------
1001        | Phoenix    | Alpha   | {"Q01": 4, "Q02": 3, ..., "CUSTOM_RW_01": 5}
1002        | Atlas      | Beta    | {"Q01": 5, "Q02": 4, ..., "CUSTOM_LD_01": 3, "CUSTOM_LD_02": 4}
```

The mechanism: the JSON column is schema-on-read. Each row can contain a different set of keys. The database doesn't enforce or even know about the keys until query time.

### Querying JSON in DuckDB

```sql
-- Extract a specific question's answer
SELECT response_id, responses_json->>'Q01' AS q01_score
FROM survey_responses;

-- Unnest to get EAV-like rows for aggregation
WITH keys_expanded AS (
    SELECT response_id, UNNEST(json_keys(responses_json)) AS question
    FROM survey_responses
)
SELECT response_id, question,
       CAST(json_extract(responses_json, '$.' || question) AS DOUBLE) AS score
FROM keys_expanded
JOIN survey_responses USING (response_id);
```

### Where JSON shines

- **Self-describing data.** The question identifiers live *in the data*, not in a separate mapping table. Looking at a row tells you what it contains. This is huge for debugging, for AI agents, and for ad-hoc exploration.
- **Zero-schema-change flexibility.** Same as EAV -- new questions appear as new keys, no DDL needed.
- **Natural fit for APIs.** Survey responses often arrive as JSON from web forms. Storing them directly skips the ETL step of reshaping into relational form.

### Where JSON hurts

- **Query syntax is more verbose** than columnar access. `responses_json->>'Q01'` vs just `q01_score`.
- **No column-level statistics.** The query optimizer can't build histograms on individual JSON keys, so predicate pushdown and join optimization suffer.
- **Type inference per query.** JSON values are strings until you cast them. You'll write `CAST(responses_json->>'Q01' AS DOUBLE)` repeatedly.

## Approach 4: Sparse Wide Table

> **Sparse Matrix/Table** — A data structure where most cells contain a default value (typically NULL or zero). The "sparsity" ratio -- how many cells are empty vs filled -- determines storage efficiency: row-oriented stores pay for every NULL slot, while columnar stores compress NULL-heavy columns to near-zero cost via run-length encoding. ([Wikipedia](https://en.wikipedia.org/wiki/Sparse_matrix))

This approach takes the opposite bet from EAV: give every possible question its own column. A project that doesn't use a question simply leaves that column NULL.

```sql
CREATE TABLE survey_responses_wide (
    response_id INTEGER,
    project_id  VARCHAR,
    team_id     VARCHAR,
    q01_score   DECIMAL(2,1),  -- Standard Q12
    q02_score   DECIMAL(2,1),
    ...
    q12_score   DECIMAL(2,1),
    custom_rw_01 DECIMAL(2,1),  -- Phoenix custom
    custom_rw_02 DECIMAL(2,1),
    custom_ld_01 DECIMAL(2,1),  -- Atlas custom
    ...                         -- All custom questions from all projects
);
```

Project Phoenix rows have values in `q01-q12` and `custom_rw_*` columns; the `custom_ld_*` columns are NULL. Project Atlas has the inverse pattern.

### Why this works in columnar storage

In a row-store (PostgreSQL, MySQL), sparse wide tables incur overhead because every row is stored contiguously -- the engine reads the full row from disk even if most columns are NULL, and the null bitmap still grows with column count. **In a columnar store (DuckDB, Parquet, Snowflake), NULL columns are essentially free.** Columnar engines store each column independently and compress NULL runs to near-zero bytes.

This is the critical insight: the sparse wide table is impractical in OLTP databases but efficient in analytical/columnar databases. The storage cost of adding 50 mostly-NULL columns in DuckDB is negligible.

### Where sparse wide works

- **Simplest possible queries.** `AVG(q01_score)` -- no joins, no JSON extraction, no PIVOT. The column name *is* the question identifier.
- **Full optimizer support.** Column statistics, predicate pushdown, vectorized execution -- everything the engine is built for.
- **AI/text-to-SQL friendly.** An LLM can read the column names and write correct queries without understanding EAV pivoting or JSON extraction.

### Where sparse wide breaks

- **Schema changes required.** Adding a new custom question means ALTER TABLE ADD COLUMN. In an automated pipeline this is manageable; in a governed enterprise environment it may need approval workflows.
- **Column sprawl.** Over years, you accumulate hundreds of question columns, most used by only one or two projects. The schema becomes hard to navigate manually.
- **Not truly dynamic.** If a new project can define arbitrary questions at runtime (user-generated forms), you can't ALTER TABLE fast enough. This is where EAV or JSON wins.

## Decision Framework

The right approach depends on four factors:

### Schema variability

- **Low variability** (question sets change quarterly, bounded total count): sparse wide table. Simple queries, full optimizer support.
- **Medium variability** (new question sets per project, bounded maximum): slot mechanism or JSON. Slots if you want relational simplicity; JSON if you want self-describing data.
- **High variability** (user-generated forms, unbounded attributes): EAV or JSON. EAV if you're in a relational-only ecosystem; JSON if your database supports it natively.

### Query patterns

- **Mostly entity lookup** ("show me respondent 1001's answers"): EAV and JSON both work well. This is their sweet spot.
- **Mostly analytics** ("mean score by team by question by quarter"): sparse wide table wins. Every other approach adds query complexity for analytical aggregation.
- **Mixed**: JSON with UNNEST offers a reasonable middle ground -- entity lookups are direct, analytics use UNNEST to get a relational view.

### Query consumers

- **Human analysts writing SQL**: any approach works; they'll learn the patterns.
- **AI agents / text-to-SQL**: sparse wide table is strongly preferred. Column names carry semantic meaning. EAV requires the AI to know valid attribute values and construct PIVOT patterns -- this is where most text-to-SQL systems fail.
- **BI tools (Tableau, Looker)**: prefer relational columns. JSON and EAV require preprocessing or view layers.

### Storage engine

- **Columnar (DuckDB, Snowflake, BigQuery, Parquet)**: sparse wide is cheap. NULLs compress away.
- **Row-store (PostgreSQL, MySQL)**: sparse wide is expensive. EAV or JSON may be more practical.

### For the engagement survey agent use case

The survey domain has bounded variability (12 standard + bounded custom), the primary pattern is analytics (mean scores, trends, comparisons), and the consumer is an AI agent. **The sparse wide table is the strongest fit**, with JSON as a reasonable second choice for its self-describing nature.
