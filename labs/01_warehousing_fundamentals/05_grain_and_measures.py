"""Lab 05: Grain and Measures — Getting Aggregation Right

Part 1: Wrong grain produces wrong numbers. One row per team (pre-aggregated)
        vs one row per response (correct grain).
Part 2: Additive vs semi-additive measures. SUM of counts works. AVG of
        averages without weighting does not.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.display import print_panel, print_sql, print_table
from shared.duck import duckdb_conn


def part1_grain(conn):
    """Wrong grain vs correct grain."""
    print_panel("PART 1: GRAIN",
                "Grain = what does one row represent?\n"
                "Wrong grain silently produces wrong numbers.")

    # ── Wrong grain: one row per team (pre-aggregated) ──
    conn.execute("""
        CREATE TABLE wrong_grain (
            team_name VARCHAR, avg_score DECIMAL(3,2), response_count INT
        )
    """)
    conn.execute("""
        INSERT INTO wrong_grain VALUES
            ('Team Alpha',   4.20, 50),
            ('Team Beta',    3.50, 10),
            ('Team Gamma',   3.80, 30)
    """)
    print_table("wrong_grain table (one row per team)",
                ["Team", "Avg Score", "Response Count"],
                conn.execute("SELECT * FROM wrong_grain").fetchall())

    # Wrong: average of averages
    wrong_sql = """\
-- WRONG: average of averages (each team weighted equally)
SELECT ROUND(AVG(avg_score), 2) AS overall_avg
FROM wrong_grain;"""
    print_sql(wrong_sql)
    wrong_result = conn.execute(wrong_sql).fetchone()[0]

    # Correct: weighted average from wrong-grain table (rescue attempt)
    weighted_sql = """\
-- RESCUE: weighted average (manual correction)
SELECT ROUND(SUM(avg_score * response_count) / SUM(response_count), 2)
       AS weighted_avg
FROM wrong_grain;"""
    print_sql(weighted_sql)
    weighted_result = conn.execute(weighted_sql).fetchone()[0]

    print_table("Average of Averages vs Weighted Average",
                ["Method", "Result", "Correct?"],
                [("AVG(avg_score)", wrong_result, "NO — treats all teams equally"),
                 ("Weighted AVG", weighted_result, "YES — accounts for team size")])

    print_panel("The Problem",
                f"AVG of averages = {wrong_result}\n"
                f"Weighted average = {weighted_result}\n\n"
                "Team Alpha (score 4.2) has 50 responses.\n"
                "Team Beta (score 3.5) has only 10.\n"
                "Unweighted AVG treats them equally. Weighted AVG counts\n"
                "Team Alpha's 50 responses 5x more than Team Beta's 10.\n\n"
                "The root cause: storing data at team grain (one row per team)\n"
                "instead of response grain (one row per response).")

    # ── Correct grain: one row per response ──
    conn.execute("""
        CREATE TABLE correct_grain (
            response_id INT PRIMARY KEY, team_name VARCHAR, score DECIMAL(2,1)
        )
    """)
    import random
    random.seed(42)
    rid = 0
    for team, base, count in [("Team Alpha", 4.2, 50), ("Team Beta", 3.5, 10), ("Team Gamma", 3.8, 30)]:
        for _ in range(count):
            rid += 1
            s = max(1.0, min(5.0, round(base + random.gauss(0, 0.3), 1)))
            conn.execute("INSERT INTO correct_grain VALUES (?,?,?)", [rid, team, s])

    correct_sql = """\
-- CORRECT GRAIN: one row per response, simple AVG works
SELECT ROUND(AVG(score), 2) AS overall_avg
FROM correct_grain;"""
    print_sql(correct_sql)
    correct_result = conn.execute(correct_sql).fetchone()[0]

    print_table("Correct Grain Result",
                ["Method", "Result"],
                [("AVG on individual responses", correct_result)])

    print_panel("Lesson",
                "Store data at the finest grain. You can always aggregate UP\n"
                "(from responses to team averages). You cannot disaggregate DOWN\n"
                "(from team averages to individual responses).\n\n"
                "The correct grain for survey data:\n"
                "  one row = one person's answer to one question in one period.")


def part2_measures(conn):
    """Additive vs semi-additive vs non-additive measures."""
    print_panel("PART 2: MEASURE TYPES",
                "Not all numbers can be aggregated the same way.")

    conn.execute("""
        CREATE TABLE team_stats (
            team_name VARCHAR,
            response_count INT,
            mean_score DECIMAL(3,2),
            p90_score DECIMAL(3,2)
        )
    """)
    conn.execute("""
        INSERT INTO team_stats VALUES
            ('Team Alpha',   50, 4.20, 4.80),
            ('Team Beta',    10, 3.50, 4.20),
            ('Team Gamma',   30, 3.80, 4.50)
    """)
    print_table("Team Stats", ["Team", "Response Count", "Mean Score", "P90 Score"],
                conn.execute("SELECT * FROM team_stats").fetchall())

    # ── Additive: response_count ──
    add_sql = "SELECT SUM(response_count) AS total_responses FROM team_stats;"
    add_result = conn.execute(add_sql).fetchone()[0]

    # ── Semi-additive: mean_score ──
    wrong_mean = conn.execute("SELECT ROUND(AVG(mean_score), 2) FROM team_stats").fetchone()[0]
    correct_mean = conn.execute(
        "SELECT ROUND(SUM(mean_score * response_count) / SUM(response_count), 2) FROM team_stats"
    ).fetchone()[0]

    # ── Non-additive: p90_score ──
    wrong_p90 = conn.execute("SELECT ROUND(AVG(p90_score), 2) FROM team_stats").fetchone()[0]

    print_table("Aggregation Results",
                ["Measure", "Operation", "Result", "Correct?"],
                [
                    ("response_count (ADDITIVE)", "SUM", add_result, "YES — SUM works across all dimensions"),
                    ("mean_score (SEMI-ADDITIVE)", "AVG (unweighted)", wrong_mean, "NO — ignores team sizes"),
                    ("mean_score (SEMI-ADDITIVE)", "Weighted AVG", correct_mean, "YES — weights by response count"),
                    ("p90_score (NON-ADDITIVE)", "AVG of P90s", wrong_p90, "NO — must recompute from raw data"),
                ])

    print_panel("Measure Classification",
                "ADDITIVE — SUM works across any dimension.\n"
                "  Examples: response_count, completion_count\n\n"
                "SEMI-ADDITIVE — SUM works across some dimensions, not others.\n"
                "  Examples: mean score (need weighted avg across groups),\n"
                "            account balance (sum across accounts, not time)\n\n"
                "NON-ADDITIVE — Cannot be summed meaningfully. Must recompute.\n"
                "  Examples: percentiles, ratios, rates, medians\n\n"
                "This is why the grain rule matters: if you store individual\n"
                "responses, you can always compute any aggregate correctly.\n"
                "If you store pre-computed stats, you're limited to additive ops.")


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 05: Grain and Measures",
                    "Two common data warehouse traps:\n"
                    "1. Wrong grain produces wrong aggregations\n"
                    "2. Not all measures can be aggregated the same way")
        part1_grain(conn)
        part2_measures(conn)


if __name__ == "__main__":
    main()
