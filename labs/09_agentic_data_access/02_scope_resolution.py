"""Lab 02: Scope Resolution — From "My Team" to team_id

When a user says "how is my team doing?", the agent has no team_id.
This lab simulates the conversational flow: discover teams, present
options, confirm selection, fetch data with resolved scope.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dataclasses import dataclass

from shared.display import print_panel, print_table

# ── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class UserScope:
    user_id: str
    team_id: str
    team_name: str
    project_id: str
    project_name: str
    time_period: str

@dataclass
class TeamMembership:
    team_id: str
    team_name: str
    role: str  # member | manager

@dataclass
class ActiveProject:
    project_id: str
    project_name: str
    latest_period: str

# ── Simulated User/Org Data ──────────────────────────────────────────────────

USERS = {
    "u1": {"name": "Alice Chen", "teams": [
        TeamMembership("t1", "Team Alpha", "manager"),
        TeamMembership("t2", "Team Beta", "member"),
        TeamMembership("t3", "Team Gamma", "member"),
    ]},
    "u2": {"name": "Bob Park", "teams": [
        TeamMembership("t2", "Team Beta", "manager"),
    ]},
}

PROJECTS = [
    ActiveProject("p1", "Project Phoenix", "2025-Q2"),
    ActiveProject("p2", "Project Atlas", "2025-Q1"),
]


# ── Scope Resolution Tools ──────────────────────────────────────────────────

def get_user_teams(user_id: str) -> list[TeamMembership]:
    """Tool: get the teams a user belongs to."""
    user = USERS.get(user_id)
    if not user:
        return []
    return user["teams"]


def get_active_projects() -> list[ActiveProject]:
    """Tool: get projects with survey data, ordered by recency."""
    return sorted(PROJECTS, key=lambda p: p.latest_period, reverse=True)


def resolve_default_scope(user_id: str) -> UserScope | None:
    """Tool: resolve the 'obvious' default scope for a user.

    Heuristic: if user manages exactly one team, pick that team.
    Pick the project with the most recent survey period.
    Returns None if ambiguous (multiple managed teams, no teams, etc).
    """
    teams = get_user_teams(user_id)
    if not teams:
        return None

    managed = [t for t in teams if t.role == "manager"]
    if len(managed) == 1:
        team = managed[0]
    elif len(teams) == 1:
        team = teams[0]
    else:
        return None  # Ambiguous — agent must ask

    projects = get_active_projects()
    if not projects:
        return None
    project = projects[0]  # Most recent

    return UserScope(
        user_id=user_id,
        team_id=team.team_id,
        team_name=team.team_name,
        project_id=project.project_id,
        project_name=project.project_name,
        time_period=project.latest_period,
    )


# ── Simulated Conversational Flows ──────────────────────────────────────────

def simulate_unambiguous_flow():
    """Bob manages one team — scope resolves automatically."""
    print_panel("Scenario 1: Unambiguous Scope (Bob)", "User says: 'How is my team doing?'")

    print("Agent thinks: Let me check Bob's default scope...")
    scope = resolve_default_scope("u2")

    print(f"  -> Default scope resolved: {scope.team_name}, {scope.project_name}, {scope.time_period}")
    print()
    print_panel(
        "Agent Response",
        f"Looking at {scope.team_name}'s results for {scope.project_name} ({scope.time_period})...\n"
        f"[Agent would now call get_team_overview({scope.team_id}, {scope.project_id}, {scope.time_period})]",
    )


def simulate_ambiguous_flow():
    """Alice is on 3 teams — agent must ask which one."""
    print_panel("Scenario 2: Ambiguous Scope (Alice)", "User says: 'How is my team doing?'")

    print("Agent thinks: Let me check Alice's default scope...")
    scope = resolve_default_scope("u1")
    print(f"  -> Default scope returned: {scope}")
    print("  -> Ambiguous! Alice manages 1 team but is on 3. Heuristic picked manager team.")
    print()

    # Show what happens when heuristic doesn't resolve (e.g., multiple managed teams)
    print("  Now simulating truly ambiguous case (pretend Alice manages 2 teams)...")
    teams = get_user_teams("u1")

    print_table("Alice's Teams", ["Team ID", "Team Name", "Role"],
                [(t.team_id, t.team_name, t.role) for t in teams])

    print_panel(
        "Agent Response (disambiguation)",
        "I see you're on three teams:\n"
        "  1. Team Alpha (manager)\n"
        "  2. Team Beta (member)\n"
        "  3. Team Gamma (member)\n"
        "Which team would you like to see results for?",
    )

    # Simulate user picking
    user_choice = "Team Alpha"
    print(f'\nUser says: "{user_choice}"')
    chosen = next(t for t in teams if t.team_name == user_choice)
    project = get_active_projects()[0]

    resolved = UserScope(
        user_id="u1", team_id=chosen.team_id, team_name=chosen.team_name,
        project_id=project.project_id, project_name=project.project_name,
        time_period=project.latest_period,
    )

    print_panel(
        "Resolved Scope",
        f"Team: {resolved.team_name} ({resolved.team_id})\n"
        f"Project: {resolved.project_name} ({resolved.project_id})\n"
        f"Period: {resolved.time_period}",
    )


def simulate_embedded_context():
    """Widget/embedded: scope comes from the page, not conversation."""
    print_panel(
        "Scenario 3: Embedded Widget",
        "Agent is embedded in a dashboard page.\n"
        "Page context provides: team_id=t1, project_id=p1, time_period=2025-Q2",
    )

    # In an embedded context, scope is injected — no resolution needed
    embedded_scope = UserScope(
        user_id="u1", team_id="t1", team_name="Team Alpha",
        project_id="p1", project_name="Project Phoenix", time_period="2025-Q2",
    )

    print_panel(
        "Agent Behavior",
        f"Scope was provided by the application — no disambiguation needed.\n"
        f"Agent directly calls: get_team_overview({embedded_scope.team_id}, "
        f"{embedded_scope.project_id}, {embedded_scope.time_period})\n\n"
        f"Contrast with Scenario 2: same user, same data,\n"
        f"but the embedded context eliminates the conversation round-trip.",
    )


if __name__ == "__main__":
    simulate_unambiguous_flow()
    print("\n")
    simulate_ambiguous_flow()
    print("\n")
    simulate_embedded_context()

    print_panel(
        "Key Insight",
        "Scope resolution is itself a tool contract.\n"
        "- get_user_teams() and get_active_projects() are agent tools\n"
        "- resolve_default_scope() eliminates round-trips for the common case\n"
        "- Embedded contexts bypass resolution entirely\n"
        "- The data-fetching tools (get_team_overview) are identical\n"
        "  regardless of how scope was resolved",
    )
