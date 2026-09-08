"""Lab 01: Tool Contracts — Typed Functions as Data Access Interfaces

Defines tool contracts as Python functions with dataclass inputs/outputs.
Implements TWO backends (DuckDB and PostgreSQL) behind the same contract.
The calling code doesn't know or care which backend is active.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dataclasses import dataclass

from shared.display import print_panel, print_table
from shared.duck import duckdb_conn
from shared.pg import pg_conn

# ── Data Contracts (shared by all backends) ──────────────────────────────────

@dataclass
class CategoryScore:
    category: str
    mean_score: float
    question_count: int

@dataclass
class TeamOverview:
    team_name: str
    team_id: str
    project_name: str
    time_period: str
    overall_score: float
    category_scores: list[CategoryScore]
    response_count: int
    response_rate: float
    benchmark: float | None
    freshness: str
    suppressed: bool
    status: str = "ok"  # ok | not_entitled | suppressed | no_data

@dataclass
class TeamInfo:
    team_id: str
    team_name: str
    department: str

@dataclass
class ProjectInfo:
    project_id: str
    project_name: str
    active: bool

# ── Seed Data ────────────────────────────────────────────────────────────────

TEAMS = [
    ("t1", "Team Alpha", "Engineering"),
    ("t2", "Team Beta", "Engineering"),
    ("t3", "Team Gamma", "Product"),
]

PROJECTS = [("p1", "Project Phoenix", True), ("p2", "Project Atlas", False)]

SCORES = [
    # (team_id, project_id, period, category, mean_score, question_count, resp_count, resp_rate, benchmark)
    ("t1", "p1", "2025-Q2", "Basic Needs",  4.2, 2, 18, 0.90, 3.72),
    ("t1", "p1", "2025-Q2", "Individual",   3.8, 4, 18, 0.90, 3.72),
    ("t1", "p1", "2025-Q2", "Teamwork",     3.9, 4, 18, 0.90, 3.72),
    ("t1", "p1", "2025-Q2", "Growth",       3.5, 2, 18, 0.90, 3.72),
    ("t2", "p1", "2025-Q2", "Basic Needs",  3.6, 2, 12, 0.80, 3.72),
    ("t2", "p1", "2025-Q2", "Individual",   3.3, 4, 12, 0.80, 3.72),
    ("t2", "p1", "2025-Q2", "Teamwork",     3.4, 4, 12, 0.80, 3.72),
    ("t2", "p1", "2025-Q2", "Growth",       3.1, 2, 12, 0.80, 3.72),
]

SEED_DDL = """
DROP TABLE IF EXISTS t09_teams;
CREATE TABLE t09_teams (team_id TEXT, team_name TEXT, department TEXT);
DROP TABLE IF EXISTS t09_projects;
CREATE TABLE t09_projects (project_id TEXT, project_name TEXT, active BOOLEAN);
DROP TABLE IF EXISTS t09_scores;
CREATE TABLE t09_scores (
    team_id TEXT, project_id TEXT, time_period TEXT,
    category TEXT, mean_score REAL, question_count INT, response_count INT,
    response_rate REAL, benchmark REAL
);
"""


def seed_db(conn, flavor="duckdb"):
    """Seed tables — works for both DuckDB and psycopg2 connections."""
    cur = conn.cursor() if flavor == "pg" else conn
    execute = cur.execute

    for stmt in SEED_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            execute(stmt)

    if flavor == "duckdb":
        for t in TEAMS:
            execute("INSERT INTO t09_teams VALUES (?, ?, ?)", t)
        for p in PROJECTS:
            execute("INSERT INTO t09_projects VALUES (?, ?, ?)", p)
        for s in SCORES:
            execute("INSERT INTO t09_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", s)
    else:
        for t in TEAMS:
            cur.execute("INSERT INTO t09_teams VALUES (%s, %s, %s)", t)
        for p in PROJECTS:
            cur.execute("INSERT INTO t09_projects VALUES (%s, %s, %s)", p)
        for s in SCORES:
            cur.execute("INSERT INTO t09_scores VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", s)
        conn.commit()


# ── Backend: DuckDB ──────────────────────────────────────────────────────────

class DuckDBBackend:
    def __init__(self, conn):
        self.conn = conn
        self.name = "DuckDB"

    def get_team_list(self, user_id: str) -> list[TeamInfo]:
        rows = self.conn.execute("SELECT team_id, team_name, department FROM t09_teams").fetchall()
        return [TeamInfo(*r) for r in rows]

    def get_project_list(self, user_id: str) -> list[ProjectInfo]:
        rows = self.conn.execute("SELECT project_id, project_name, active FROM t09_projects").fetchall()
        return [ProjectInfo(*r) for r in rows]

    def get_team_overview(self, team_id: str, project_id: str, time_period: str) -> TeamOverview:
        rows = self.conn.execute(
            "SELECT category, mean_score, question_count, response_count, "
            "response_rate, benchmark FROM t09_scores "
            "WHERE team_id = ? AND project_id = ? AND time_period = ?",
            [team_id, project_id, time_period],
        ).fetchall()
        return self._build_overview(rows, team_id, project_id, time_period)

    def _build_overview(self, rows, team_id, project_id, time_period):
        if not rows:
            return TeamOverview(
                team_name="", team_id=team_id, project_name="", time_period=time_period,
                overall_score=0.0, category_scores=[], response_count=0,
                response_rate=0.0, benchmark=None,
                freshness="", suppressed=False, status="no_data",
            )
        cats = [CategoryScore(r[0], round(float(r[1]), 2), r[2]) for r in rows]
        total_qs = sum(c.question_count for c in cats)
        overall = sum(c.mean_score * c.question_count for c in cats) / total_qs if total_qs else 0
        resp = rows[0][3]
        resp_rate = float(rows[0][4]) if rows[0][4] else 0.0
        benchmark = float(rows[0][5]) if rows[0][5] else None
        team_name = self.conn.execute(
            "SELECT team_name FROM t09_teams WHERE team_id = ?", [team_id]
        ).fetchone()
        proj_name = self.conn.execute(
            "SELECT project_name FROM t09_projects WHERE project_id = ?", [project_id]
        ).fetchone()
        return TeamOverview(
            team_name=team_name[0] if team_name else team_id,
            team_id=team_id,
            project_name=proj_name[0] if proj_name else project_id,
            time_period=time_period,
            overall_score=round(overall, 2),
            category_scores=cats,
            response_count=resp,
            response_rate=resp_rate,
            benchmark=benchmark,
            freshness="2025-07-01T06:00:00Z",
            suppressed=resp < 4,
            status="suppressed" if resp < 4 else "ok",
        )


# ── Backend: PostgreSQL ──────────────────────────────────────────────────────

class PostgreSQLBackend:
    def __init__(self, conn):
        self.conn = conn
        self.name = "PostgreSQL"

    def get_team_list(self, user_id: str) -> list[TeamInfo]:
        cur = self.conn.cursor()
        cur.execute("SELECT team_id, team_name, department FROM t09_teams")
        return [TeamInfo(*r) for r in cur.fetchall()]

    def get_project_list(self, user_id: str) -> list[ProjectInfo]:
        cur = self.conn.cursor()
        cur.execute("SELECT project_id, project_name, active FROM t09_projects")
        return [ProjectInfo(*r) for r in cur.fetchall()]

    def get_team_overview(self, team_id: str, project_id: str, time_period: str) -> TeamOverview:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT category, mean_score, question_count, response_count, "
            "response_rate, benchmark FROM t09_scores "
            "WHERE team_id = %s AND project_id = %s AND time_period = %s",
            (team_id, project_id, time_period),
        )
        rows = cur.fetchall()
        if not rows:
            return TeamOverview(
                team_name="", team_id=team_id, project_name="", time_period=time_period,
                overall_score=0.0, category_scores=[], response_count=0,
                response_rate=0.0, benchmark=None,
                freshness="", suppressed=False, status="no_data",
            )
        cats = [CategoryScore(r[0], float(r[1]), r[2]) for r in rows]
        total_qs = sum(c.question_count for c in cats)
        overall = sum(c.mean_score * c.question_count for c in cats) / total_qs if total_qs else 0
        resp = rows[0][3]
        resp_rate = float(rows[0][4]) if rows[0][4] else 0.0
        benchmark = float(rows[0][5]) if rows[0][5] else None
        cur.execute("SELECT team_name FROM t09_teams WHERE team_id = %s", (team_id,))
        team_name = cur.fetchone()
        cur.execute("SELECT project_name FROM t09_projects WHERE project_id = %s", (project_id,))
        proj_name = cur.fetchone()
        return TeamOverview(
            team_name=team_name[0] if team_name else team_id,
            team_id=team_id,
            project_name=proj_name[0] if proj_name else project_id,
            time_period=time_period,
            overall_score=round(overall, 2),
            category_scores=cats,
            response_count=resp,
            response_rate=resp_rate,
            benchmark=benchmark,
            freshness="2025-07-01T06:00:00Z",
            suppressed=resp < 4,
            status="suppressed" if resp < 4 else "ok",
        )


# ── Demo: Same calling code, two backends ────────────────────────────────────

def display_overview(result: TeamOverview, backend_name: str):
    benchmark_str = f"{result.benchmark:.2f}" if result.benchmark else "N/A"
    print_panel(
        f"{backend_name} -> get_team_overview",
        f"Team: {result.team_name} | Project: {result.project_name} | Period: {result.time_period}\n"
        f"Overall: {result.overall_score} (benchmark: {benchmark_str}) | "
        f"Responses: {result.response_count} (rate: {result.response_rate:.0%}) | "
        f"Suppressed: {result.suppressed} | Status: {result.status}\n"
        f"Freshness: {result.freshness}",
    )
    print_table(
        f"Category Scores ({backend_name})",
        ["Category", "Mean Score", "Questions"],
        [(c.category, c.mean_score, c.question_count) for c in result.category_scores],
    )


def run_with_backend(backend, label: str):
    """Run the exact same sequence of tool calls against any backend."""
    print(f"\n{'='*60}")
    print(f"  Backend: {label}")
    print(f"{'='*60}")

    teams = backend.get_team_list("user_1")
    print_table("get_team_list", ["ID", "Name", "Department"],
                [(t.team_id, t.team_name, t.department) for t in teams])

    projects = backend.get_project_list("user_1")
    print_table("get_project_list", ["ID", "Name", "Active"],
                [(p.project_id, p.project_name, p.active) for p in projects])

    overview = backend.get_team_overview("t1", "p1", "2025-Q2")
    display_overview(overview, label)

    # Error state: no data
    empty = backend.get_team_overview("t1", "p1", "2024-Q1")
    print_panel(f"{label} -> no_data case", f"Status: {empty.status} | Score: {empty.overall_score}")


if __name__ == "__main__":
    # Backend 1: DuckDB
    with duckdb_conn() as duck:
        seed_db(duck, "duckdb")
        run_with_backend(DuckDBBackend(duck), "DuckDB")

    # Backend 2: PostgreSQL
    with pg_conn() as pg:
        seed_db(pg, "pg")
        run_with_backend(PostgreSQLBackend(pg), "PostgreSQL")

    print_panel(
        "Key Insight",
        "Same calling code (run_with_backend) works with both DuckDB and PostgreSQL.\n"
        "The tool contract (get_team_overview, get_team_list, get_project_list)\n"
        "abstracts the data source. The agent never knows which backend is active.",
    )
