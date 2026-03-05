"""
Report Generator Agent
======================
Purpose : Consume classified JSON, produce the five-section weekly markdown report,
          commit it to the GitHub repo.
Schedule: Weekly, immediately after Classifier.

Input  : /output/classified/classified_YYYY-MM-DD.json
Output : /output/reports/report_YYYY-MM-DD.md
         Committed to GitHub via git CLI (GitHub Actions runner has write access).

Report structure (five required sections per knowledge file):
  1. Hypothesis Validation Status  — confirmed / weak / insufficient data per H1-H5
  2. Top 5 Pain Points             — by frequency and engagement weight
  3. Language Pair Frequency Table — most common combos + underserved combos flagged
  4. Reachable Parent Shortlist    — thread URL, pain type, age, language combo, date
  5. Recommended Action            — one product or validation action based on week's data

Validation thresholds (from schema):
  confirmed          — 20+ posts match hypothesis across 2+ communities
  weak               — fewer than 5 posts match hypothesis across all communities
  insufficient_data  — between 5 and 19

TIER_1 signal is surfaced in every section where it is relevant.
TIER_1 posts are the primary TryLinguals validation signal and are always called out.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "").strip()
CLASSIFIED_OUTPUT_DIR = "output/classified"
REPORTS_DIR           = "output/reports"
MODEL                 = "claude-sonnet-4-20250514"
MAX_TOKENS            = 2048

# Validation thresholds
CONFIRMED_THRESHOLD   = 20
WEAK_THRESHOLD        = 5

# Top N pain points to surface
TOP_N_PAIN_POINTS     = 5

# Common language combos — not underserved (families have existing options)
COMMON_COMBOS = {
    frozenset(["english", "spanish"]),
    frozenset(["english", "french"]),
    frozenset(["english", "mandarin"]),
    frozenset(["english", "german"]),
}

HYPOTHESES = {
    "H1": "Multilingual parenting is an active, recurring pain point",
    "H2": "Books are part of the solution parents actually want",
    "H3": "Parents need content spanning multiple languages simultaneously",
    "H4": "Personalization increases perceived value",
    "H5": "Parents demonstrate willingness to pay",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_latest_classified(classified_dir: str = CLASSIFIED_OUTPUT_DIR) -> tuple[str, list[dict]]:
    files = sorted(Path(classified_dir).glob("classified_*.json"), reverse=True)
    if not files:
        raise FileNotFoundError(f"No classified output files found in {classified_dir}")
    path = files[0]
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    date_str = path.stem.replace("classified_", "")
    logger.info("Loaded classified file: %s (%d records)", path.name, data["record_count"])
    return date_str, data["records"]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def compute_hypothesis_status(records: list[dict]) -> dict[str, dict]:
    """
    For each hypothesis, count matching posts and distinct communities.
    Return status: confirmed / weak / insufficient_data.
    """
    status = {}
    for h_id in HYPOTHESES:
        matching = [r for r in records if h_id in r.get("hypothesis_matches", [])]
        communities = {r["subreddit"] for r in matching}
        count = len(matching)

        if count >= CONFIRMED_THRESHOLD and len(communities) >= 2:
            verdict = "confirmed"
        elif count < WEAK_THRESHOLD:
            verdict = "weak"
        else:
            verdict = "insufficient_data"

        tier1_matches = sum(1 for r in matching if r.get("trilingual_signal_tier") == "TIER_1")

        status[h_id] = {
            "verdict":        verdict,
            "post_count":     count,
            "communities":    sorted(communities),
            "tier1_matches":  tier1_matches,
            "description":    HYPOTHESES[h_id],
        }
    return status


def compute_top_pain_points(records: list[dict], n: int = TOP_N_PAIN_POINTS) -> list[dict]:
    """
    Rank pain types by (count × avg_engagement_weight).
    engagement_weight = score + (num_comments × 2)  — comments weighted higher as they
    indicate active discussion, which is the signal definition in the schema.
    """
    by_pain: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        pain = r.get("pain_type_primary", "OTHER")
        if not r.get("noise_flag", False):
            by_pain[pain].append(r)

    ranked = []
    for pain_type, posts in by_pain.items():
        count = len(posts)
        avg_eng = sum(
            p.get("score", 0) + p.get("num_comments", 0) * 2
            for p in posts
        ) / count if count else 0
        tier1 = sum(1 for p in posts if p.get("trilingual_signal_tier") == "TIER_1")
        ranked.append({
            "pain_type":         pain_type,
            "post_count":        count,
            "avg_engagement":    round(avg_eng, 1),
            "weighted_score":    round(count * avg_eng, 1),
            "tier1_count":       tier1,
        })

    ranked.sort(key=lambda x: x["weighted_score"], reverse=True)
    return ranked[:n]


def compute_language_table(records: list[dict]) -> dict:
    """
    Build frequency table of language combinations.
    Flags TIER_1 combos and underserved combos separately.
    """
    combo_counter: Counter = Counter()
    combo_tier1:   Counter = Counter()
    combo_underserved: set = set()

    for r in records:
        langs = [l.lower().strip() for l in r.get("languages", []) if l]
        if not langs:
            continue
        combo_key = " + ".join(sorted(langs))
        combo_counter[combo_key] += 1
        if r.get("trilingual_signal_tier") == "TIER_1":
            combo_tier1[combo_key] += 1
        if r.get("underserved_combo"):
            combo_underserved.add(combo_key)

    rows = []
    for combo, count in combo_counter.most_common(20):
        rows.append({
            "combination":  combo,
            "frequency":    count,
            "tier1_posts":  combo_tier1.get(combo, 0),
            "underserved":  combo in combo_underserved,
        })
    return {"rows": rows, "total_combos": len(combo_counter)}


def get_reachable_shortlist(records: list[dict]) -> list[dict]:
    """Return records where reachable_parent is True, sorted by engagement."""
    reachable = [r for r in records if r.get("reachable_parent") and not r.get("noise_flag")]
    reachable.sort(
        key=lambda r: r.get("score", 0) + r.get("num_comments", 0) * 2,
        reverse=True,
    )
    return [
        {
            "url":          r["url"],
            "subreddit":    r["subreddit"],
            "pain_type":    r.get("pain_type_primary"),
            "child_age":    r.get("child_age_range"),
            "languages":    r.get("languages", []),
            "tier":         r.get("trilingual_signal_tier"),
            "date":         r.get("created_utc", "")[:10],
            "score":        r.get("score"),
            "title":        r["title"][:80],
        }
        for r in reachable
    ]


# ---------------------------------------------------------------------------
# LLM-generated recommendation (Section 5)
# ---------------------------------------------------------------------------

def generate_recommendation(
    client: anthropic.Anthropic | None,
    hypothesis_status: dict,
    top_pain_points: list[dict],
    language_table: dict,
    reachable_shortlist: list[dict],
    tier1_count: int,
) -> str:
    """
    Ask Claude to produce one concrete product or validation action
    based on this week's data. Grounded in the analytics, not freeform.
    """
    context = {
        "hypothesis_status":    {h: s["verdict"] for h, s in hypothesis_status.items()},
        "top_pain_type":        top_pain_points[0]["pain_type"] if top_pain_points else "unknown",
        "tier1_posts_this_week": tier1_count,
        "underserved_combos":   [r["combination"] for r in language_table["rows"] if r["underserved"]][:5],
        "reachable_parent_count": len(reachable_shortlist),
    }

    prompt = f"""You are the TryLinguals market research advisor.
Based on this week's Pain Point Scanner data, recommend ONE specific product or validation action.
The recommendation must be grounded in the data below. Do not recommend anything that requires
a hypothesis to be confirmed if that hypothesis is marked weak or insufficient_data.

TIER_1 posts (families with three explicit languages) are TryLinguals' primary signal.
If TIER_1 count is 5 or more, the recommendation should address trilingual family validation.

Data this week:
{json.dumps(context, indent=2)}

Respond in 3-5 sentences. Be specific. Reference the data. No preamble."""

    if client is None:
        return (
            "ANTHROPIC_API_KEY is not configured, so the AI recommendation section was skipped. "
            "Review sections 1-4 and select one manual experiment for the coming week."
        )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.error("Recommendation generation failed: %s", exc)
        return "Recommendation generation failed this week. Review raw data manually."


# ---------------------------------------------------------------------------
# Markdown report builder
# ---------------------------------------------------------------------------

def build_report(
    date_str: str,
    records: list[dict],
    hypothesis_status: dict,
    top_pain_points: list[dict],
    language_table: dict,
    reachable_shortlist: list[dict],
    recommendation: str,
) -> str:
    tier1_total = sum(1 for r in records if r.get("trilingual_signal_tier") == "TIER_1")
    tier2_total = sum(1 for r in records if r.get("trilingual_signal_tier") == "TIER_2")
    signal_posts = sum(1 for r in records if not r.get("noise_flag"))

    lines = [
        f"# TryLinguals Pain Point Scanner — Weekly Report",
        f"**Week ending:** {date_str}  ",
        f"**Posts analyzed:** {signal_posts} signal / {len(records)} total  ",
        f"**TIER_1 posts (trilingual families):** {tier1_total}  ",
        f"**TIER_2 posts (frustrated bilingual, missing third language):** {tier2_total}  ",
        "",
        "---",
        "",
        "## 1. Hypothesis Validation Status",
        "",
    ]

    for h_id, data in hypothesis_status.items():
        verdict_emoji = {"confirmed": "✅", "weak": "⚠️", "insufficient_data": "🔄"}.get(data["verdict"], "❓")
        lines.append(
            f"**{h_id} — {data['description']}**  \n"
            f"Status: {verdict_emoji} **{data['verdict'].upper()}**  \n"
            f"Posts matched: {data['post_count']} across {len(data['communities'])} communities  "
        )
        if data["tier1_matches"] > 0:
            lines.append(f"TIER_1 matches: {data['tier1_matches']} ⭐  ")
        lines.append("")

    lines += [
        "---",
        "",
        "## 2. Top 5 Pain Points by Frequency × Engagement",
        "",
        "| Pain Type | Posts | Avg Engagement | Weighted Score | TIER_1 |",
        "|-----------|-------|----------------|----------------|--------|",
    ]
    for pp in top_pain_points:
        t1_flag = f"⭐ {pp['tier1_count']}" if pp["tier1_count"] > 0 else "—"
        lines.append(
            f"| {pp['pain_type']} | {pp['post_count']} | "
            f"{pp['avg_engagement']} | {pp['weighted_score']} | {t1_flag} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 3. Language Pair Frequency Table",
        "",
        f"Total distinct combinations observed: {language_table['total_combos']}",
        "",
        "| Combination | Frequency | TIER_1 Posts | Underserved |",
        "|-------------|-----------|--------------|-------------|",
    ]
    for row in language_table["rows"]:
        us_flag = "🎯 YES" if row["underserved"] else "no"
        t1_flag = f"⭐ {row['tier1_posts']}" if row["tier1_posts"] > 0 else "—"
        lines.append(
            f"| {row['combination']} | {row['frequency']} | {t1_flag} | {us_flag} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 4. Reachable Parent Shortlist",
        "",
        "_These threads are Week 2 outreach targets. Respond publicly in-thread. Do not DM._",
        "",
    ]

    if reachable_shortlist:
        lines += [
            "| Date | Subreddit | Pain Type | Age | Languages | Tier | Title |",
            "|------|-----------|-----------|-----|-----------|------|-------|",
        ]
        for r in reachable_shortlist:
            langs = ", ".join(r["languages"]) if r["languages"] else "—"
            tier_flag = f"⭐ {r['tier']}" if r["tier"] in ("TIER_1", "TIER_2") else r["tier"]
            lines.append(
                f"| {r['date']} | r/{r['subreddit']} | {r['pain_type']} | "
                f"{r['child_age']} | {langs} | {tier_flag} | "
                f"[{r['title']}]({r['url']}) |"
            )
    else:
        lines.append("_No reachable parents identified this week._")

    lines += [
        "",
        "---",
        "",
        "## 5. Recommended Action",
        "",
        recommendation,
        "",
        "---",
        "",
        f"_Generated by TryLinguals Pain Point Scanner v1.0 | Schema version 1.0_",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Git commit helper
# ---------------------------------------------------------------------------

def commit_report(report_path: str, date_str: str) -> None:
    """
    Stage and commit the report file.
    In GitHub Actions the GITHUB_TOKEN grants write access.
    This assumes git is configured with user name/email via workflow env.
    """
    try:
        subprocess.run(["git", "config", "user.email", "scanner@trylinguals.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TryLinguals Scanner"],
                       check=True, capture_output=True)
        subprocess.run(["git", "add", report_path], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"scanner: weekly report {date_str}"],
            check=True, capture_output=True
        )
        subprocess.run(["git", "push"], check=True, capture_output=True)
        logger.info("Report committed and pushed: %s", report_path)
    except subprocess.CalledProcessError as exc:
        logger.error("Git commit failed: %s\n%s", exc, exc.stderr)


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

def run(classified_dir: str = CLASSIFIED_OUTPUT_DIR) -> str:
    """
    Execute report generation pipeline end-to-end.
    Returns path to the written report file.
    """
    logger.info("Reporter agent starting")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

    date_str, records = load_latest_classified(classified_dir)

    hypothesis_status = compute_hypothesis_status(records)
    top_pain_points   = compute_top_pain_points(records)
    language_table    = compute_language_table(records)
    reachable         = get_reachable_shortlist(records)
    tier1_count       = sum(1 for r in records if r.get("trilingual_signal_tier") == "TIER_1")

    recommendation = generate_recommendation(
        client, hypothesis_status, top_pain_points, language_table, reachable, tier1_count
    )

    report_md = build_report(
        date_str, records, hypothesis_status, top_pain_points,
        language_table, reachable, recommendation
    )

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"report_{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report_md)
    logger.info("Report written: %s", report_path)

    commit_report(report_path, date_str)

    return report_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
