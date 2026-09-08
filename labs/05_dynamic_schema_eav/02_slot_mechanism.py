"""Lab 02: Slot Mechanism

Pre-allocates fixed generic columns (slot_1..slot_20). A mapping table
decodes what each slot means per project. Shows deterministic width but
opaque column names that defeat AI/text-to-SQL.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import random

from shared.display import print_panel, print_sql, print_table
from shared.duck import duckdb_conn

random.seed(42)

# ── Question sets (same as lab 01) ───────────────────────────────────────────
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
NUM_SLOTS = 20


def clamp(v, lo=1.0, hi=5.0):
    return max(lo, min(hi, round(v, 1)))


def build_and_load(conn):
    # Build slot response table with 20 generic columns
    slot_cols = ", ".join(f"slot_{i} DOUBLE" for i in range(1, NUM_SLOTS + 1))
    conn.execute(f"""
        CREATE TABLE slot_responses (
            response_id INTEGER,
            project     VARCHAR,
            team        VARCHAR,
            {slot_cols}
        )
    """)

    # Mapping table: what each slot means per project
    conn.execute("""
        CREATE TABLE slot_mapping (
            project       VARCHAR,
            slot_number   INTEGER,
            question_id   VARCHAR,
            question_text VARCHAR
        )
    """)

    # Load mappings: Q12 goes to slots 1-12, custom starts at 13
    for project, custom_qs in CUSTOM.items():
        for i, (qid, text, _) in enumerate(Q12, 1):
            conn.execute("INSERT INTO slot_mapping VALUES (?,?,?,?)",
                         [project, i, qid, text])
        for j, (qid, text, _) in enumerate(custom_qs, 13):
            conn.execute("INSERT INTO slot_mapping VALUES (?,?,?,?)",
                         [project, j, qid, text])

    # Load response data
    rid = 0
    for project, custom_qs in CUSTOM.items():
        questions = Q12 + custom_qs
        for team, adj in TEAMS.items():
            for _ in range(5):
                rid += 1
                scores = [clamp(base + adj + random.gauss(0, 0.3))
                          for _, _, base in questions]
                # Pad to 20 slots with NULL
                scores += [None] * (NUM_SLOTS - len(scores))
                placeholders = ", ".join("?" for _ in range(NUM_SLOTS))
                conn.execute(
                    f"INSERT INTO slot_responses VALUES (?, ?, ?, {placeholders})",
                    [rid, project, team] + scores,
                )

    # Show what the raw data looks like — opaque
    print_panel("Raw Slot Data (first 3 rows)",
                "Column names carry no meaning — what is slot_7?")
    sql = "SELECT response_id, project, team, slot_1, slot_7, slot_13, slot_14 FROM slot_responses LIMIT 3"
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Raw Slots", ["ID", "Project", "Team", "slot_1", "slot_7", "slot_13", "slot_14"], rows)


def show_mapping(conn):
    """Show the mapping table that gives slots meaning."""
    print_panel("Slot Mapping Table", "The Rosetta Stone — without this, slot data is meaningless")

    sql = """
SELECT project, slot_number, question_id, question_text
FROM slot_mapping
WHERE slot_number IN (1, 7, 13, 14)
ORDER BY project, slot_number;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Mapping (selected slots)", ["Project", "Slot#", "Question ID", "Question Text"], rows)

    print_panel("Notice",
                "slot_13 means different things per project:\n"
                "  Phoenix: CRW01 (remote work tools)\n"
                "  Atlas:   CLD01 (leadership vision)\n"
                "  Orbit:   CSF01 (physical safety)\n"
                "The same column holds completely different questions.")


def query_with_mapping(conn):
    """Show a query that translates slots back to question names."""
    print_panel("Query: Mean Score by Question (Phoenix)",
                "Must join mapping table to know what each slot measures")

    sql = """
SELECT
    m.question_id,
    m.question_text,
    ROUND(AVG(CASE m.slot_number
        WHEN 1  THEN r.slot_1  WHEN 2  THEN r.slot_2
        WHEN 3  THEN r.slot_3  WHEN 4  THEN r.slot_4
        WHEN 5  THEN r.slot_5  WHEN 6  THEN r.slot_6
        WHEN 7  THEN r.slot_7  WHEN 8  THEN r.slot_8
        WHEN 9  THEN r.slot_9  WHEN 10 THEN r.slot_10
        WHEN 11 THEN r.slot_11 WHEN 12 THEN r.slot_12
        WHEN 13 THEN r.slot_13 WHEN 14 THEN r.slot_14
    END), 2) AS mean_score
FROM slot_responses r
JOIN slot_mapping m ON r.project = m.project
WHERE r.project = 'Phoenix'
GROUP BY m.question_id, m.question_text, m.slot_number
ORDER BY m.slot_number;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Mean Score by Question (Phoenix)",
                ["Question ID", "Question Text", "Mean Score"], rows)

    print_panel("AI/Text-to-SQL Problem",
                "An LLM asked 'what is the average score for expectations?'\n"
                "must: 1) find 'expectations' in slot_mapping\n"
                "      2) determine it maps to slot_1\n"
                "      3) query slot_1 from slot_responses\n"
                "      4) know to filter by project (slot_13 differs per project)\n"
                "This multi-hop reasoning is where text-to-SQL breaks down.")


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 02: Slot Mechanism",
                    "20 generic columns (slot_1..slot_20) + mapping table.\n"
                    "Phoenix uses slots 1-14, Atlas 1-17, Orbit 1-15.")
        build_and_load(conn)
        show_mapping(conn)
        query_with_mapping(conn)

        print_panel("Key Takeaway",
                    "Slots give deterministic table width and relational query patterns,\n"
                    "but column names are meaningless without the mapping table. AI agents\n"
                    "cannot reason about 'slot_7' — they need semantic column names.\n"
                    "Slot exhaustion (exceeding 20) requires a schema change anyway.")


if __name__ == "__main__":
    main()
