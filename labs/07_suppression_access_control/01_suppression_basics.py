"""Lab 01: Suppression Basics — Why the Same Data Gets Different Treatment

Demonstrates the core suppression concept: NULL out aggregates where the
group size is below a threshold (here, 4). The same underlying responses
produce different suppression results depending on the grouping dimensions.

Key insight: suppression is a property of the QUERY, not the DATA.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

SUPPRESSION_THRESHOLD = 4


def create_data(conn):
    """Build survey responses with teams sized to trigger suppression."""
    conn.execute("""
        CREATE TABLE responses (
            employee_id  INTEGER,
            team         VARCHAR,
            location     VARCHAR,
            department   VARCHAR,
            q01          INTEGER  -- single question score for clarity
        )
    """)
    # Team A: 10 people, split across 2 locations and 2 departments
    # Team B: 3 people — always suppressed at team level
    # Team C: 6 people, but only 2 in Austin — suppressed at team+location
    data = []
    eid = 1

    # Team A: 10 people — 7 in New York/Engineering, 3 in Austin/Engineering
    for score in [4, 5, 3, 4, 5, 4, 3]:
        data.append((eid, "Team A", "New York", "Engineering", score))
        eid += 1
    for score in [4, 3, 5]:
        data.append((eid, "Team A", "Austin", "Engineering", score))
        eid += 1

    # Team B: 3 people — all in Chicago/Sales (too small at team level)
    for score in [2, 3, 1]:
        data.append((eid, "Team B", "Chicago", "Sales", score))
        eid += 1

    # Team C: 6 people — 4 in New York/Product, 2 in Austin/Product
    for score in [3, 4, 4, 5]:
        data.append((eid, "Team C", "New York", "Product", score))
        eid += 1
    for score in [2, 3]:
        data.append((eid, "Team C", "Austin", "Product", score))
        eid += 1

    conn.executemany(
        "INSERT INTO responses VALUES (?, ?, ?, ?, ?)", data
    )
    print_panel("Data Created", f"{len(data)} survey responses across 3 teams")


def show_cut(conn, title, sql):
    """Run a suppression query and display results."""
    print_sql(sql)
    rows = conn.execute(sql).fetchall()
    cols = [d[0] for d in conn.description]
    print_table(title, cols, rows)


def main():
    with duckdb_conn() as conn:
        create_data(conn)

        # ── Cut 1: By team only ──────────────────────────────────────
        print_panel(
            "Cut 1: Group by Team",
            "Team B (3 people) is suppressed. Teams A and C pass."
        )
        show_cut(conn, "Team-Level Scores (threshold=4)", f"""
            SELECT team,
                   COUNT(*)                                         AS n,
                   CASE WHEN COUNT(*) >= {SUPPRESSION_THRESHOLD}
                        THEN ROUND(AVG(q01), 2) END                 AS avg_q01
            FROM responses
            GROUP BY team
            ORDER BY team
        """)

        # ── Cut 2: By team + location ────────────────────────────────
        print_panel(
            "Cut 2: Group by Team + Location",
            "Same data, finer cut. Team A Austin (3 people) and\n"
            "Team C Austin (2 people) are NOW suppressed — they passed\n"
            "at team level but fail at team+location."
        )
        show_cut(conn, "Team+Location Scores (threshold=4)", f"""
            SELECT team, location,
                   COUNT(*)                                         AS n,
                   CASE WHEN COUNT(*) >= {SUPPRESSION_THRESHOLD}
                        THEN ROUND(AVG(q01), 2) END                 AS avg_q01
            FROM responses
            GROUP BY team, location
            ORDER BY team, location
        """)

        # ── Cut 3: By team + location + department ───────────────────
        print_panel(
            "Cut 3: Group by Team + Location + Department",
            "Adding department doesn't change counts here (each team\n"
            "is in one department), but in real data more dimensions\n"
            "= smaller cells = more suppression."
        )
        show_cut(conn, "Team+Location+Dept Scores (threshold=4)", f"""
            SELECT team, location, department,
                   COUNT(*)                                         AS n,
                   CASE WHEN COUNT(*) >= {SUPPRESSION_THRESHOLD}
                        THEN ROUND(AVG(q01), 2) END                 AS avg_q01
            FROM responses
            GROUP BY team, location, department
            ORDER BY team, location
        """)

        # ── Why this matters ─────────────────────────────────────────
        print_panel(
            "Key Takeaway",
            "Suppression depends on the GROUP BY, not the raw data.\n"
            "Team A passes at team level (n=10) but fails at\n"
            "team+location for Austin (n=3).\n\n"
            "This is why suppression must be computed PER QUERY —\n"
            "you cannot pre-flag rows as 'suppressed' because the\n"
            "same row passes or fails depending on the report dimensions."
        )


if __name__ == "__main__":
    main()
