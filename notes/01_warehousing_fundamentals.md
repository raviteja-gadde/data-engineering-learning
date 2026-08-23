# Data Warehousing Fundamentals

## What Is a Data Warehouse (and Why Not Just Use Your App Database?)

> **OLTP vs OLAP** — OLTP (Online Transaction Processing) systems handle high volumes of short, row-level reads and writes — the pattern behind any production application (insert a record, update a field, look up by primary key). OLAP (Online Analytical Processing) systems handle complex queries that scan and aggregate large volumes of data across many rows. The two workloads have fundamentally different access patterns, which is why they call for different database architectures. ([Wikipedia — OLAP](https://en.wikipedia.org/wiki/Online_analytical_processing))

Your app database (PostgreSQL, MySQL) is an **operational** or **transactional** database. It's optimized for the pattern your application needs: read one employee's record, insert one survey response, update one team's name. Thousands of small, fast operations per second. This is called **OLTP** — Online Transaction Processing.

A **data warehouse** is a separate database optimized for the opposite pattern: read *all* survey responses, join them with team and question metadata, group by quarter, and compute the average engagement score across the entire organization. One query touches millions of rows. This is called **OLAP** — Online Analytical Processing.

Why can't you just run analytics on your app database?

- **Performance conflict.** A query that scans 10 million survey responses and groups by team will lock rows, consume memory, and slow down the app. Your users submitting surveys right now will see timeouts.
> **Denormalization** — Normalization (3NF and beyond) eliminates data redundancy by splitting data across many related tables, which is ideal for write-heavy transactional workloads. Denormalization deliberately reintroduces redundancy — pre-joining and flattening data into fewer, wider tables — to reduce the number of joins at query time. Analytical workloads read far more than they write, so the tradeoff favors read speed over storage efficiency. ([Wikipedia — Denormalization](https://en.wikipedia.org/wiki/Denormalization))

- **Schema mismatch.** App schemas are normalized for write efficiency (third normal form, foreign keys everywhere). Analytics needs denormalized, wide tables you can scan quickly.
- **Historical data.** App databases reflect *current state*. When Team Alpha merges with Team Beta, the old team name vanishes. A warehouse preserves history so you can answer "what were Team Alpha's scores before the merger?"
- **Multiple sources.** Engagement survey data might live in one system, HRIS data in another, project tracking in a third. A warehouse consolidates them into one queryable place.

In the survey domain: your survey application stores individual responses as users submit them. The data warehouse takes those responses and structures them for questions like "show me engagement trends by department over the last 4 quarters, broken down by question category."

## Star Schema: The Core Pattern

A **star schema** is the most common way to organize data in a warehouse. It has two types of tables:

**Fact tables** store measurements — things you count, sum, or average. Each row represents one event or observation. In survey data, each row in `fact_survey_responses` is one person's answer to one question:

```
fact_survey_responses
─────────────────────
response_id (PK)
team_key (FK → dim_team)
project_key (FK → dim_project)
question_key (FK → dim_question)
time_period_key (FK → dim_time_period)
score (1-5)           ← the measure
response_count (1)    ← always 1 per row, useful for counting
```

**Dimension tables** store descriptive attributes — the "who, what, when, where" context around each fact. They are the axes you slice your analysis by:

```
dim_team                    dim_question
────────                    ────────────
team_key (PK)               question_key (PK)
team_name                   question_id (Q01-Q12)
department                  question_text
location                    category (Basic Needs, Individual, Teamwork, Growth)
```

> **Likert Scale** — A psychometric response scale where respondents rate their agreement with a statement, typically on a 1-to-5 or 1-to-7 range (e.g., 1 = Strongly Disagree, 5 = Strongly Agree). The resulting data is ordinal — the intervals between points are not guaranteed equal — but it is routinely treated as interval data for computing means and running parametric statistics. ([Wikipedia — Likert Scale](https://en.wikipedia.org/wiki/Likert_scale))

The question dimension uses a 12-item engagement survey framework — 12 validated engagement questions grouped into four categories: Basic Needs (Q01-Q02), Individual contribution (Q03-Q06), Teamwork (Q07-Q10), and Growth (Q11-Q12). Each maps to a 1-5 Likert scale. This structure is realistic: Basic Needs questions ("I know what is expected of me") tend to score higher (3.8-4.2), while Teamwork items like "I have a best friend at work" score lower (2.8-3.5).

```
dim_project                 dim_time_period
───────────                 ───────────────
project_key (PK)            time_period_key (PK)
project_name                period_label (Q1 2025, Q2 2025)
                            year
                            quarter
```

It's called a "star" because the fact table sits in the center with dimension tables radiating outward like points of a star.

### Worked Example: "Mean Engagement Score for Team Alpha in Q1 2025"

This is the bread and butter of star schema querying. You join the fact table to the dimensions you need, filter, and aggregate:

```sql
SELECT
    dt.team_name,
    dtp.period_label,
    ROUND(AVG(f.score), 2) AS mean_score
FROM fact_survey_responses f
JOIN dim_team dt         ON f.team_key = dt.team_key
JOIN dim_time_period dtp ON f.time_period_key = dtp.time_period_key
WHERE dt.team_name = 'Team Alpha'
  AND dtp.period_label = 'Q1 2025'
GROUP BY dt.team_name, dtp.period_label;
```

What's happening:
1. Start from `fact_survey_responses` — every individual response
2. Join `dim_team` to get the team name (the fact table only has `team_key`, a numeric ID)
3. Join `dim_time_period` to get the period label
4. Filter to the team and period you care about
5. `AVG(f.score)` computes the mean across all responses matching those filters

To slice differently — say, by question category — you join `dim_question` and group by `dq.category` instead. The star schema makes every combination of slicing available through the same pattern: join the relevant dimension, filter or group by its attributes.

## Snowflake Schema: When Dimensions Have Dimensions

A **snowflake schema** takes star schema dimensions and normalizes them further. Instead of `dim_team` containing `department` and `location` directly:

```
Star schema (denormalized dimension):
dim_team: team_key, team_name, department, location

Snowflake schema (normalized dimensions):
dim_team:       team_key, team_name, department_key, location_key
dim_department: department_key, department_name, division
dim_location:   location_key, city, region, country
```

A query that groups by department now requires an extra join hop through the normalized hierarchy:

```sql
-- Star: 1 join  (fact → dim_team, which already has department)
-- Snowflake: 2 joins (fact → dim_team → dim_department)
```

### When to Use Snowflake

- **Avoids redundancy.** If 50 teams share 5 departments, the department name is stored 5 times in snowflake vs. duplicated across 50 team rows in star.
- **Cleaner updates.** Renaming a department means updating one row in `dim_department` instead of 50 rows in `dim_team`.

### Why Star Is Usually Preferred

- **Simpler queries.** Fewer joins means easier-to-write and easier-to-debug SQL.
- **Better performance.** Warehouse query engines optimize for wide, denormalized scans. The extra joins in snowflake schemas can slow queries down.
- **Disk is cheap.** The redundancy star schema introduces is trivial compared to query simplicity.

In practice, most data warehouses use star schemas. Snowflake schemas appear when a dimension has deeply nested hierarchies (like `team → department → division → business_unit → company`).

## Grain: The Most Important Design Decision

> **Grain** — In dimensional modeling, the grain (or granularity) of a fact table defines what a single row represents — the finest level of detail captured. Setting the grain is the first and most consequential decision in fact table design, because it determines which questions the table can answer and which aggregations are valid. Every dimension foreign key and every measure must be consistent with the declared grain. ([Kimball Group — Declaring the Grain](https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/grain/))

The **grain** of a fact table is the answer to: "What does one row represent?"

In `fact_survey_responses`, the grain is: **one person's response to one question in one time period for one team and project.**

This sounds obvious, but getting grain wrong is the single most common data warehouse design mistake, and it silently produces wrong numbers.

### Wrong Grain Example

Suppose you design a fact table where one row = one team's average score:

```
fact_team_scores (WRONG GRAIN)
──────────────────────────────
team_key
time_period_key
avg_score        ← pre-aggregated
response_count
```

Team Alpha has 50 responses, Team Beta has 10. Now someone asks: "What's the overall average score?"

```sql
-- This gives you the average of averages, NOT the overall average
SELECT AVG(avg_score) FROM fact_team_scores;
```

If Team Alpha's average is 4.0 (from 50 responses) and Team Beta's is 3.0 (from 10 responses), this returns 3.5. But the correct overall average — weighted by actual responses — is (4.0*50 + 3.0*10)/60 = 3.83.

**The fix:** always store data at the finest grain. One row per individual response. You can always aggregate up from fine grain; you cannot disaggregate from coarse grain.

## Measures vs Dimensions

**Measures** are the numbers you aggregate: `score`, `response_count`, `completion_time_seconds`. They answer "how much?" or "how many?"

**Dimensions** are the axes you slice and filter by: `team_name`, `question_category`, `period_label`, `department`. They answer "for which?" or "broken down by?"

A useful test: if you'd put it in a `GROUP BY` or `WHERE` clause, it's a dimension. If you'd wrap it in `SUM()`, `AVG()`, or `COUNT()`, it's a measure.

Some fields are ambiguous. `score` could be grouped by ("show me the distribution of scores"), but its primary purpose is aggregation, so it's a measure. When in doubt, the *primary use* determines the classification.

## Surrogate Keys vs Natural Keys

> **Surrogate Key** — A synthetic identifier generated by the warehouse (typically an auto-incrementing integer) that has no business meaning. Unlike natural keys (employee ID, email), surrogate keys are warehouse-internal: they are immune to source-system changes, support multiple versions of the same entity (critical for SCD Type 2), and yield faster integer joins. Fact tables reference dimension rows by surrogate key, while dimensions retain the natural key for traceability. ([Wikipedia — Surrogate Key](https://en.wikipedia.org/wiki/Surrogate_key))

A **natural key** is an identifier that already exists in the source system: employee ID, team code, email address.

A **surrogate key** is an auto-incrementing integer that the warehouse generates: `team_key = 1, 2, 3, ...`

Warehouses prefer surrogate keys because:

- **Source systems change.** If HR renumbers employee IDs, natural keys break. Surrogate keys are warehouse-internal and stable.
- **Multiple sources.** Two source systems might both have a "Team Alpha" but with different IDs. Surrogate keys let you map both.
- **SCD support.** When Team Alpha restructures (see Slowly Changing Dimensions below), you might have two rows in `dim_team` — the old structure and the new one. They need different keys. A surrogate key gives each version its own identity.
- **Join performance.** Integer joins are faster than string joins.

The dimension table keeps both: `team_key` (surrogate, PK) and `team_code` (natural, for traceability back to the source system).

## Materialized Views: Pre-Computing Results

A **regular view** is a saved SQL query. Every time you query it, the database runs the underlying SQL fresh. No storage cost, but re-computes every time.

A **materialized view** is a saved SQL query *plus its results stored as a table*. The first time it runs, it executes the query and stores the result. Subsequent reads just scan the stored result — fast.

The tradeoff is **staleness.** When new survey responses arrive, the materialized view still shows the old result until you explicitly refresh it.

```sql
-- Conceptual (DuckDB uses CREATE TABLE AS for this pattern)
CREATE TABLE mv_team_scores AS
SELECT team_name, period_label, AVG(score) AS mean_score
FROM fact_survey_responses f
JOIN dim_team dt ON f.team_key = dt.team_key
JOIN dim_time_period dtp ON f.time_period_key = dtp.time_period_key
GROUP BY team_name, period_label;

-- Fast reads: just scan the pre-computed table
SELECT * FROM mv_team_scores;

-- After new data arrives: drop and recreate (or use TRUNCATE + INSERT)
```

### Refresh Strategies

- **Full rebuild:** Drop and recreate. Simple, works at any scale a warehouse handles, but expensive for huge tables.
- **Incremental refresh:** Only process rows added since last refresh. Complex to implement correctly (you need to track what's "new").
- **Scheduled refresh:** Rebuild on a cron schedule (nightly, hourly). Accepts a known staleness window.

In the survey domain, materialized views are perfect for dashboard queries. Surveys don't arrive continuously — they come in batches after a survey closes. You can rebuild materialized views after each survey batch.

## Slowly Changing Dimensions (SCD)

> **Slowly Changing Dimension (SCD)** — A dimension whose attribute values change over time, but infrequently relative to the fact data being recorded. The "slowly" distinguishes these from rapidly changing dimensions (like a real-time stock price) and from dimensions that never change. The challenge is how the warehouse should handle these changes: lose history, preserve full history, or track limited history. Ralph Kimball defined the standard SCD type taxonomy (Types 0-7) that most warehouses follow. ([Wikipedia — Slowly Changing Dimension](https://en.wikipedia.org/wiki/Slowly_changing_dimension))

Real-world entities change over time. Team Alpha's manager changes, an employee moves departments, a question gets reworded. How does the warehouse handle this?

### SCD Type 1: Overwrite

Simply update the dimension row with the new value. The old value is lost.

```
Before: dim_team row: team_key=1, team_name='Team Alpha', department='Engineering'
After:  dim_team row: team_key=1, team_name='Team Alpha', department='Product'
```

Use when: you don't care about history. Fixing a typo in a team name is Type 1.

### SCD Type 2: Add a New Row with Versioning

Create a new row for the new version. The old row stays. Each row has `valid_from` and `valid_to` dates that mark when that version was the "truth."

```
team_key=1, team_name='Team Alpha', department='Engineering', valid_from='2024-01-01', valid_to='2025-06-30'
team_key=7, team_name='Team Alpha', department='Product',     valid_from='2025-07-01', valid_to='9999-12-31'
```

(The `9999-12-31` sentinel means "currently active.")

Now you can answer both:
- "What department was Team Alpha in during Q1 2025?" → Engineering (use `valid_from`/`valid_to` to find the version active during that period)
- "What department is Team Alpha in now?" → Product (find the row where `valid_to = '9999-12-31'`)

The fact table's `team_key` foreign key points to the specific version that was active when the response was recorded. Old responses point to `team_key=1` (Engineering era), new responses point to `team_key=7` (Product era).

This is the most important SCD type for survey analytics. When a team restructures, you need to show "results under the old structure" and "results under the current structure" — SCD Type 2 makes both queries possible from the same data.

### SCD Type 3: Add a Column

Add a `previous_department` column to the dimension row. You keep one row, but only one level of history.

```
team_key=1, team_name='Team Alpha', department='Product', previous_department='Engineering'
```

Use when: you only need to know the immediately prior value, and deeper history doesn't matter. Rarely used in practice because it doesn't scale to multiple changes.

## Additive, Semi-Additive, and Non-Additive Facts

Not all measures can be aggregated the same way across all dimensions.

### Additive Facts

Can be summed across *any* dimension. `response_count` is additive:
- Sum across teams: total responses in the company. Correct.
- Sum across time periods: total responses across all periods. Correct.
- Sum across questions: total responses across all questions. Correct (if each row is one response to one question).

### Semi-Additive Facts

Can be summed across *some* dimensions but not others. `score` (when averaged) is semi-additive:
- Average across questions for one team: correct (mean engagement score for Team Alpha).
- Average across teams: **only correct if all teams have the same number of responses.** Otherwise you need a weighted average.

Account balances are the classic example: you can sum them across accounts (total cash), but not across time (summing January balance + February balance is meaningless).

### Non-Additive Facts

Cannot be meaningfully summed across any dimension. Percentiles and ratios are non-additive:
- Team Alpha's 90th percentile score is 4.5, Team Beta's is 4.0. The overall 90th percentile is NOT (4.5 + 4.0) / 2. You must recompute from the raw responses.

This is why grain matters: if you store individual responses (finest grain), you can always compute the correct aggregate. If you store pre-computed percentiles, you're stuck.

## The Survey Data Challenge

Employee engagement survey data is significantly harder to warehouse than classic examples like e-commerce (orders, products, customers). Here's why:

### Dynamic Questions

E-commerce products change slowly. Survey questions change every cycle. New questions are added, old ones are retired, wording is revised. The `dim_question` dimension grows and mutates in ways that `dim_product` rarely does. Trend analysis ("how has engagement changed over 3 years?") requires mapping old questions to new ones.

### Hierarchical Teams

Teams nest: `Team Alpha → Engineering Department → Technology Division → Company`. When Team Alpha moves from Engineering to Product, every level of the hierarchy shifts. SCD Type 2 must track changes at every level, and queries must join through the correct version of the hierarchy for each time period.

### Anonymity and Suppression

If Team Gamma has only 3 members, showing their average score could identify individual respondents. Engagement surveys enforce **suppression rules**: results for groups smaller than a threshold (typically 5) are hidden. This isn't a simple `WHERE count >= 5` — suppression cascades. If you suppress one team's data, the department rollup also changes.

### Reverse-Scored Questions

"I feel burned out at work" is scored in reverse: a 5 (strongly agree) is actually a bad engagement signal. Some questions must be flipped before aggregation. This transformation belongs in the warehouse pipeline, not the reporting layer, because inconsistently applying it produces wrong results.

### Pre-Computed Aggregations

Survey platforms often deliver pre-computed results (mean scores per team per question) rather than raw individual responses. This creates the grain problem discussed earlier: you're working with a coarser grain than you'd like, and certain calculations (like re-slicing by a different dimension, or computing weighted averages across groups) become impossible or incorrect.

These challenges make survey data warehousing a genuinely interesting domain — it exercises every concept in this topic: grain decisions, SCD for team restructures, careful measure classification, and thoughtful materialized view design.
