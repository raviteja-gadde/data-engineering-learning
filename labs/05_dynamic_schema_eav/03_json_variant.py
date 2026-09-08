"""Lab 03: JSON / Semi-Structured Column

Stores variable survey responses as a JSON object per respondent in DuckDB.
Demonstrates json_extract for direct access and UNNEST for analytical queries.
Self-describing data — question identifiers live in the data itself.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import random

from shared.display import print_panel, print_sql, print_table
from shared.duck import duckdb_conn

random.seed(42)

Q12 = [
    ("Q01", "I know what is expected of me at work", 4.1),
    ("Q02", "I have the materials and equipment I need", 3.9),
    ("Q03", "I have the opportunity to do what I do best every day", 3.7),
    ("Q04", "I have received recognition for doing good work", 3.4),
    ("Q05", "My supervisor seems to care about me as a person", 3.8),
    ("Q06", "Someone at work encourages my development", 3.5),
    ("Q07", "At work, my opinions seem to count", 3.6),
    ("Q08", "The mission makes me feel my job is important", 3.7),
    ("Q09", "My associates are committed to quality work", 3.8),
    ("Q10", "I have a best friend at work", 3.0),
    ("Q11", "Someone has talked to me about my progress", 3.3),
    ("Q12", "I have had opportunities to learn and grow", 3.5),
]

CUSTOM = {
    "Phoenix": [("CRW01", "Remote work tools are adequate", 3.6),
                ("CRW02", "I feel connected to my remote team", 3.2)],
    "Atlas":   [("CLD01", "Leaders communicate a clear vision", 3.4),
                ("CLD02", "I trust senior leadership", 3.1),
                ("CLD03", "Leadership programs are available", 3.3),
                ("CLD04", "My manager gives actionable feedback", 3.5),
                ("CLD05", "Leaders model the values they espouse", 3.2)],
    "Orbit":   [("CSF01", "I feel physically safe at work", 4.2),
                ("CSF02", "Safety concerns are addressed promptly", 3.9),
                ("CSF03", "Safety training is adequate", 3.7)],
}

TEAMS = {"Alpha": 0.2, "Beta": -0.1, "Gamma": 0.0}


def clamp(v, lo=1.0, hi=5.0):
    return max(lo, min(hi, round(v, 1)))


def build_and_load(conn):
    conn.execute("""
        CREATE TABLE json_responses (
            response_id INTEGER,
            project     VARCHAR,
            team        VARCHAR,
            responses   JSON
        )
    """)

    rid = 0
    for project, custom_qs in CUSTOM.items():
        questions = Q12 + custom_qs
        for team, adj in TEAMS.items():
            for _ in range(5):
                rid += 1
                answers = {}
                for qid, _, base in questions:
                    answers[qid] = clamp(base + adj + random.gauss(0, 0.3))
                conn.execute(
                    "INSERT INTO json_responses VALUES (?,?,?,?)",
                    [rid, project, team, json.dumps(answers)],
                )

    # Show self-describing nature
    print_panel("Self-Describing Data",
                "Each row carries its own question identifiers in the JSON keys")
    sql = "SELECT response_id, project, responses FROM json_responses LIMIT 2"
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    for row in rows:
        print_panel(f"Response {row[0]} ({row[1]})",
                    json.dumps(json.loads(row[2]), indent=2)[:400] + "\n...")


def direct_access(conn):
    """Extract specific question scores directly from JSON."""
    print_panel("Direct Access: json_extract",
                "Pull a specific question's score without any joins")

    sql = """
SELECT
    response_id, project, team,
    CAST(json_extract(responses, '$.Q01') AS DOUBLE) AS q01,
    CAST(json_extract(responses, '$.Q02') AS DOUBLE) AS q02,
    json_extract(responses, '$.CRW01') AS crw01_or_null
FROM json_responses
WHERE project = 'Phoenix'
LIMIT 5;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Direct JSON Access (Phoenix)",
                ["ID", "Project", "Team", "Q01", "Q02", "CRW01"], rows)

    print_panel("Notice",
                "CRW01 exists for Phoenix but would be NULL for Atlas/Orbit.\n"
                "No error, no schema issue — the key simply isn't present.")


def unnest_for_analytics(conn):
    """UNNEST JSON to get EAV-like rows for aggregation."""
    print_panel("UNNEST for Analytics",
                "Flatten JSON into rows for GROUP BY aggregation — like EAV on demand")

    sql = """
WITH keys_expanded AS (
    SELECT
        r.response_id,
        r.project,
        r.team,
        r.responses,
        UNNEST(json_keys(r.responses)) AS question_id
    FROM json_responses r
),
unpacked AS (
    SELECT
        response_id,
        project,
        team,
        question_id,
        CAST(json_extract(responses, '$.' || question_id) AS DOUBLE) AS score
    FROM keys_expanded
)
SELECT
    team,
    question_id,
    ROUND(AVG(score), 2) AS mean_score,
    COUNT(*) AS n
FROM unpacked
WHERE project = 'Phoenix'
  AND question_id IN ('Q01', 'Q05', 'Q10', 'CRW01')
GROUP BY team, question_id
ORDER BY team, question_id;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Mean by Team x Question (Phoenix, selected)",
                ["Team", "Question", "Mean", "N"], rows)


def show_flexibility(conn):
    """Add new question with zero schema change."""
    print_panel("Flexibility: Add New Question",
                "Update a JSON object — no ALTER TABLE, no new mapping rows")

    conn.execute("""
        INSERT INTO json_responses VALUES
            (9901, 'Phoenix', 'Alpha', '{"Q01": 4.0, "Q02": 3.8, "CNEW01": 4.5}')
    """)

    sql = """
SELECT response_id, project,
       json_extract(responses, '$.CNEW01') AS new_question
FROM json_responses
WHERE json_extract(responses, '$.CNEW01') IS NOT NULL;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Rows with CNEW01", ["ID", "Project", "CNEW01"], rows)


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 03: JSON / Semi-Structured Column",
                    "Each respondent's answers stored as a JSON object.\n"
                    "Keys ARE the question IDs — self-describing data.")
        build_and_load(conn)
        direct_access(conn)
        unnest_for_analytics(conn)
        show_flexibility(conn)

        print_panel("Key Takeaway",
                    "JSON is self-describing: question identifiers live in the data, not\n"
                    "in schema or mapping tables. Zero-schema-change flexibility like EAV,\n"
                    "but an AI agent can inspect a sample row to discover what questions\n"
                    "exist. Tradeoff: query syntax is more verbose (json_extract, UNNEST)\n"
                    "and the optimizer has no column-level statistics.")


if __name__ == "__main__":
    main()
