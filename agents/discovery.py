"""
Subreddit Discovery Agent
=========================
Purpose : Search Reddit for communities with high multilingual parenting signal density.
          Uses public Reddit JSON endpoints — no API credentials required.
Schedule: Monthly (not weekly). Run manually or via cron on the 1st.

Input  : Keyword clusters defined in KEYWORD_CLUSTERS below.
Output : /knowledge/subreddit-index.json
         Format: { "generated_at": ISO-8601, "subreddits": [ { "name": str,
                   "signal_score": float, "sample_titles": [str], "rank": int } ] }

Signal scoring:
  - Base score from subscriber count (log-scaled, capped)
  - Multiplier when keyword cluster matches are found in subreddit description / title
  - Trilingual keyword cluster is weighted 2x — these communities are TryLinguals priority
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Polite user agent for public requests — no credentials needed
USER_AGENT  = os.environ.get("REDDIT_USER_AGENT", "TryLinguals_PainPointScanner/1.0")
OUTPUT_PATH = "knowledge/subreddit-index.json"
MAX_SUBREDDITS = 30
REQUEST_PAUSE  = 2.0   # seconds between requests — stay polite

# Reddit public search endpoint
SUBREDDIT_SEARCH_URL = "https://www.reddit.com/subreddits/search.json"

KEYWORD_CLUSTERS: dict[str, dict] = {
    "bilingual_parenting": {
        "terms": [
            "bilingual parenting", "bilingual children", "raising bilingual",
            "bilingual family", "bilingual kids",
        ],
        "weight": 1.0,
    },
    "trilingual_parenting": {
        "terms": [
            "trilingual parenting", "trilingual children", "raising trilingual",
            "multilingual family", "multilingual kids", "heritage language",
        ],
        "weight": 2.0,  # Primary signal — TIER_1 families live here
    },
    "language_learning_children": {
        "terms": [
            "language learning kids", "children language", "language immersion home",
            "kids second language",
        ],
        "weight": 1.0,
    },
    "multilingual_books": {
        "terms": [
            "bilingual books", "multilingual books", "bilingual children books",
            "dual language books",
        ],
        "weight": 1.2,  # Directly validates H2
    },
    "expat_immigrant_families": {
        "terms": [
            "expat parenting", "immigrant family language", "heritage language maintenance",
            "minority language", "OPOL one parent one language",
        ],
        "weight": 1.5,  # High frustration-gap signal
    },
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> dict | None:
    """GET with polite headers. Returns parsed JSON or None on failure."""
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.warning("Request failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Core discovery logic
# ---------------------------------------------------------------------------

def search_subreddits_for_cluster(
    cluster_name: str,
    cluster_config: dict,
) -> dict[str, dict]:
    """
    Search Reddit's public subreddit search for a single keyword cluster.
    Returns dict keyed by subreddit name with raw metadata.
    """
    found: dict[str, dict] = {}

    for term in cluster_config["terms"]:
        data = _get(SUBREDDIT_SEARCH_URL, params={"q": term, "limit": 25, "type": "sr"})
        if not data:
            continue

        children = data.get("data", {}).get("children", [])
        for child in children:
            sub = child.get("data", {})
            name = sub.get("display_name", "")
            if not name:
                continue

            if name not in found:
                found[name] = {
                    "name":        name,
                    "title":       sub.get("title", ""),
                    "description": (sub.get("public_description") or "")[:300],
                    "subscribers": sub.get("subscribers") or 0,
                    "cluster_hits": {},
                }
            hits = found[name]["cluster_hits"]
            hits[cluster_name] = hits.get(cluster_name, 0) + 1

        time.sleep(REQUEST_PAUSE)

    return found


def compute_signal_score(sub_data: dict) -> float:
    """
    Signal score = log-scaled subscriber base × cluster hit multiplier.
    Trilingual cluster hits apply 2x weight.
    """
    subscriber_base = math.log10(max(sub_data["subscribers"], 10))
    cluster_score = 0.0
    for cluster_name, hit_count in sub_data["cluster_hits"].items():
        weight = KEYWORD_CLUSTERS[cluster_name]["weight"]
        cluster_score += weight * hit_count
    return round(subscriber_base * cluster_score, 4)


def rank_subreddits(all_subs: dict[str, dict]) -> list[dict]:
    """Score, sort descending, return top MAX_SUBREDDITS."""
    scored = []
    for name, data in all_subs.items():
        score = compute_signal_score(data)
        scored.append({
            "name":         data["name"],
            "title":        data["title"],
            "description":  data["description"],
            "subscribers":  data["subscribers"],
            "signal_score": score,
        })

    scored.sort(key=lambda x: x["signal_score"], reverse=True)
    top = scored[:MAX_SUBREDDITS]
    for rank, sub in enumerate(top, start=1):
        sub["rank"] = rank
    return top


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_subreddit_index(subreddits: list[dict]) -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    payload = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "subreddit_count": len(subreddits),
        "subreddits":      subreddits,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    logger.info("Subreddit index written: %d communities ranked", len(subreddits))


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

def run() -> list[dict]:
    logger.info("Discovery agent starting — %d keyword clusters", len(KEYWORD_CLUSTERS))

    merged: dict[str, dict] = {}
    for cluster_name, cluster_config in KEYWORD_CLUSTERS.items():
        logger.info("Searching cluster: %s", cluster_name)
        found = search_subreddits_for_cluster(cluster_name, cluster_config)
        for name, data in found.items():
            if name not in merged:
                merged[name] = data
            else:
                for c, hits in data["cluster_hits"].items():
                    merged[name]["cluster_hits"][c] = (
                        merged[name]["cluster_hits"].get(c, 0) + hits
                    )

    ranked = rank_subreddits(merged)
    write_subreddit_index(ranked)
    logger.info("Discovery complete. Top: r/%s (score: %s)",
                ranked[0]["name"], ranked[0]["signal_score"])
    return ranked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
