"""Lab 04: Context Window Sizing — How Much Data Can an Agent Reason Over?

Generates survey data at four granularity levels. Measures JSON size,
estimates token count, and rates reasoning quality. Shows why flat
overview is the right default shape for agent consumption.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import json
import random

from shared.display import print_panel, print_table

random.seed(42)

# ── Engagement Survey Structure ─────────────────────────────────────────────

CATEGORIES = {
    "Basic Needs": ["Q01: Expected of me", "Q02: Materials and equipment"],
    "Individual":  ["Q03: Do best every day", "Q04: Recognition",
                    "Q05: Supervisor cares", "Q06: Encourages development"],
    "Teamwork":    ["Q07: Opinions count", "Q08: Mission/purpose",
                    "Q09: Committed to quality", "Q10: Best friend at work"],
    "Growth":      ["Q11: Progress feedback", "Q12: Learn and grow"],
}

SCALE_VALUES = [1, 2, 3, 4, 5]
NUM_RESPONDENTS = 200  # Multi-team rollup — raw responses hit ~100KB / ~25K tokens


def random_score(base=3.5, spread=0.8):
    return round(max(1.0, min(5.0, random.gauss(base, spread))), 2)


# ── Shape A: Overview (what agents should get) ───────────────────────────────

def build_overview() -> dict:
    """4 categories, 12 questions with mean scores. Flat and complete."""
    categories = []
    for cat_name, questions in CATEGORIES.items():
        q_scores = []
        for q in questions:
            score = random_score()
            q_scores.append({"question": q, "mean_score": score})
        cat_mean = round(sum(q["mean_score"] for q in q_scores) / len(q_scores), 2)
        categories.append({
            "category": cat_name,
            "mean_score": cat_mean,
            "questions": q_scores,
        })
    overall = round(sum(c["mean_score"] for c in categories) / len(categories), 2)
    return {
        "team": "Team Alpha", "project": "Project Phoenix", "period": "2025-Q2",
        "overall_score": overall, "response_count": NUM_RESPONDENTS,
        "response_rate": 0.88, "benchmark": 3.72,
        "categories": categories,
    }


# ── Shape B: Detailed metrics per question ───────────────────────────────────

def build_detailed() -> dict:
    """Each question gets mean, median, std, percentiles, distribution."""
    categories = []
    for cat_name, questions in CATEGORIES.items():
        q_details = []
        for q in questions:
            responses = [random.randint(1, 5) for _ in range(NUM_RESPONDENTS)]
            responses.sort()
            mean = round(sum(responses) / len(responses), 2)
            median = responses[len(responses) // 2]
            std = round((sum((x - mean) ** 2 for x in responses) / len(responses)) ** 0.5, 2)
            q_details.append({
                "question": q, "mean": mean, "median": median, "std_dev": std,
                "p25": responses[len(responses) // 4],
                "p75": responses[3 * len(responses) // 4],
                "min": responses[0], "max": responses[-1],
                "distribution": {str(v): responses.count(v) for v in SCALE_VALUES},
                "favorable_pct": round(100 * sum(1 for r in responses if r >= 4) / len(responses), 1),
                "unfavorable_pct": round(100 * sum(1 for r in responses if r <= 2) / len(responses), 1),
            })
        categories.append({"category": cat_name, "questions": q_details})
    return {
        "team": "Team Alpha", "project": "Project Phoenix", "period": "2025-Q2",
        "response_count": NUM_RESPONDENTS, "categories": categories,
    }


# ── Shape C: Full dimensional (categories x questions x scale values) ────────

def build_dimensional() -> dict:
    """Cross-tabulation: each question broken out by demographic dimensions."""
    dims = {
        "department": ["Engineering", "Product", "Design"],
        "location": ["New York", "Austin", "Remote"],
        "tenure_band": ["<1yr", "1-3yr", "3-5yr", "5+yr"],
    }
    categories = []
    for cat_name, questions in CATEGORIES.items():
        q_dims = []
        for q in questions:
            slices = []
            for dim_name, dim_values in dims.items():
                for dv in dim_values:
                    n = random.randint(3, 15)
                    slices.append({
                        "dimension": dim_name, "value": dv,
                        "mean": random_score(), "count": n,
                        "distribution": {str(v): random.randint(0, n) for v in SCALE_VALUES},
                    })
            q_dims.append({"question": q, "slices": slices})
        categories.append({"category": cat_name, "questions": q_dims})
    return {
        "team": "Team Alpha", "project": "Project Phoenix", "period": "2025-Q2",
        "response_count": NUM_RESPONDENTS, "categories": categories,
    }


# ── Shape D: Raw individual responses ────────────────────────────────────────

def build_raw_responses() -> dict:
    """Individual respondent rows — what should never go to an agent."""
    all_questions = [q for qs in CATEGORIES.values() for q in qs]
    responses = []
    for i in range(NUM_RESPONDENTS):
        row = {
            "respondent_id": f"r{i+1:03d}",
            "department": random.choice(["Engineering", "Product", "Design"]),
            "location": random.choice(["New York", "Austin", "Remote"]),
            "tenure_band": random.choice(["<1yr", "1-3yr", "3-5yr", "5+yr"]),
        }
        for q in all_questions:
            row[q] = random.randint(1, 5)
        responses.append(row)
    return {
        "team": "Team Alpha", "project": "Project Phoenix", "period": "2025-Q2",
        "responses": responses,
    }


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyze_shape(name: str, data: dict) -> dict:
    """Measure a data shape: JSON size, estimated tokens, reasoning rating."""
    json_str = json.dumps(data, indent=2)
    size_bytes = len(json_str.encode("utf-8"))
    est_tokens = len(json_str) // 4  # Rough: ~4 chars per token

    # Rating heuristic based on token count
    if est_tokens < 1000:
        rating = "Excellent"
        recommended = "Yes — default shape"
    elif est_tokens < 3000:
        rating = "Good"
        recommended = "On request only"
    elif est_tokens < 15000:
        rating = "Degrades"
        recommended = "No — agent summarizes instead of reasoning"
    else:
        rating = "Poor"
        recommended = "Never — agent cannot do meaningful analysis"

    return {
        "name": name,
        "size_kb": round(size_bytes / 1024, 1),
        "est_tokens": est_tokens,
        "rating": rating,
        "recommended": recommended,
        "json_str": json_str,
    }


if __name__ == "__main__":
    shapes = [
        analyze_shape("A: Overview", build_overview()),
        analyze_shape("B: Detailed", build_detailed()),
        analyze_shape("C: Dimensional", build_dimensional()),
        analyze_shape("D: Raw Responses", build_raw_responses()),
    ]

    print_table(
        "Context Window Sizing: Data Shape Comparison",
        ["Shape", "JSON Size", "Est. Tokens", "Reasoning Quality", "Recommended?"],
        [(s["name"], f"{s['size_kb']} KB", f"{s['est_tokens']:,}",
          s["rating"], s["recommended"]) for s in shapes],
    )

    # Show what each shape looks like (first 300 chars)
    for s in shapes:
        preview = s["json_str"][:300] + "..." if len(s["json_str"]) > 300 else s["json_str"]
        print_panel(f"{s['name']} — Preview (first 300 chars)", preview)

    # Context budget breakdown
    print_panel(
        "Typical Context Budget (128K token model)",
        "System prompt + tool definitions:   ~2,000 tokens\n"
        "Conversation history (10 turns):     ~3,000 tokens\n"
        "Tool result (overview shape):          ~500 tokens\n"
        "Reserved for response generation:    ~2,000 tokens\n"
        "─────────────────────────────────────────────────\n"
        "Total used:                          ~7,500 tokens\n"
        "Remaining capacity:                ~120,500 tokens\n\n"
        "The overview shape uses <1% of context.\n"
        "Raw responses would consume ~20% — and produce worse reasoning.",
    )

    print_panel(
        "Key Insight",
        "Flat, complete-for-the-context is the right shape.\n"
        "4 categories, 12 question scores, overall score, metadata.\n"
        "One level of hierarchy, no deeper nesting.\n\n"
        "More data does not mean better agent responses.\n"
        "Past ~3K tokens of data, reasoning quality degrades —\n"
        "the agent starts summarizing rather than analyzing.",
    )
