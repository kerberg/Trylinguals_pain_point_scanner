"""
Subreddit Discovery Agent
=========================
Purpose : Search Reddit for communities with high multilingual parenting signal density.
          Produces a ranked subreddit list used by the Content Scraper.
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
from typing import Any

import praw
from praw.models import Subreddit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Reddit API credentials come from environment variables.
# In GitHub Actions these are repo secrets. Locally, load via python-dotenv.
REDDIT_CLIENT_ID     = os.environ["REDDIT_CLIENT_ID"]
REDDIT_CLIENT_SECRET = os.environ["REDDIT_CLIENT_SECRET"]
REDDIT_USER_AGENT    = os.environ.get("REDDIT_USER_AGENT", "TryLinguals_PainPointScanner/1.0")

OUTPUT_PATH = "knowledge/subreddit-index.json"

# Maximum subreddits to retain in the ranked index.
MAX_SUBREDDITS = 30

# How many search result pages to pull per keyword cluster.
SEARCH_LIMIT = 25

# Keyword clusters — each cluster targets one dimension of the hypothesis space.
# Trilingual cluster carries 2x weight because trilingual families are the primary signal.
KEYWORD_CLUSTERS: dict[str, dict] = {
    "bilingual_parenting": {
        "terms": [
            "bilingual parenting", "bilingual children", "raising bilingual",
            "bilingual family", "bilingual kids", "bilingual toddler",
        ],
        "weight": 1.0,
    },
    "trilingual_parenting": {
        "terms": [
            "trilingual parenting", "trilingual children", "raising trilingual",
            "multilingual family", "multilingual kids", "three languages",
            "heritage language",
        ],
        "weight": 2.0,  # Primary signal for TryLinguals — TIER_1 families live here
    },
    "language_learning_children": {
        "terms": [
            "language learning kids", "children language", "kids language books",
            "foreign language children", "language immersion home",
        ],
        "weight": 1.0,
    },
    "multilingual_books": {
        "terms": [
            "bilingual books", "multilingual books", "bilingual children books",
            "dual language books", "language books kids",
        ],
        "weight": 1.2,  # Directly validates H2 (books as desired format)
    },
    "expat_immigrant_families": {
        "terms": [
            "expat parenting", "immigrant family language", "heritage language maintenance",
            "minority language", "OPOL", "one parent one language",
        ],
        "weight": 1.5,  # High frustration-gap signal; underserved combos live here
    },
}


# ---------------------------------------------------------------------------
# Reddit client factory
# ---------------------------------------------------------------------------

def build_reddit_client() -> praw.Reddit:
    """Read-only Reddit client. No authentication scope beyond public data needed."""
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
        read_only=True,
    )


# ---------------------------------------------------------------------------
# Core discovery logic
# ---------------------------------------------------------------------------

def search_subreddits_for_cluster(
    reddit: praw.Reddit,
    cluster_name: str,
    cluster_config: dict,
) -> dict[str, dict]:
    """
    Search Reddit subreddit listings for a single keyword cluster.

    Returns a dict keyed by subreddit name with raw metadata.
    Does not apply scoring — that happens in rank_subreddits().
    """
    found: dict[str, dict] = {}

    for term in cluster_config["terms"]:
        try:
            results = reddit.subreddits.search(term, limit=SEARCH_LIMIT)
            for sub in results:
                if sub.name not in found:
                    found[sub.name] = {
                        "name": sub.display_name,
                        "title": sub.title,
                        "description": (sub.public_description or "")[:300],
                        "subscribers": sub.subscribers or 0,
                        "cluster_hits": {},
                    }
                # Track which cluster hit this subreddit and how many terms matched
                hits = found[sub.name]["cluster_hits"]
                hits[cluster_name] = hits.get(cluster_name, 0) + 1

            # Respect Reddit's rate limit — 60 requests/min for read-only
            time.sleep(1.0)

        except Exception as exc:
            logger.warning("Search failed for term '%s': %s", term, exc)
            continue

    return found


def compute_signal_score(sub_data: dict) -> float:
    """
    Signal score = log-scaled subscriber base × cluster hit multiplier.

    Rationale:
      - Large generic subs (r/Parenting) dilute signal with off-topic noise.
        Log scaling prevents them from dominating.
      - Trilingual-cluster hits apply 2x weight (see KEYWORD_CLUSTERS).
      - Multiple cluster hits compound additively, not multiplicatively,
        to avoid over-weighting fringe subs that match every term.
    """
    subscriber_base = math.log10(max(sub_data["subscribers"], 10))  # floor at 10

    cluster_score = 0.0
    for cluster_name, hit_count in sub_data["cluster_hits"].items():
        weight = KEYWORD_CLUSTERS[cluster_name]["weight"]
        cluster_score += weight * hit_count

    return round(subscriber_base * cluster_score, 4)


def rank_subreddits(all_subs: dict[str, dict]) -> list[dict]:
    """
    Apply scoring, sort descending, return top MAX_SUBREDDITS.
    Strips internal cluster_hits from output — not needed downstream.
    """
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
    """Write ranked list to knowledge/subreddit-index.json."""
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "subreddit_count": len(subreddits),
        "subreddits": subreddits,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    logger.info("Subreddit index written: %d communities ranked", len(subreddits))


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

def run() -> list[dict]:
    """
    Execute the discovery pipeline end-to-end.
    Returns the ranked subreddit list (also written to disk).
    """
    logger.info("Discovery agent starting — scanning %d keyword clusters", len(KEYWORD_CLUSTERS))
    reddit = build_reddit_client()

    merged: dict[str, dict] = {}
    for cluster_name, cluster_config in KEYWORD_CLUSTERS.items():
        logger.info("Searching cluster: %s", cluster_name)
        found = search_subreddits_for_cluster(reddit, cluster_name, cluster_config)
        # Merge: if sub already seen, accumulate cluster_hits
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
    logger.info("Discovery complete. Top subreddit: r/%s (score: %s)",
                ranked[0]["name"], ranked[0]["signal_score"])
    return ranked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
