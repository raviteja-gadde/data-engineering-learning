# Suppression and Access Control in Analytics

## Why Suppression Exists

Aggregated data can betray individual responses. The mechanism is simple arithmetic.

A team of 3 people reports a mean score of 2.0 on "I have a best friend at work." Two of them know they answered 3 and 4. The mean of 2.0 requires the third person answered... `(2.0 * 3) - 3 - 4 = -1`. Wait, that's impossible on a 1-5 scale, so let's use a real example: three people answered 1, 2, and 3. Mean = 2.0. If two people know their own answers (2 and 3), the third person's answer is `(2.0 * 3) - 2 - 3 = 1`. They've deduced the exact response of their colleague.

This isn't theoretical. In a 3-person team, any two members can always reconstruct the third's answer from the group average. With 4 people, the math still constrains possibilities enough to make good guesses. With 10 people, the signal dissolves into noise.

This is why every serious survey platform suppresses results below a minimum group size. Industry practice typically uses N >= 4. Some organizations use 5 or even 10 for sensitive questions. The principle applies far beyond surveys:

> **HIPAA / FERPA** — HIPAA (Health Insurance Portability and Accountability Act) governs the use and disclosure of protected health information by healthcare providers, insurers, and their business associates. FERPA (Family Educational Rights and Privacy Act) protects the privacy of student education records, giving parents and eligible students control over who can access grades, transcripts, and disciplinary records. Both mandate suppression or de-identification of data that could reveal individual identities. ([HIPAA — HHS.gov](https://www.hhs.gov/hipaa/index.html), [FERPA — ED.gov](https://www2.ed.gov/policy/gen/guid/fpco/ferpa/index.html))

- **HIPAA**: healthcare data must suppress cell sizes below a threshold to prevent patient identification
- **FERPA**: educational records have similar suppression requirements
- **Bureau of Labor Statistics**: suppresses employment data for small geographic/industry cells to protect employer identity
- **Census Bureau**: applies disclosure avoidance to any table where a cell represents too few respondents

The pattern is universal: when aggregate statistics are computed over small groups, the aggregation itself can be reversed to identify individuals.

## k-Anonymity: The Formal Framework

> **k-Anonymity** — A formal privacy model requiring that every record in a dataset be indistinguishable from at least k-1 other records on a set of quasi-identifier attributes. Originally proposed by Latanya Sweeney, it prevents re-identification by ensuring no individual can be uniquely singled out through combinations of non-sensitive attributes. ([Wikipedia — k-Anonymity](https://en.wikipedia.org/wiki/K-anonymity))

Suppression is an informal version of a well-studied privacy concept called **k-anonymity**.

### Definition

> **Quasi-identifiers** — Attributes in a dataset that are not unique identifiers on their own but can be combined to re-identify individuals. Classic examples include age, zip code, and gender — research has shown these three fields alone can uniquely identify 87% of the U.S. population. Quasi-identifiers are the attributes that k-anonymity policies target for generalization or suppression. ([Wikipedia — Quasi-identifier](https://en.wikipedia.org/wiki/Quasi-identifier))

A dataset satisfies k-anonymity if every combination of quasi-identifiers (attributes that could be used to identify someone) appears at least k times. A quasi-identifier is any attribute that, alone or combined with others, could narrow down who a record belongs to.

In a survey context:
- **Quasi-identifiers**: team, location, department, tenure band, gender
- **Sensitive attribute**: survey responses (the thing we want to protect)

When we say "suppress results where count < 4," we are enforcing k-anonymity with k=4 on whatever grouping dimensions the report uses.

### Why k Matters More Than You'd Think

k=1 means every person appears at least once — no protection at all. k=2 means each group has at least 2 people — an attacker who knows one person's answer can deduce the other's. k=4 (typical survey minimum) means even if an attacker knows their own answer, the remaining 3+ responses provide meaningful cover.

Higher k values provide more protection but reduce data granularity. A small organization with 5-person teams and 3 locations may find that k=4 suppresses most cross-tabulations, rendering the data useless. This is the fundamental tension: **privacy and granularity are in direct conflict**.

### Generalization vs Suppression

Two ways to satisfy k-anonymity:
- **Suppression**: hide the entire group's results (replace with NULL or "insufficient data")
- **Generalization**: roll up to a coarser grouping (show department-level instead of team-level results)

Survey platforms typically use suppression for display and let users navigate to a higher level themselves. Data platforms may implement automatic roll-up.

## Dynamic Suppression: Why It Must Be Computed Per Query

This is the insight that trips up most first implementations: **suppression is not a property of the data — it's a property of the data cut**.

Team Alpha has 10 people across 2 locations. At the team level, count = 10 — no suppression needed. But slice by location:
- Team Alpha, New York: 7 people — passes
- Team Alpha, Austin: 3 people — **suppressed**

Add another dimension — department:
- Team Alpha, New York, Engineering: 4 people — passes
- Team Alpha, New York, Product: 3 people — **suppressed**

The same underlying data passes or fails suppression depending on which dimensions appear in the GROUP BY. This means:

1. You cannot pre-compute a "suppressed" flag on the raw data
2. Every query or report must evaluate suppression at runtime based on its specific grouping
3. A dashboard with drill-down must re-evaluate suppression at each level

This is why suppression logic belongs in views, policies, or transformation layers — not in application code that runs after the query.

## Suppression as Data Property vs Application Logic

There are three places to enforce suppression, and they differ dramatically in reliability.

### Application-Level (Fragile)

```python
results = db.query("SELECT team, AVG(score) FROM responses GROUP BY team")
for row in results:
    if row.count < 4:
        row.avg_score = None  # suppress in Python
```

Problems: every consumer must implement suppression independently. A new dashboard, a CSV export, an API endpoint — each is a new place for the rule to be missed. One developer forgets, and unsuppressed data leaks.

### Database-Level View (Reliable)

```sql
CREATE VIEW team_scores_safe AS
SELECT team,
       COUNT(*) AS n,
       CASE WHEN COUNT(*) >= 4 THEN ROUND(AVG(score), 2) END AS avg_score
FROM responses
GROUP BY team;
```

Now every consumer querying `team_scores_safe` gets suppressed results automatically. The rule lives in one place. No one can accidentally bypass it by querying the view.

### Transformation-Level (dbt Model, ETL)

```sql
-- dbt model: team_scores.sql
SELECT team,
       COUNT(*) AS n,
       CASE WHEN COUNT(*) >= 4 THEN ROUND(AVG(score), 2) END AS avg_score
FROM {{ ref('stg_responses') }}
GROUP BY team
```

The suppression is baked into the data pipeline. Downstream tables already have NULLs where suppression applies. Version-controlled, tested, auditable.

### The Right Answer

Enforce as close to the data as possible. Database views or transformation-layer models are correct. Application-level suppression is a last resort when you cannot control the data layer.

The key principle: **suppression is a data integrity concern, not a presentation concern**. It should be enforced where data integrity is enforced — in the database or the pipeline, not in the UI.

## Row-Level Security (RLS)

> **Row-Level Security (RLS)** — A database feature that transparently filters rows returned by queries based on the identity or role of the executing user. The filter is applied by the database engine itself, not by application code, so it cannot be bypassed by writing different queries. PostgreSQL, SQL Server, and Snowflake all provide native RLS implementations. ([PostgreSQL — Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html))

Suppression controls *what data is shown at all*. Row-Level Security controls *who sees which rows*. Different problems, often needed together.

### The Problem RLS Solves

A manager should see their own team's survey results but not other teams'. Without RLS, you have two bad options:

1. **Separate tables per manager** — doesn't scale, nightmare to maintain
2. **Application-level filtering** — every query must include `WHERE team = current_user_team`, and one missed filter leaks all data

RLS moves the filter into the database engine itself. The query `SELECT * FROM scores` returns different rows depending on who executes it.

### PostgreSQL Implementation

```sql
-- Enable RLS on the table
ALTER TABLE team_scores ENABLE ROW LEVEL SECURITY;

-- Policy: managers see only their team
CREATE POLICY manager_team_access ON team_scores
    FOR SELECT
    USING (team_name = current_setting('app.current_team'));

-- Admin bypass: sees everything
CREATE POLICY admin_access ON team_scores
    FOR SELECT
    TO admin_role
    USING (true);
```

When `manager_a` runs `SELECT * FROM team_scores`, PostgreSQL silently appends the policy's `USING` clause. The manager never sees rows for other teams — not because the app filtered them, but because the database itself excluded them.

### Snowflake Row Access Policies

Snowflake uses a different syntax but the same concept:

```sql
CREATE ROW ACCESS POLICY team_visibility AS (team_name VARCHAR)
RETURNS BOOLEAN ->
    CASE
        WHEN IS_ROLE_IN_SESSION('ADMIN') THEN true
        WHEN team_name = CURRENT_ROLE() THEN true
        ELSE false
    END;

ALTER TABLE team_scores ADD ROW ACCESS POLICY team_visibility ON (team_name);
```

### RLS + Suppression Together

In a real survey platform, you need both:
1. **RLS** ensures Manager A cannot see Team B's data at all
2. **Suppression** ensures that even for their own team, if a sub-group is too small, the aggregate is hidden

These are complementary, not alternative, controls.

## Column Masking

> **Dynamic Data Masking** — A technique that hides or obfuscates sensitive column values at query time without altering the stored data. Different users see different representations of the same column (full value, partial mask, or NULL) based on their role or privilege level. Unlike encryption, the underlying data remains unchanged and fully accessible to authorized roles. ([SQL Server — Dynamic Data Masking](https://learn.microsoft.com/en-us/sql/relational-databases/security/dynamic-data-masking))

Sometimes the row is visible but specific column values should be hidden based on the viewer's role.

### Use Cases

- A manager can see team-level aggregates but not individual response scores
- An HR analyst can see all aggregates but not free-text comments
- An admin can see everything

### PostgreSQL Implementation

Column masking is typically implemented via views or functions rather than a built-in feature:

```sql
CREATE OR REPLACE FUNCTION mask_score(score NUMERIC, role TEXT)
RETURNS NUMERIC AS $$
BEGIN
    IF role = 'admin' THEN RETURN score;
    ELSE RETURN NULL;
    END IF;
END;
$$ LANGUAGE plpgsql;
```

Snowflake has native column-level masking policies:

```sql
CREATE MASKING POLICY score_mask AS (val NUMERIC)
RETURNS NUMERIC ->
    CASE
        WHEN IS_ROLE_IN_SESSION('ANALYST') THEN val
        ELSE NULL
    END;
```

## Policy-as-Code

When access control and suppression rules live in code (SQL, dbt macros, OPA policies), they become:

- **Version-controlled**: every change is tracked in git
- **Auditable**: you can prove what rules were in effect at any point in time
- **Testable**: CI can verify that suppression thresholds are applied, that RLS policies exist
- **Reviewable**: changes go through pull requests like any other code

### Implementation Patterns

- **SQL views/policies**: most direct, lives in the database
- **dbt macros**: `{{ suppress_below(4) }}` wraps the CASE WHEN pattern, ensuring consistency across all models
- **Open Policy Agent (OPA)**: external policy engine that evaluates access decisions. The query "can user X see team Y's data?" is evaluated by OPA, and the database query is constructed accordingly
- **Attribute-Based Access Control (ABAC)**: policies reference user attributes (role, team, department) and data attributes (sensitivity level, data classification) to make access decisions

### The dbt Macro Pattern

```sql
-- macros/suppress_below.sql
{% macro suppress_below(column, threshold=4) %}
    CASE WHEN COUNT(*) >= {{ threshold }} THEN {{ column }} END
{% endmacro %}

-- models/team_scores.sql
SELECT team,
       {{ suppress_below('ROUND(AVG(score), 2)') }} AS avg_score
FROM {{ ref('stg_responses') }}
GROUP BY team
```

One macro, one threshold definition, applied consistently everywhere.

## Industry Parallels

### HIPAA (Healthcare)

The Safe Harbor method requires suppression of cells with fewer than a threshold (often set at the "expert determination" level). Geographic data must be generalized to regions with 20,000+ population. Dates must be generalized to years for patients over 89. The suppression patterns in healthcare data are the most mature and well-documented.

### Bureau of Labor Statistics

BLS publishes employment statistics suppressed at multiple levels. If publishing county-level data would reveal a single employer's workforce, the entire county cell is suppressed. They also apply "complementary suppression" — if suppressing one cell makes another cell calculable by subtraction from a published total, the second cell must also be suppressed.

### FERPA (Education)

Student education records require suppression when cell sizes are small enough to identify individual students. Typical thresholds range from 5-10 depending on the institution and data type.

### Census Bureau

The Census Bureau's Disclosure Avoidance System is the most sophisticated suppression framework in existence. For the 2020 Census, they adopted differential privacy — adding calibrated noise to published statistics rather than suppressing cells. This is a more advanced approach than k-anonymity but solves the same fundamental problem: preventing individual identification from aggregate statistics.

## Key Takeaways

- Suppression exists because small-group aggregates can be reversed to identify individual responses
- k-anonymity formalizes the minimum group size requirement
- Suppression must be computed per query because it depends on the grouping dimensions, not the raw data
- Enforce suppression close to the data (views, policies, transformation layer) — never rely solely on application code
- Row-Level Security and suppression solve different problems and are both needed in multi-tenant analytics
- Policy-as-code (SQL policies, dbt macros, OPA) makes access rules auditable, testable, and version-controlled
- These patterns have decades of precedent in healthcare (HIPAA), education (FERPA), and government statistics (BLS, Census)
