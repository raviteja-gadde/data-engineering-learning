"""Lab 02: Compression in Columnar Storage

Creates a DuckDB table with columns of varying cardinality to show how
columnar compression adapts. Demonstrates that a 95% NULL column (sparse
custom question responses) barely affects storage — NULLs are free.

Uses two measurement approaches:
- pragma_storage_info for compression TYPE selection per column
- Parquet export for per-column compressed SIZE comparison
"""

import sys, os, random, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.duck import duckdb_conn
from shared.display import print_table, print_sql, print_panel

random.seed(42)
NUM_ROWS = 500_000


def build_table(conn):
    """Create and populate a table with columns of different cardinality."""
    # Use SQL generation for speed instead of Python loops
    conn.execute(f"""
        CREATE TABLE compression_demo AS
        SELECT
            i AS row_id,
            CASE i % 8
                WHEN 0 THEN 'Engineering' WHEN 1 THEN 'Sales'
                WHEN 2 THEN 'Product'     WHEN 3 THEN 'Support'
                WHEN 4 THEN 'Marketing'   WHEN 5 THEN 'Finance'
                WHEN 6 THEN 'Legal'       ELSE 'HR'
            END AS team_id,
            1 + (hash(i) % 5)::INTEGER AS response_value,
            round(3.5 + 0.8 * sin(i::DOUBLE / 100), 4) AS score_precise,
            CASE WHEN hash(i + 99) % 10 < 3 THEN
                (ARRAY['Great team', 'Need tools', 'Supportive mgr',
                       'Want training', 'Good balance', 'Improve comms',
                       'Proud of mission', 'Feeling burned out',
                       'Love flexibility', 'Clearer expectations']
                )[1 + (hash(i + 50) % 10)::INTEGER]
            ELSE NULL END AS comment_text,
            CASE WHEN hash(i + 200) % 100 < 5
                 THEN 1 + (hash(i + 300) % 5)::INTEGER
                 ELSE NULL END AS custom_q1
        FROM range({NUM_ROWS}) tbl(i)
    """)


def show_cardinality(conn):
    """Show distinct value counts to explain compression differences."""
    sql = """
        SELECT 'team_id' AS col, COUNT(DISTINCT team_id) AS distinct_vals
        FROM compression_demo
        UNION ALL SELECT 'response_value', COUNT(DISTINCT response_value) FROM compression_demo
        UNION ALL SELECT 'score_precise', COUNT(DISTINCT score_precise) FROM compression_demo
        UNION ALL SELECT 'comment_text', COUNT(DISTINCT comment_text) FROM compression_demo
        UNION ALL SELECT 'custom_q1', COUNT(DISTINCT custom_q1) FROM compression_demo
    """
    rows = conn.execute(sql).fetchall()
    print_table("Distinct Values per Column (drives compression choice)",
                ["Column", "Distinct Values"], rows)


def show_compression_types(conn):
    """Query DuckDB's storage metadata to show compression type per column."""
    conn.execute("CHECKPOINT")

    storage_sql = """
        SELECT
            column_name,
            compression,
            segment_type,
            COUNT(*) AS num_segments,
            SUM(count) AS total_values
        FROM pragma_storage_info('compression_demo')
        WHERE segment_type NOT IN ('VALIDITY')
        GROUP BY column_name, compression, segment_type
        ORDER BY column_name, compression
    """
    print_panel("Compression Types",
                "DuckDB chooses compression per-column based on data characteristics.")
    print_sql(storage_sql)
    rows = conn.execute(storage_sql).fetchall()
    print_table("Compression Type per Column",
                ["Column", "Compression", "Type", "Segments", "Values"],
                rows)


def measure_column_sizes(conn):
    """Export each column to Parquet to measure compressed size."""
    tmpdir = tempfile.mkdtemp()
    columns = ['team_id', 'response_value', 'score_precise',
               'comment_text', 'custom_q1']

    sizes = {}
    for col in columns:
        path = os.path.join(tmpdir, f"{col}.parquet")
        conn.execute(f"COPY (SELECT {col} FROM compression_demo) TO '{path}' (FORMAT PARQUET)")
        sizes[col] = os.path.getsize(path)
        os.remove(path)

    # Full table
    full_path = os.path.join(tmpdir, "all.parquet")
    conn.execute(f"COPY compression_demo TO '{full_path}' (FORMAT PARQUET)")
    sizes['ALL COLUMNS'] = os.path.getsize(full_path)
    os.remove(full_path)
    os.rmdir(tmpdir)

    # Uncompressed reference: NUM_ROWS * raw bytes per type
    raw_sizes = {
        'team_id':        NUM_ROWS * 11,   # ~11 bytes avg string
        'response_value': NUM_ROWS * 4,    # 4 bytes int
        'score_precise':  NUM_ROWS * 8,    # 8 bytes double
        'comment_text':   NUM_ROWS * 5,    # ~15 bytes * 30% non-null
        'custom_q1':      NUM_ROWS * 4,    # 4 bytes int (but 95% null)
    }

    def fmt(b):
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024*1024):.1f} MB"

    display_rows = []
    for col in columns:
        raw = raw_sizes[col]
        compressed = sizes[col]
        ratio = raw / compressed if compressed > 0 else 0
        display_rows.append((col, fmt(raw), fmt(compressed), f"{ratio:.1f}x"))

    display_rows.append(("---", "---", "---", "---"))
    total_raw = sum(raw_sizes.values())
    display_rows.append(("ALL COLUMNS", fmt(total_raw),
                        fmt(sizes['ALL COLUMNS']),
                        f"{total_raw / sizes['ALL COLUMNS']:.1f}x"))

    print_table("Per-Column Compressed Size (Parquet export)",
                ["Column", "Raw (est.)", "Compressed", "Ratio"],
                display_rows)
    return sizes


def show_null_impact(conn, sizes):
    """Show how little the 95% NULL column costs."""
    total = sizes['ALL COLUMNS']
    custom = sizes['custom_q1']

    card = conn.execute("""
        SELECT COUNT(*) AS total_rows,
               COUNT(custom_q1) AS non_null,
               ROUND(100.0 * COUNT(custom_q1) / COUNT(*), 1) AS pct_non_null
        FROM compression_demo
    """).fetchone()
    total_rows, non_null, pct = card

    pct_of_table = round(100.0 * custom / total, 1) if total > 0 else 0

    def fmt(b):
        return f"{b:,} bytes ({b / 1024:.1f} KB)"

    print_table("NULL Column Impact (custom_q1 — ~95% NULL)",
                ["Metric", "Value"],
                [
                    ("Total rows", f"{total_rows:,}"),
                    ("Non-NULL rows in custom_q1", f"{non_null:,} ({pct}%)"),
                    ("custom_q1 compressed size", fmt(custom)),
                    ("Total table compressed size", fmt(total)),
                    ("custom_q1 as % of table", f"{pct_of_table}%"),
                ])


def main():
    db_path = os.path.join(os.path.dirname(__file__), "compression_demo.duckdb")
    if os.path.exists(db_path):
        os.remove(db_path)

    with duckdb_conn(db_path) as conn:
        print_panel("Lab 02: Compression in Columnar Storage",
                    f"Creating {NUM_ROWS:,} rows with columns of varying cardinality.\n"
                    "Columnar engines choose compression per-column based on data "
                    "characteristics.")

        build_table(conn)
        show_cardinality(conn)
        show_compression_types(conn)
        sizes = measure_column_sizes(conn)
        show_null_impact(conn, sizes)

        print_panel("Key Takeaway",
                    "Columnar storage compresses each column independently:\n"
                    "- Low cardinality (team_id): Dictionary encoding → huge ratio\n"
                    "- Very low cardinality (response_value): BitPacking → compact\n"
                    "- High cardinality (score_precise): ALP (floats) → less compressible\n"
                    "- Strings (comment_text): dictionary encoding, repeated strings → ints\n"
                    "- 95% NULL column (custom_q1): barely registers in storage\n\n"
                    "NULLs are essentially free in columnar storage. Adding sparse\n"
                    "custom survey questions to a wide table is cheap — the\n"
                    "'ever-widening table' concern doesn't apply when most values are NULL.")

    if os.path.exists(db_path):
        os.remove(db_path)


if __name__ == "__main__":
    main()
