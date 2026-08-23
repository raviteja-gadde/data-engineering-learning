"""Lab 05: Approach Comparison

Same dataset loaded into all 4 representations. Same analytical query
run against each. Side-by-side comparison of query complexity, execution
time, storage size, and flexibility.
"""

import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

import random
random.seed(42)

# ── Shared question data ────────────────────────────────────────────────────
Q12 = [
    ("Q01", "q01_expectations", 4.1), ("Q02", "q02_materials", 3.9),
    ("Q03", "q03_do_best", 3.7),      ("Q04", "q04_recognition", 3.4),
    ("Q05", "q05_cares", 3.8),        ("Q06", "q06_development", 3.5),
    ("Q07", "q07_opinions", 3.6),     ("Q08", "q08_mission", 3.7),
    ("Q09", "q09_quality", 3.8),      ("Q10", "q10_best_friend", 3.0),
    ("Q11", "q11_progress", 3.3),     ("Q12", "q12_learn_grow", 3.5),
]

CUSTOM = {
    "Phoenix": [("CRW01", "crw01_remote_tools", 3.6),
                ("CRW02", "crw02_remote_connection", 3.2)],
    "Atlas":   [("CLD01", "cld01_leadership_vision", 3.4),
                ("CLD02", "cld02_trust_leadership", 3.1),
                ("CLD03", "cld03_leadership_programs", 3.3),
                ("CLD04", "cld04_actionable_feedback", 3.5),
                ("CLD05", "cld05_values_modeled", 3.2)],
    "Orbit":   [("CSF01", "csf01_physical_safety", 4.2),
                ("CSF02", "csf02_safety_response", 3.9),
                ("CSF03", "csf03_safety_training", 3.7)],
}

TEAMS = {"Alpha": 0.2, "Beta": -0.1, "Gamma": 0.0}
NUM_SLOTS = 20


def clamp(v, lo=1.0, hi=5.0):
    return max(lo, min(hi, round(v, 1)))


def generate_data():
    """Generate raw data once, load into all 4 representations."""
    rows = []  # (rid, project, team, [(qid, col_name, score), ...])
    rid = 0
    for project, custom_qs in CUSTOM.items():
        questions = Q12 + custom_qs
        for team, adj in TEAMS.items():
            for _ in range(5):
                rid += 1
                answers = []
                for qid, col, base in questions:
                    score = clamp(base + adj + random.gauss(0, 0.3))
                    answers.append((qid, col, score))
                rows.append((rid, project, team, answers))
    return rows


def load_eav(conn, data):
    conn.execute("""CREATE TABLE eav (
        response_id INTEGER, project VARCHAR, team VARCHAR,
        attribute VARCHAR, value DOUBLE)""")
    for rid, proj, team, answers in data:
        for qid, _, score in answers:
            conn.execute("INSERT INTO eav VALUES (?,?,?,?,?)",
                         [rid, proj, team, qid, score])


def load_slots(conn, data):
    slot_cols = ", ".join(f"slot_{i} DOUBLE" for i in range(1, NUM_SLOTS + 1))
    conn.execute(f"""CREATE TABLE slots (
        response_id INTEGER, project VARCHAR, team VARCHAR, {slot_cols})""")
    conn.execute("""CREATE TABLE slot_map (
        project VARCHAR, slot_number INTEGER, question_id VARCHAR)""")
    # Build mapping
    for proj, custom_qs in CUSTOM.items():
        for i, (qid, _, _) in enumerate(Q12, 1):
            conn.execute("INSERT INTO slot_map VALUES (?,?,?)", [proj, i, qid])
        for j, (qid, _, _) in enumerate(custom_qs, 13):
            conn.execute("INSERT INTO slot_map VALUES (?,?,?)", [proj, j, qid])
    # Load data
    for rid, proj, team, answers in data:
        scores = [s for _, _, s in answers] + [None] * (NUM_SLOTS - len(answers))
        ph = ", ".join("?" for _ in range(NUM_SLOTS))
        conn.execute(f"INSERT INTO slots VALUES (?,?,?,{ph})",
                     [rid, proj, team] + scores)


def load_json(conn, data):
    conn.execute("""CREATE TABLE jresp (
        response_id INTEGER, project VARCHAR, team VARCHAR, responses JSON)""")
    for rid, proj, team, answers in data:
        obj = {qid: score for qid, _, score in answers}
        conn.execute("INSERT INTO jresp VALUES (?,?,?,?)",
                     [rid, proj, team, json.dumps(obj)])


def load_wide(conn, data):
    all_custom = []
    for qs in CUSTOM.values():
        for qid, col, base in qs:
            if col not in [c for _, c, _ in all_custom]:
                all_custom.append((qid, col, base))
    all_cols = Q12 + all_custom
    col_defs = ", ".join(f"{col} DECIMAL(2,1)" for _, col, _ in all_cols)
    conn.execute(f"""CREATE TABLE wide (
        response_id INTEGER, project VARCHAR, team VARCHAR, {col_defs})""")
    col_names = [col for _, col, _ in all_cols]
    project_active = {}
    for proj, custom_qs in CUSTOM.items():
        active = {col for _, col, _ in Q12 + custom_qs}
        project_active[proj] = active
    for rid, proj, team, answers in data:
        answer_map = {col: score for _, col, score in answers}
        values = [answer_map.get(c) for c in col_names]
        ph = ", ".join("?" for _ in col_names)
        conn.execute(f"INSERT INTO wide VALUES (?,?,?,{ph})",
                     [rid, proj, team] + values)


def run_comparison(conn):
    """Run the same analytical query against all 4 representations."""
    print_panel("Comparison Query",
                "Mean Q01 and Q10 score by team for Phoenix.\n"
                "Same question, 4 different SQL patterns.")

    # ── EAV ──
    eav_sql = """
SELECT team,
       ROUND(AVG(CASE WHEN attribute='Q01' THEN value END), 2) AS q01_avg,
       ROUND(AVG(CASE WHEN attribute='Q10' THEN value END), 2) AS q10_avg
FROM eav WHERE project='Phoenix'
GROUP BY team ORDER BY team;
"""
    t0 = time.perf_counter()
    eav_rows = conn.execute(eav_sql).fetchall()
    eav_time = (time.perf_counter() - t0) * 1000

    # ── Slots ──
    slot_sql = """
SELECT team,
       ROUND(AVG(slot_1), 2)  AS q01_avg,
       ROUND(AVG(slot_10), 2) AS q10_avg
FROM slots WHERE project='Phoenix'
GROUP BY team ORDER BY team;
"""
    t0 = time.perf_counter()
    slot_rows = conn.execute(slot_sql).fetchall()
    slot_time = (time.perf_counter() - t0) * 1000

    # ── JSON ──
    json_sql = """
SELECT team,
       ROUND(AVG(CAST(json_extract(responses, '$.Q01') AS DOUBLE)), 2) AS q01_avg,
       ROUND(AVG(CAST(json_extract(responses, '$.Q10') AS DOUBLE)), 2) AS q10_avg
FROM jresp WHERE project='Phoenix'
GROUP BY team ORDER BY team;
"""
    t0 = time.perf_counter()
    json_rows = conn.execute(json_sql).fetchall()
    json_time = (time.perf_counter() - t0) * 1000

    # ── Wide ──
    wide_sql = """
SELECT team,
       ROUND(AVG(q01_expectations), 2) AS q01_avg,
       ROUND(AVG(q10_best_friend), 2)  AS q10_avg
FROM wide WHERE project='Phoenix'
GROUP BY team ORDER BY team;
"""
    t0 = time.perf_counter()
    wide_rows = conn.execute(wide_sql).fetchall()
    wide_time = (time.perf_counter() - t0) * 1000

    # Show all results (should match)
    for label, rows in [("EAV", eav_rows), ("Slots", slot_rows),
                        ("JSON", json_rows), ("Wide", wide_rows)]:
        print_table(f"{label} Result", ["Team", "Q01 Avg", "Q10 Avg"], rows)

    # ── Comparison table ──
    eav_count = conn.execute("SELECT COUNT(*) FROM eav").fetchone()[0]
    slot_count = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    json_count = conn.execute("SELECT COUNT(*) FROM jresp").fetchone()[0]
    wide_count = conn.execute("SELECT COUNT(*) FROM wide").fetchone()[0]

    print_table("Approach Comparison",
                ["Approach", "SQL Length", "Time (ms)", "Total Rows",
                 "Schema Flexibility", "AI-Friendly"],
                [
                    ("EAV",   f"{len(eav_sql.strip())} chars",
                     f"{eav_time:.2f}", eav_count,
                     "Infinite", "Poor — needs PIVOT"),
                    ("Slots", f"{len(slot_sql.strip())} chars",
                     f"{slot_time:.2f}", slot_count,
                     "Fixed ceiling", "Poor — opaque names"),
                    ("JSON",  f"{len(json_sql.strip())} chars",
                     f"{json_time:.2f}", json_count,
                     "Infinite", "Moderate — verbose syntax"),
                    ("Wide",  f"{len(wide_sql.strip())} chars",
                     f"{wide_time:.2f}", wide_count,
                     "Requires ALTER", "Excellent — semantic names"),
                ])


def storage_comparison(conn):
    """Compare storage footprint across approaches."""
    print_panel("Storage Comparison", "Export each to Parquet and compare file sizes")

    tables = ["eav", "slots", "jresp", "wide"]
    sizes = {}
    for t in tables:
        path = f"/tmp/compare_{t}.parquet"
        conn.execute(f"COPY {t} TO '{path}' (FORMAT PARQUET)")
        sizes[t] = os.path.getsize(path)

    print_table("Parquet File Sizes",
                ["Approach", "File Size", "Note"],
                [
                    ("EAV", f"{sizes['eav']:,} bytes", "One row per question-answer"),
                    ("Slots", f"{sizes['slots']:,} bytes", "Fixed 20 columns, NULLs padded"),
                    ("JSON", f"{sizes['jresp']:,} bytes", "Compressed JSON strings"),
                    ("Wide", f"{sizes['wide']:,} bytes", "Named columns, NULL-compressed"),
                ])


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 05: Approach Comparison",
                    "Same dataset, 4 representations, same query.\n"
                    "See the tradeoffs side by side.")

        data = generate_data()
        load_eav(conn, data)
        load_slots(conn, data)
        load_json(conn, data)
        load_wide(conn, data)

        print_panel("Data Loaded", f"{len(data)} responses loaded into all 4 tables")
        run_comparison(conn)
        storage_comparison(conn)

        print_panel("Decision Framework",
                    "For the engagement survey agent use case:\n"
                    "  - Schema variability: LOW (12 standard + bounded custom)\n"
                    "  - Query pattern: ANALYTICS (mean scores, trends, comparisons)\n"
                    "  - Consumer: AI AGENT (needs semantic column names)\n"
                    "  - Engine: COLUMNAR (NULLs are free)\n\n"
                    "Recommendation: SPARSE WIDE TABLE\n"
                    "  Simplest queries, best AI compatibility, negligible NULL overhead.\n"
                    "  JSON is the reasonable second choice — self-describing, flexible,\n"
                    "  but AI must handle json_extract syntax.")


if __name__ == "__main__":
    main()
