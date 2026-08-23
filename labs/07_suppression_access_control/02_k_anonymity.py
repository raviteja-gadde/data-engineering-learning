"""Lab 02: k-Anonymity — When Quasi-Identifiers Expose Individuals

Demonstrates how combinations of seemingly innocent attributes can
identify individuals, and how k-anonymity prevents this by ensuring
every attribute combination has at least k records.

Concrete attacker scenario: "I know my colleague is on Team C, in Austin,
and in Product. What did they score?"
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

K_THRESHOLD = 4


def create_data(conn):
    """Employees with quasi-identifiers and a sensitive attribute (score)."""
    conn.execute("""
        CREATE TABLE employees (
            emp_id      INTEGER,
            team        VARCHAR,
            location    VARCHAR,
            tenure_band VARCHAR,
            q01_score   INTEGER   -- sensitive: this is what we protect
        )
    """)
    # Designed so some quasi-identifier combos have very few people
    data = [
        # Team A, New York, various tenure bands — large enough
        (1,  "Team A", "New York", "0-2 years", 4),
        (2,  "Team A", "New York", "0-2 years", 5),
        (3,  "Team A", "New York", "3-5 years", 3),
        (4,  "Team A", "New York", "3-5 years", 4),
        (5,  "Team A", "New York", "3-5 years", 5),
        (6,  "Team A", "New York", "5+ years",  4),
        (7,  "Team A", "New York", "5+ years",  3),
        (8,  "Team A", "Austin",   "0-2 years", 4),
        (9,  "Team A", "Austin",   "0-2 years", 5),
        (10, "Team A", "Austin",   "3-5 years", 3),
        # Team B, Chicago — small team
        (11, "Team B", "Chicago",  "0-2 years", 2),
        (12, "Team B", "Chicago",  "3-5 years", 3),
        (13, "Team B", "Chicago",  "5+ years",  1),
        # Team C — 1 person in a unique combo
        (14, "Team C", "Austin",   "5+ years",  2),
        (15, "Team C", "New York", "0-2 years", 4),
        (16, "Team C", "New York", "0-2 years", 3),
        (17, "Team C", "New York", "3-5 years", 5),
        (18, "Team C", "New York", "3-5 years", 4),
    ]
    conn.executemany("INSERT INTO employees VALUES (?, ?, ?, ?, ?)", data)
    print_panel("Data", f"{len(data)} employees with quasi-identifiers: team, location, tenure_band")


def main():
    with duckdb_conn() as conn:
        create_data(conn)

        # ── Step 1: Show all quasi-identifier groups and their sizes ─
        print_panel(
            "Step 1: Quasi-Identifier Groups",
            "Each unique (team, location, tenure_band) combination\n"
            "is a group. Groups with fewer than k records are vulnerable."
        )
        sql_groups = """
            SELECT team, location, tenure_band,
                   COUNT(*)       AS group_size,
                   CASE WHEN COUNT(*) < 4 THEN 'VULNERABLE'
                        ELSE 'OK' END AS status
            FROM employees
            GROUP BY team, location, tenure_band
            ORDER BY group_size ASC
        """
        print_sql(sql_groups)
        rows = conn.execute(sql_groups).fetchall()
        print_table("Quasi-Identifier Groups", ["team", "location", "tenure", "size", "status"], rows)

        # ── Step 2: The attacker scenario ────────────────────────────
        print_panel(
            "Step 2: Attacker Scenario",
            "An attacker knows: 'My colleague is on Team C, in Austin,\n"
            "with 5+ years tenure.' They search the dataset..."
        )
        sql_attack = """
            SELECT team, location, tenure_band, q01_score
            FROM employees
            WHERE team = 'Team C' AND location = 'Austin' AND tenure_band = '5+ years'
        """
        print_sql(sql_attack)
        rows = conn.execute(sql_attack).fetchall()
        print_table(
            "Attack Result: Exact Match (1 person)",
            ["team", "location", "tenure", "score"], rows
        )
        print_panel(
            "Identification!",
            "Only 1 record matches. The attacker now knows this person\n"
            "scored 2 on Q01. k-anonymity with k>=2 would have prevented\n"
            "this by requiring at least 2 people in every group."
        )

        # ── Step 3: Apply k-anonymity suppression ────────────────────
        print_panel(
            "Step 3: Suppressed Results (k=4)",
            "Replace scores with NULL for any group smaller than k.\n"
            "The attacker's query now returns no useful information."
        )
        sql_suppressed = f"""
            SELECT e.team, e.location, e.tenure_band,
                   CASE WHEN g.group_size >= {K_THRESHOLD}
                        THEN e.q01_score END           AS q01_score_safe,
                   g.group_size
            FROM employees e
            JOIN (
                SELECT team, location, tenure_band, COUNT(*) AS group_size
                FROM employees
                GROUP BY team, location, tenure_band
            ) g USING (team, location, tenure_band)
            ORDER BY g.group_size ASC, e.emp_id
        """
        print_sql(sql_suppressed)
        rows = conn.execute(sql_suppressed).fetchall()
        print_table(
            "All Records with k-Anonymity Suppression",
            ["team", "location", "tenure", "score (safe)", "group_size"],
            rows
        )

        # ── Step 4: Retry the attack ─────────────────────────────────
        sql_retry = f"""
            SELECT e.team, e.location, e.tenure_band,
                   CASE WHEN g.group_size >= {K_THRESHOLD}
                        THEN e.q01_score END AS q01_score_safe
            FROM employees e
            JOIN (
                SELECT team, location, tenure_band, COUNT(*) AS group_size
                FROM employees
                GROUP BY team, location, tenure_band
            ) g USING (team, location, tenure_band)
            WHERE e.team = 'Team C' AND e.location = 'Austin'
                  AND e.tenure_band = '5+ years'
        """
        print_sql(sql_retry)
        rows = conn.execute(sql_retry).fetchall()
        print_table("Attack Retry After Suppression", ["team", "location", "tenure", "score"], rows)

        print_panel(
            "Key Takeaway",
            "k-anonymity ensures every quasi-identifier combination has\n"
            "at least k records. When k=4, even small-group members\n"
            "cannot be singled out. Survey 'minimum group size' rules\n"
            "are k-anonymity applied to report dimensions."
        )


if __name__ == "__main__":
    main()
