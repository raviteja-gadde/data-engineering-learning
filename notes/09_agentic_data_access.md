# Agentic Data Access Patterns

## The Agent's Data Access Problem

An AI agent reasoning over analytical data faces a fundamentally different problem than a dashboard. A dashboard renders pixels — charts, heatmaps, tables. An agent reasons in text. This changes what data access means.

When a user asks an agent "how is my team doing on engagement?", the agent needs to:

1. **Identify scope** — which team, which project, which time period
2. **Retrieve structured data** — scores, trends, comparisons
3. **Interpret the data** — what's high, what's low, what changed
4. **Compose a response** — natural language synthesis of findings

Steps 2-4 all happen inside the agent's context window. The data must be:
- **Complete enough** to answer the question without a second round-trip
- **Compact enough** to fit in the context window alongside the conversation and system prompt
- **Annotated enough** that the agent can interpret it correctly (what does 3.8 mean? is that good?)

A dashboard doesn't have these constraints. It can lazy-load panels, paginate tables, let users drill down interactively. The agent gets one shot: the tool returns data, the agent reasons over it, done.

### What "Reasoning Over Data" Actually Means

An LLM doesn't compute — it pattern-matches over text. When an agent sees `{"mean_score": 3.8, "benchmark": 4.0, "trend": -0.2}`, it can say "below benchmark and declining." But give it 500 rows of individual responses and ask for a statistical analysis — it will hallucinate patterns or miss real ones. The agent's strength is synthesis and narrative, not computation.

This means the data layer must do the computation (aggregation, trend calculation, benchmark comparison) and hand the agent pre-digested results. The agent's job is interpretation and communication.

## Tool-Mediated Data Access

The agent accesses data through **tools** — typed functions it can call. The agent never writes SQL, never knows the schema, never connects to a database. It calls a function with parameters and gets back structured data.

This is not just an implementation convenience. It is a **hard architectural boundary**.

### Why the Agent Must Never Touch SQL

- **Security.** An agent that writes SQL can be prompt-injected into running `DROP TABLE` or `SELECT * FROM salaries`.

> **Parameterized Queries** — A technique where SQL statements use placeholders (`$1`, `%s`, `?`) instead of interpolating user input directly into the query string. The database driver sends the query structure and parameters separately, so user-supplied values can never be interpreted as SQL syntax — eliminating SQL injection by construction. Every major database client library supports them. ([OWASP — Query Parameterization](https://cheatsheetseries.owasp.org/cheatsheets/Query_Parameterization_Cheat_Sheet.html))
- **Correctness.** Survey schemas are subtle — EAV pivots, suppression logic, period boundaries. An agent guessing at SQL will get it wrong in ways that are plausible but incorrect.
- **Stability.** Schema changes break SQL. A tool contract can absorb schema changes internally while presenting the same interface to the agent.
- **Testability.** A typed function with defined inputs and outputs is testable. "Whatever SQL the LLM decided to write" is not.

### Tool Contracts as Stable Interfaces

> **Interface / Protocol (Python)** — Python achieves interface-like contracts through three mechanisms: `typing.Protocol` (PEP 544) defines structural subtyping — any class with matching method signatures satisfies the protocol without explicit inheritance. Abstract Base Classes (`abc.ABC` with `@abstractmethod`) enforce contracts via inheritance, raising `TypeError` if a subclass omits a required method. Duck typing is the informal version — if it has the right methods, it works. `Protocol` is preferred for tool contracts because it decouples the contract from the implementation hierarchy. ([Python Docs — typing.Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol))

A tool contract is a function signature:

```
get_team_overview(team_id, project_id, time_period) -> TeamOverview
```

The contract specifies:
- **Input parameters** — what scope the caller must provide
- **Output structure** — exactly what fields come back
- **Error states** — what can go wrong and how it's signaled

The contract stays the same regardless of whether the implementation reads from DuckDB, PostgreSQL, Snowflake, a cache, or an API. The agent calls the same function with the same parameters and gets the same shape of response. The entire data infrastructure can change underneath without the agent knowing.

This is the same principle as any stable API boundary, but it matters more here because the "client" is an LLM. A human developer can read an error traceback and adapt. An agent seeing unexpected data shapes will silently produce wrong answers.

## Tool Contract Design for Analytical Data

### Input: Scope Parameters

Every analytical query operates within a scope — who is asking, about what group, over what time period. The tool contract makes this explicit:

> **Dataclass (Python)** — Python's `@dataclass` decorator (from the `dataclasses` module, stdlib since 3.7) auto-generates `__init__`, `__repr__`, and comparison methods from annotated class fields. It provides a concise way to create typed data containers without boilerplate, and supports defaults, immutability (`frozen=True`), and post-init processing. Preferred over plain dicts when you want IDE autocompletion, type checking, and self-documenting structure. ([Python Docs — dataclasses](https://docs.python.org/3/library/dataclasses.html))

```python
@dataclass
class TeamOverviewRequest:
    team_id: str
    project_id: str
    time_period: str  # "2025-Q1"
```

Scope parameters are the contract's primary mechanism for controlling what data the agent sees. They replace the WHERE clause the agent would write if it had SQL access.

### Output: Structured Data + Metadata

The output is not just the numbers. It includes everything the agent needs to interpret the numbers correctly:

```python
@dataclass
class TeamOverview:
    team_name: str
    team_id: str
    project_name: str
    time_period: str
    overall_score: float
    category_scores: list[CategoryScore]  # 4 categories, 12 questions
    response_count: int
    response_rate: float
    freshness: str          # ISO timestamp of when this data was computed
    suppressed: bool        # True if count < threshold
    benchmark: float | None # company-wide comparison, if available
```

Key design decisions:

- **Scope context is echoed back.** The response includes `team_name`, `project_name`, `time_period` — not just the IDs the agent sent. This lets the agent say "Team Alpha's Q1 2025 results" without a second lookup.
- **Freshness timestamp.** The agent can say "as of this morning's data refresh" instead of implying real-time accuracy.
- **Suppression signal.** The agent must know when data is suppressed so it can say "results are suppressed for privacy" rather than showing zeros or nulls and guessing why.
- **Benchmark.** Without context, "3.8" is meaningless. With a benchmark of 4.0, the agent can say "below company average."

### Error States

The agent needs to distinguish different failure modes because each requires a different response:

- **`not_entitled`** — user doesn't have permission to see this team's data. Agent says "you don't have access to that team's results."
- **`suppressed`** — data exists but group is too small. Agent says "results are suppressed to protect respondent anonymity (fewer than 4 responses)."
- **`no_data`** — no survey responses exist for this scope. Agent says "no survey data found for that team and period."
- **`stale_cache`** — data is available but older than expected. Agent can proceed but should note "this data is from last week's refresh."

Returning all of these as generic exceptions or empty results forces the agent to guess. Distinct error types let it give precise, helpful responses.

## Scope Resolution

When a user says "how is my team doing?", the agent doesn't have a `team_id`. It has "my team." This is the scope resolution problem.

### Two Architectures, Two Approaches

**Widget/Embedded (agent lives inside an app):** The page already knows the user's team. The application passes `team_id`, `project_id`, and `time_period` as part of the agent's system prompt or tool context. The agent never needs to ask.

**Standalone Chat (agent lives in a chat interface):** The agent must discover scope through conversation:

1. Call `get_team_list(user_id)` — returns 3 teams
2. Present options: "I see you're on three teams: Alpha, Beta, and Gamma. Which one?"
3. User says "Alpha"
4. Call `get_project_list(user_id)` — returns active projects
5. Select the most recent survey period automatically (or ask if ambiguous)
6. Now the agent has full scope: `team_id="alpha"`, `project_id="phoenix"`, `time_period="2025-Q2"`

The scope resolution tools (`get_team_list`, `get_project_list`) are as much a part of the tool contract as the data-fetching tools. Without them, the standalone agent can't function.

### Default Scope

For most users, there's an obvious default: their primary team, the active project, the most recent completed survey period. A `get_default_scope(user_id)` tool that returns this eliminates the disambiguation step for the common case. The agent only needs to ask when the user's situation is genuinely ambiguous.

## Caching at the Tool Layer

Same team, same project, same period — queried twice in one conversation. Should the tool hit the database twice?

> **TTL-Based Caching** — A caching strategy where each cached entry is stored with a time-to-live (TTL) — a fixed duration after which the entry expires and must be re-fetched from the source. TTL avoids explicit invalidation logic: the cache self-cleans on a schedule. The tradeoff is staleness — a short TTL means more cache misses and source queries, while a long TTL risks serving outdated data. ([Wikipedia — Cache (computing)](https://en.wikipedia.org/wiki/Cache_(computing)))

No. Tool-level caching is a simple dict keyed on the request parameters with a time-to-live (TTL).

```
cache_key = (team_id, project_id, time_period)
if cache_key in cache and not expired:
    return cache[cache_key]  # <1ms
else:
    result = query_database(...)  # ~50ms
    cache[cache_key] = (result, now)
    return result
```

### Why Cache at the Tool Layer, Not Below

Database-level caching (query cache, buffer pool) exists but doesn't help here. The tool layer is where we know the semantic meaning of the request — "same team, same period" — and can make intelligent caching decisions.

Tool-level caching also guarantees **intra-conversation consistency**. If the agent asks about Team Alpha twice in one conversation, it gets the same numbers both times. Without caching, a data refresh between calls could produce different numbers within the same conversation — confusing for the user.

### TTL Design

Survey data is inherently batch — it changes when a new survey closes or a refresh runs, not continuously. A TTL of 5-15 minutes is appropriate for survey data. For real-time metrics, shorter TTLs or event-driven invalidation.

## Context Window Constraints

An LLM has a fixed context window. The conversation, system prompt, tool definitions, and tool results all compete for that space. How much room does analytical data actually take?

### Measuring Data Shapes

> **Token (LLM Context)** — In large language models, a token is a sub-word unit produced by the model's tokenizer — typically 3-4 English characters or roughly 0.75 words. Models have a fixed context window measured in tokens (e.g., 128K tokens) that must hold the system prompt, conversation history, tool definitions, and tool results combined. Longer inputs consume more of this budget, leaving less room for the model's response. ([Wikipedia — Large Language Model](https://en.wikipedia.org/wiki/Large_language_model))

A rough token estimate: `len(json_string) / 4` characters per token.

**Overview level** (4 categories, 12 questions with scores):
- JSON size: ~1-2 KB
- Estimated tokens: ~300-500
- Agent reasoning quality: excellent — flat, complete, interpretable
- Recommended: yes, this is the default shape

**Detailed level** (all metrics per question — mean, median, std, percentiles):
- JSON size: ~4-8 KB
- Estimated tokens: ~1,000-2,000
- Agent reasoning quality: good, but agent may ignore half the fields
- Recommended: only when the user asks for detail

**Full dimensional** (categories x questions x all scale values):
- JSON size: ~20-50 KB
- Estimated tokens: ~5,000-12,000
- Agent reasoning quality: degrades — agent starts summarizing rather than reasoning
- Recommended: no

**Raw responses** (individual respondent rows):
- JSON size: ~100 KB+ for even moderate teams
- Estimated tokens: ~25,000+
- Agent reasoning quality: poor — agent cannot do meaningful statistical analysis
- Recommended: never send to an agent

### The Right Default Shape

Flat, complete-for-the-context is the right shape. For survey data, that means: 4 category scores, 12 question scores, overall score, response count, trend indicators. One level of hierarchy (categories contain questions), no deeper nesting. Everything the agent needs to give a comprehensive answer, nothing it can't reason over.

The agent tool should return this shape by default. If the user asks to "drill into the Teamwork category," a second tool (or the same tool with a `detail_level` parameter) returns the deeper cut.

## Non-Contradiction Requirement

When an agent and a dashboard appear on the same screen — the agent in a chat panel, the dashboard in the main view — they must show the same numbers. A user who sees "3.8" on the dashboard and the agent says "3.7" will lose trust in both.

### How Contradiction Happens

**Scenario: independent computation.** The dashboard queries PostgreSQL at 10:00:01. The agent's tool queries DuckDB at 10:00:03. Between those two moments, a data pipeline refreshed the scores. Dashboard shows old numbers, agent shows new ones. Neither is wrong — they're just inconsistent.

**Scenario: different aggregation logic.** The dashboard computes mean score with one rounding rule. The tool's SQL uses a slightly different formula. They diverge by 0.1 on some teams. Small, but visible.

### The Fix: Shared Serving Layer

Both consumers — dashboard and agent — read from the **same pre-computed serving table**. Not "both run the same SQL." The same table. Same rows. Same values.

This is the Gold layer from the medallion architecture (Topic 3). The serving table is computed once by the pipeline, written to a single location, and read by all consumers. Consistency is guaranteed by construction, not by hoping two independent computations produce the same result.

The agent's tool implementation becomes: `SELECT * FROM gold_team_scores WHERE team_id = ? AND period = ?`. The dashboard's API does the same query. Same table, same rows, same numbers.

### When Freshness Conflicts With Consistency

If the agent's tool bypasses the serving table to get "fresher" data, it breaks consistency. The right answer: don't. If freshness matters, refresh the serving table more frequently. The single-source-of-truth principle is worth the latency.

## Synthesis: How Topics 1-8 Converge Here

This topic is where every prior topic becomes concrete:

- **Topic 1 (Warehousing):** The star schema is the data model the tool queries
- **Topic 2 (Columnar):** DuckDB's columnar format makes analytical queries fast enough for real-time tool calls
- **Topic 3 (Medallion/dbt):** The Gold serving layer is what both agent and dashboard read from
- **Topic 4 (Pre-computation):** The serving table is pre-computed — the tool never runs raw aggregations at query time
- **Topic 5 (EAV):** Survey data stored in EAV is pivoted once during transformation, not by every tool call
- **Topic 6 (OLTP vs OLAP):** OLTP for survey submission, OLAP for the agent's analytical reads
- **Topic 7 (Suppression):** Suppression is applied in the serving layer and signaled in the tool's response
- **Topic 8 (Frontier Platforms):** Modern survey platforms expose these patterns through APIs

The agent doesn't need to know about any of this. It calls `get_team_overview()` and gets back a dataclass. Every layer of complexity — EAV pivots, suppression logic, columnar scans, materialized views — is hidden behind that one function call. That's the point.
