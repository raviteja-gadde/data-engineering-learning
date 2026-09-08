"""Lab 04: Vectorized Execution — Python Loop vs pandas vs DuckDB

Benchmarks computing the mean of 10M values three ways to demonstrate
vectorized execution. The gap between a Python for-loop, pandas, and
DuckDB shows the impact of interpretation overhead, SIMD, and
cache-optimized batch processing.

Key: we benchmark COMPUTATION time, not data conversion. Each method
starts from its native format (Python list, numpy array, pre-loaded table).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import duckdb
import numpy as np
import pandas as pd

from shared.display import print_panel, print_table

NUM_VALUES = 10_000_000


def bench_python_loop(py_list):
    """Pure Python for-loop: interpreter overhead per element."""
    total = 0.0
    count = 0
    for val in py_list:
        total += val
        count += 1
    return total / count


def bench_pandas(np_arr):
    """pandas Series.mean(): compiled C loop over contiguous array."""
    series = pd.Series(np_arr, copy=False)
    return series.mean()


def bench_numpy(np_arr):
    """NumPy mean: compiled C with SIMD intrinsics."""
    return np_arr.mean()


def bench_duckdb(conn):
    """DuckDB SQL AVG() on a pre-loaded table: vectorized engine."""
    return conn.execute("SELECT AVG(val) FROM scores").fetchone()[0]


def time_it(fn, runs=3):
    """Time a zero-arg callable, return (median_ms, result)."""
    times = []
    result = None
    for _ in range(runs):
        start = time.perf_counter()
        result = fn()
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    times.sort()
    return times[len(times) // 2], result


def main():
    print_panel("Lab 04: Vectorized Execution",
                f"Computing the mean of {NUM_VALUES:,} random values (1-5 Likert scores).\n"
                "Same computation, four execution models.\n"
                "Each starts from its NATIVE format — we measure computation, not conversion.")

    # ── Generate data in each format ──
    print_panel("Generating Data", f"{NUM_VALUES:,} random float values (1.0 - 5.0)...")
    np.random.seed(42)
    np_arr = np.round(np.random.uniform(1.0, 5.0, NUM_VALUES), 2)
    py_list = np_arr.tolist()  # native Python list for the for-loop

    # Pre-load DuckDB table
    conn = duckdb.connect()
    conn.execute("CREATE TABLE scores AS SELECT unnest($1::DOUBLE[]) AS val", [py_list])

    results = []

    # Python for-loop — 1 run (slow)
    print_panel("Running", "Python for-loop (this will take a few seconds)...")
    py_ms, py_result = time_it(lambda: bench_python_loop(py_list), runs=1)
    results.append(("Python for-loop", py_ms, py_result))

    # pandas — from numpy array (zero-copy wrap)
    print_panel("Running", "pandas Series.mean()...")
    pd_ms, pd_result = time_it(lambda: bench_pandas(np_arr), runs=5)
    results.append(("pandas", pd_ms, pd_result))

    # NumPy — native
    print_panel("Running", "NumPy array.mean()...")
    np_ms, np_result = time_it(lambda: bench_numpy(np_arr), runs=5)
    results.append(("NumPy", np_ms, np_result))

    # DuckDB — pre-loaded table
    print_panel("Running", "DuckDB SQL AVG() on pre-loaded table...")
    duck_ms, duck_result = time_it(lambda: bench_duckdb(conn), runs=5)
    results.append(("DuckDB SQL", duck_ms, duck_result))

    conn.close()

    # ── Results ──
    baseline = results[0][1]
    display_rows = []
    for name, ms, result in results:
        speedup = baseline / ms if ms > 0 else float("inf")
        display_rows.append((
            name, f"{ms:.1f} ms", f"{speedup:.0f}x", f"{result:.6f}"
        ))

    print_table(f"Mean of {NUM_VALUES:,} Values — Execution Time",
                ["Method", "Time", "vs Python loop", "Result"],
                display_rows)

    print_panel("Why the Differences?",
                "PYTHON FOR-LOOP (slowest)\n"
                "  Each iteration: bytecode dispatch, type check, box/unbox float,\n"
                "  branch prediction miss. Tens of nanoseconds per element.\n"
                f"  {NUM_VALUES:,} elements adds up quickly.\n\n"
                "PANDAS (fast)\n"
                "  Wraps the numpy array (zero-copy), then compiled C loop.\n"
                "  Small overhead from pandas Series construction.\n\n"
                "NUMPY (fastest for raw arrays)\n"
                "  Compiled C with SIMD intrinsics. Processes 4-8 doubles per\n"
                "  CPU cycle. Minimal overhead — just the array and the operation.\n\n"
                "DUCKDB SQL (fast, with query engine overhead)\n"
                "  Vectorized execution: ~2048-value batches sized for CPU L2 cache.\n"
                "  Uses SIMD internally. Small overhead from SQL parsing and the\n"
                "  query engine pipeline — but on a single AVG(), it's close to NumPy.\n\n"
                "The lesson: analytical query engines are fast because they use the\n"
                "same techniques as NumPy (compiled code, SIMD, cache-aware batching)\n"
                "but applied to SQL queries over arbitrary schemas. The Python loop\n"
                "is orders of magnitude slower because the interpreter interposes\n"
                "between every single addition.")


if __name__ == "__main__":
    main()
