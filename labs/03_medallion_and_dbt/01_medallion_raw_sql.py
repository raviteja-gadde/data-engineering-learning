"""Lab 01 — Medallion Architecture in Raw SQL

Build Bronze -> Silver -> Gold layers in DuckDB using plain SQL.
No dbt, no frameworks -- just SQL to understand the concept before the tool.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_sql, print_table
from shared.duck import duckdb_conn


def main():
    with duckdb_conn() as con:

        # ── BRONZE: Raw ingestion, nothing lost ──────────────────────
        print_panel(
            "BRONZE LAYER",
            "Raw data exactly as it arrived.\n"
            "Duplicates? Keep them. Wrong types? Keep them.\n"
            "Bronze is your insurance policy -- you can always rebuild from here."
        )

        con.execute("""
            CREATE TABLE bronze_survey_responses (
                response_id   VARCHAR,
                respondent_id VARCHAR,
                team_id       VARCHAR,
                question_id   VARCHAR,
                score         VARCHAR,   -- string on purpose: raw data
                submitted_at  VARCHAR,
                _loaded_at    TIMESTAMP DEFAULT current_timestamp
            )
        """)

        # Insert raw data with intentional problems:
        # - Duplicate R001 (ingestion retry)
        # - Score as string (raw CSV)
        # - One invalid score ('X')
        con.execute("""
            INSERT INTO bronze_survey_responses
                (response_id, respondent_id, team_id, question_id, score, submitted_at)
            VALUES
                ('R001', 'EMP01', 'T001', 'Q01', '4', '2025-03-15 09:12:00'),
                ('R002', 'EMP01', 'T001', 'Q02', '5', '2025-03-15 09:12:00'),
                ('R003', 'EMP01', 'T001', 'Q06', '2', '2025-03-15 09:14:00'),
                ('R001', 'EMP01', 'T001', 'Q01', '4', '2025-03-15 09:12:00'),
                ('R004', 'EMP02', 'T001', 'Q01', '5', '2025-03-15 10:00:00'),
                ('R005', 'EMP02', 'T001', 'Q06', '3', '2025-03-15 10:02:00'),
                ('R006', 'EMP03', 'T002', 'Q01', '3', '2025-03-16 14:20:00'),
                ('R007', 'EMP03', 'T002', 'Q06', '4', '2025-03-16 14:22:00'),
                ('R008', 'EMP04', 'T002', 'Q01', '4', '2025-03-16 15:00:00'),
                ('R009', 'EMP04', 'T002', 'Q06', '3', '2025-03-16 15:02:00'),
                ('R010', 'EMP05', 'T003', 'Q01', 'X', '2025-03-17 11:00:00')
        """)

        bronze_sql = "SELECT response_id, respondent_id, team_id, question_id, score, submitted_at FROM bronze_survey_responses"
        print_sql(bronze_sql)
        rows = con.execute(bronze_sql).fetchall()
        print_table(
            "Bronze: Raw Data (11 rows, including 1 duplicate and 1 invalid)",
            ["response_id", "respondent_id", "team_id", "question_id", "score", "submitted_at"],
            rows,
        )

        # Question config (reference data -- also bronze)
        con.execute("""
            CREATE TABLE bronze_question_config (
                question_id       VARCHAR PRIMARY KEY,
                question_text     VARCHAR,
                category          VARCHAR,
                is_reverse_scored BOOLEAN
            )
        """)
        con.execute("""
            INSERT INTO bronze_question_config VALUES
                ('Q01', 'I know what is expected of me at work', 'Basic Needs', false),
                ('Q02', 'I have the materials and equipment I need to do my work right', 'Basic Needs', false),
                ('Q06', 'There is someone at work who encourages my development', 'Individual', true)
        """)

        # ── SILVER: Cleaned, deduped, business rules applied ─────────
        print_panel(
            "SILVER LAYER",
            "Clean, deduplicated, typed, business-rule-applied data.\n\n"
            "Four things happen:\n"
            "1. Dedup: ROW_NUMBER picks first occurrence per response_id\n"
            "2. Type enforcement: score cast to INTEGER, invalid values rejected\n"
            "3. Join with question config to get category and reverse-score flag\n"
            "4. Reverse-scale: Q06 treated as reverse-scored for demo (raw 2 -> corrected 4)"
        )

        silver_sql = """
            CREATE TABLE silver_survey_responses AS
            WITH deduped AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY response_id
                        ORDER BY submitted_at DESC
                    ) AS row_num
                FROM bronze_survey_responses
                WHERE TRY_CAST(score AS INTEGER) IS NOT NULL  -- reject non-numeric
            ),
            unique_responses AS (
                SELECT
                    response_id,
                    respondent_id,
                    team_id,
                    question_id,
                    CAST(score AS INTEGER) AS raw_score,
                    CAST(submitted_at AS TIMESTAMP) AS submitted_at
                FROM deduped
                WHERE row_num = 1
            )
            SELECT
                r.response_id,
                r.respondent_id,
                r.team_id,
                r.question_id,
                q.category,
                r.raw_score,
                CASE
                    WHEN q.is_reverse_scored THEN 6 - r.raw_score
                    ELSE r.raw_score
                END AS score,
                q.is_reverse_scored,
                r.submitted_at
            FROM unique_responses r
            JOIN bronze_question_config q USING (question_id)
            WHERE r.raw_score BETWEEN 1 AND 5
        """
        print_sql(silver_sql)
        con.execute(silver_sql)

        rows = con.execute("""
            SELECT response_id, respondent_id, team_id, question_id,
                   raw_score, score, is_reverse_scored
            FROM silver_survey_responses
            ORDER BY response_id
        """).fetchall()
        print_table(
            "Silver: Cleaned Data (9 unique valid rows, reverse-scale applied)",
            ["response_id", "respondent_id", "team_id", "question_id",
             "raw_score", "score", "is_reverse_scored"],
            rows,
        )

        # Show the reverse-scale effect
        print_panel(
            "Reverse-Scale Demo",
            "Our project treats Q06 as reverse-scored for demonstration.\n"
            "Formula: score = 6 - raw_score. This flips the scale so all\n"
            "questions point the same direction for aggregation.\n\n"
            "R003: raw_score=2, is_reverse_scored=true -> score = 6-2 = 4\n"
            "R005: raw_score=3, is_reverse_scored=true -> score = 6-3 = 3\n\n"
            "Without reverse-scaling, averages would be misleading."
        )

        # Suppression check
        con.execute("""
            CREATE TABLE silver_suppression AS
            SELECT
                team_id,
                COUNT(DISTINCT respondent_id) AS respondent_count,
                COUNT(DISTINCT respondent_id) < 4 AS is_suppressed
            FROM silver_survey_responses
            GROUP BY team_id
        """)
        rows = con.execute("SELECT * FROM silver_suppression ORDER BY team_id").fetchall()
        print_table(
            "Silver: Suppression Flags (< 4 respondents = suppressed)",
            ["team_id", "respondent_count", "is_suppressed"],
            rows,
        )

        # ── GOLD: Pre-aggregated, dashboard-ready ────────────────────
        print_panel(
            "GOLD LAYER",
            "Pre-computed aggregates for dashboards.\n"
            "One row per team per category with the mean score.\n"
            "Consumers read this directly -- no joins or aggregations needed."
        )

        gold_sql = """
            CREATE TABLE gold_team_metrics AS
            SELECT
                r.team_id,
                r.category,
                ROUND(AVG(r.score), 2) AS mean_score,
                COUNT(*) AS response_count,
                s.is_suppressed
            FROM silver_survey_responses r
            JOIN silver_suppression s USING (team_id)
            GROUP BY r.team_id, r.category, s.is_suppressed
            ORDER BY r.team_id, r.category
        """
        print_sql(gold_sql)
        con.execute(gold_sql)

        rows = con.execute("SELECT * FROM gold_team_metrics").fetchall()
        print_table(
            "Gold: Team Metrics (pre-aggregated, suppression-aware)",
            ["team_id", "category", "mean_score", "response_count", "is_suppressed"],
            rows,
        )

        # ── Summary ──────────────────────────────────────────────────
        print_panel(
            "What Just Happened",
            "Bronze (11 rows) -> Silver (9 rows) -> Gold (4 rows)\n\n"
            "Bronze: Raw dump. Duplicates, bad types, invalid values.\n"
            "Silver: Deduped (R001 duplicate removed), invalid rejected (R010 score='X'),\n"
            "        types enforced, reverse-scale applied (Q06 corrected).\n"
            "Gold:   Mean score per team per category. Dashboard reads this directly.\n\n"
            "Each layer has clear guarantees. Business rules (reverse-scaling,\n"
            "suppression) live in Silver -- version-controlled, testable, auditable.\n\n"
            "Next lab: do this same thing with dbt instead of raw SQL."
        )

if __name__ == "__main__":
    main()
