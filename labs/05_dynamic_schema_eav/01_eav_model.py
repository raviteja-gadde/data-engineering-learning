"""Lab 01: Entity-Attribute-Value (EAV) Model

Builds an EAV table for survey responses across 3 projects with different
question sets. Shows infinite flexibility (add questions without schema change)
and the pain (analytical queries require PIVOT/conditional aggregation).
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

import random
random.seed(42)

# ── Standard Q12 + custom questions per project ─────────────────────────────
Q12 = [
    ("Q01", "I know what is expected of me at work", "Basic Needs", 4.1),
    ("Q02", "I have the materials and equipment I need", "Basic Needs", 3.9),
    ("Q03", "I have the opportunity to do what I do best every day", "Individual", 3.7),
    ("Q04", "I have received recognition for doing good work", "Individual", 3.4),
    ("Q05", "My supervisor seems to care about me as a person", "Individual", 3.8),
    ("Q06", "Someone at work encourages my development", "Individual", 3.5),
    ("Q07", "At work, my opinions seem to count", "Teamwork", 3.6),
    ("Q08", "The mission makes me feel my job is important", "Teamwork", 3.7),
    ("Q09", "My associates are committed to quality work", "Teamwork", 3.8),
    ("Q10", "I have a best friend at work", "Teamwork", 3.0),
    ("Q11", "Someone has talked to me about my progress", "Growth", 3.3),
    ("Q12", "I have had opportunities to learn and grow", "Growth", 3.5),
]

CUSTOM_QUESTIONS = {
    "Phoenix": [
        ("CRW01", "Remote work tools are adequate", "Custom", 3.6),
        ("CRW02", "I feel connected to my remote team", "Custom", 3.2),
    ],
    "Atlas": [
        ("CLD01", "Leaders communicate a clear vision", "Custom", 3.4),
        ("CLD02", "I trust senior leadership", "Custom", 3.1),
        ("CLD03", "Leadership development programs are available", "Custom", 3.3),
        ("CLD04", "My manager gives actionable feedback", "Custom", 3.5),
        ("CLD05", "Leaders model the values they espouse", "Custom", 3.2),
    ],
    "Orbit": [
        ("CSF01", "I feel physically safe at work", "Custom", 4.2),
        ("CSF02", "Safety concerns are addressed promptly", "Custom", 3.9),
        ("CSF03", "Safety training is adequate", "Custom", 3.7),
    ],
}

TEAMS = {"Alpha": 0.2, "Beta": -0.1, "Gamma": 0.0}


def clamp(v, lo=1.0, hi=5.0):
    return max(lo, min(hi, round(v, 1)))


def build_and_load(conn):
    conn.execute("""
        CREATE TABLE eav_responses (
            response_id INTEGER,
            project     VARCHAR,
            team        VARCHAR,
            attribute   VARCHAR,
            value       DOUBLE
        )
    """)

    rid = 0
    for project, custom_qs in CUSTOM_QUESTIONS.items():
        questions = Q12 + custom_qs
        for team, adj in TEAMS.items():
            for _ in range(5):  # 5 respondents per team per project
                rid += 1
                for qid, _, _, base in questions:
                    score = clamp(base + adj + random.gauss(0, 0.3))
                    conn.execute(
                        "INSERT INTO eav_responses VALUES (?,?,?,?,?)",
                        [rid, project, team, qid, score],
                    )

    count = conn.execute("SELECT COUNT(*) FROM eav_responses").fetchone()[0]
    projects = conn.execute("SELECT project, COUNT(DISTINCT attribute) FROM eav_responses GROUP BY project").fetchall()
    print_table("Data Loaded", ["Project", "Distinct Questions"], projects)
    print_panel("Total EAV Rows", f"{count} rows — each question-answer pair is its own row")


def show_flexibility(conn):
    """Add a brand-new question type with zero schema change."""
    print_panel("Flexibility Demo", "Adding a new question to Phoenix — no ALTER TABLE needed")

    conn.execute("""
        INSERT INTO eav_responses VALUES
            (9901, 'Phoenix', 'Alpha', 'CNEW01', 4.0),
            (9902, 'Phoenix', 'Beta',  'CNEW01', 3.5)
    """)

    sql = """
SELECT project, COUNT(DISTINCT attribute) AS num_questions
FROM eav_responses GROUP BY project ORDER BY project;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Questions Per Project (after adding CNEW01)", ["Project", "Num Questions"], rows)


def show_pain(conn):
    """Analytical query: mean score per question per team — requires PIVOT."""
    print_panel("The Pain: Analytical Queries",
                "Mean score per Q12 question per team requires conditional aggregation.\n"
                "One CASE expression per question — imagine doing this for 50 questions.")

    # Show the verbose query needed
    sql = """
SELECT
    team,
    ROUND(AVG(CASE WHEN attribute = 'Q01' THEN value END), 2) AS Q01,
    ROUND(AVG(CASE WHEN attribute = 'Q02' THEN value END), 2) AS Q02,
    ROUND(AVG(CASE WHEN attribute = 'Q03' THEN value END), 2) AS Q03,
    ROUND(AVG(CASE WHEN attribute = 'Q04' THEN value END), 2) AS Q04,
    ROUND(AVG(CASE WHEN attribute = 'Q05' THEN value END), 2) AS Q05,
    ROUND(AVG(CASE WHEN attribute = 'Q06' THEN value END), 2) AS Q06
FROM eav_responses
WHERE project = 'Phoenix'
GROUP BY team
ORDER BY team;
"""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    print_table("Pivoted Q01-Q06 (Phoenix)", ["Team", "Q01", "Q02", "Q03", "Q04", "Q05", "Q06"], rows)

    # Simpler EAV-native query: mean by category
    sql2 = """
SELECT
    e.team,
    q.category,
    ROUND(AVG(e.value), 2) AS mean_score,
    COUNT(*) AS n
FROM eav_responses e
JOIN (VALUES
    ('Q01','Basic Needs'),('Q02','Basic Needs'),
    ('Q03','Individual'),('Q04','Individual'),('Q05','Individual'),('Q06','Individual'),
    ('Q07','Teamwork'),('Q08','Teamwork'),('Q09','Teamwork'),('Q10','Teamwork'),
    ('Q11','Growth'),('Q12','Growth')
) AS q(qid, category) ON e.attribute = q.qid
WHERE e.project = 'Phoenix'
GROUP BY e.team, q.category
ORDER BY e.team, q.category;
"""
    print_panel("Category Aggregation", "Grouping by category is easier — no per-question CASE needed")
    print_sql(sql2)
    rows2 = conn.execute(sql2).fetchall()
    print_table("Mean by Category (Phoenix)", ["Team", "Category", "Mean", "N"], rows2)


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 01: EAV Model",
                    "Entity-Attribute-Value for variable survey question sets.\n"
                    "Phoenix: 12 Q12 + 2 custom (14 total)\n"
                    "Atlas:   12 Q12 + 5 custom (17 total)\n"
                    "Orbit:   12 Q12 + 3 custom (15 total)")
        build_and_load(conn)
        show_flexibility(conn)
        show_pain(conn)

        print_panel("Key Takeaway",
                    "EAV gives infinite flexibility — add any question with zero schema change.\n"
                    "The cost: analytical queries require PIVOT/conditional aggregation, one CASE\n"
                    "per question. For an AI agent generating SQL, constructing correct PIVOTs\n"
                    "from data (not schema) is fragile and error-prone.")


if __name__ == "__main__":
    main()
