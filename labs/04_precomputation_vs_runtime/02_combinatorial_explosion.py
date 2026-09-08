"""Lab 02: Combinatorial Explosion in Rollup Tables

Starts with 2 dimensions and adds one at a time up to 8. Shows how the
number of cells (unique combinations) in a rollup table grows
exponentially. Plots the curve with matplotlib.

This is why you can't just "pre-compute everything" — the storage and
build cost explode once you cross ~4-5 dimensions.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from shared.display import print_panel, print_table
from shared.duck import duckdb_conn

# Real survey dimension names and their cardinalities
DIMENSIONS = [
    ("team",            20),   # 20 teams
    ("question",        12),   # 12 engagement survey items
    ("time_period",      8),   # 8 quarters (2 years)
    ("department",       5),   # 5 departments
    ("location",         5),   # 5 office locations
    ("category",         4),   # 4 Q12 categories
    ("project",          6),   # 6 projects
    ("reporting_type",   3),   # mean, percentile, response_rate
]


def main():
    with duckdb_conn() as conn:
        print_panel("Lab 02: Combinatorial Explosion",
                    "Adding dimensions to a rollup table.\n"
                    "Each new dimension MULTIPLIES the row count.")

        # ── Build dimension tables and compute actual distinct combos ────
        results = []
        cumulative_product = 1

        for i, (dim_name, cardinality) in enumerate(DIMENSIONS):
            # Create each dimension table
            conn.execute(f"""
                CREATE TABLE dim_{dim_name} AS
                SELECT UNNEST(generate_series(1, {cardinality})) AS {dim_name}_key
            """)

            cumulative_product *= cardinality

            # Build the actual cross join to count real rollup rows
            tables = [f"dim_{DIMENSIONS[j][0]}" for j in range(i + 1)]
            cross_sql = " CROSS JOIN ".join(tables)
            count = conn.execute(f"SELECT COUNT(*) FROM {cross_sql}").fetchone()[0]

            dim_names = " x ".join(DIMENSIONS[j][0] for j in range(i + 1))
            results.append((i + 1, dim_names, count, f"{count:,}"))

        # ── Display table ────────────────────────────────────────────────
        print_table("Rollup Table Size by Number of Dimensions",
                    ["# Dims", "Dimensions", "Rollup Rows", "Formatted"],
                    results)

        # ── Concrete example ─────────────────────────────────────────────
        print_panel("Concrete Scale",
                    f"2 dimensions:  {results[1][2]:>10,} rollup rows\n"
                    f"4 dimensions:  {results[3][2]:>10,} rollup rows\n"
                    f"6 dimensions:  {results[5][2]:>10,} rollup rows\n"
                    f"8 dimensions:  {results[7][2]:>10,} rollup rows\n\n"
                    f"Each new dimension multiplies — it's not additive.\n"
                    f"At 8 dimensions you're storing {results[7][2]:,} pre-computed cells.\n"
                    f"Add one more dimension with 10 values → {results[7][2] * 10:,} cells.")

        # ── Plot ─────────────────────────────────────────────────────────
        dims = [r[0] for r in results]
        counts = [r[2] for r in results]

        _fig, ax = plt.subplots(figsize=(10, 6))
        ax.bar(dims, counts, color="#4C78A8", edgecolor="white", linewidth=0.5)
        ax.set_xlabel("Number of Dimensions", fontsize=12)
        ax.set_ylabel("Rollup Table Rows", fontsize=12)
        ax.set_title("Combinatorial Explosion: Rollup Rows vs Dimensions", fontsize=14)
        ax.set_yscale("log")
        ax.set_xticks(dims)

        # Label each bar with dimension name and count
        for i, (d, c) in enumerate(zip(dims, counts)):
            dim_name = DIMENSIONS[i][0]
            ax.text(d, c * 1.3, f"{c:,}\n+{dim_name}", ha="center", va="bottom", fontsize=8)

        plt.tight_layout()
        plot_path = os.path.join(os.path.dirname(__file__), "combinatorial_explosion.png")
        plt.savefig(plot_path, dpi=150)
        plt.close()

        print_panel("Plot Saved", f"{plot_path}\n\nY-axis is log scale — linear would hide the small bars entirely.")

        # ── The practical lesson ─────────────────────────────────────────
        print_panel("Key Takeaway",
                    "You CANNOT pre-compute every combination of dimensions.\n\n"
                    "The solution: pick the top 2-3 dimension combos that cover ~80%\n"
                    "of actual queries (e.g., team x period, department x category).\n"
                    "Build rollup tables for those. Compute the rest at runtime.\n\n"
                    "This is why real systems use PARTIAL pre-computation:\n"
                    "  - Rollup tables for hot paths (dashboard overview)\n"
                    "  - Runtime aggregation for ad-hoc slicing\n"
                    "  - Cache layer in between for repeat queries")


if __name__ == "__main__":
    main()
