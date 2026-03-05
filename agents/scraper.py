"""
Content Scraper Agent
=====================
Purpose : Pull posts and top comments from the ranked subreddit list.
          Uses public Reddit JSON endpoints — no API credentials required.
          Add .json to any Reddit URL to get machine-readable data.
Schedule: Weekly (runs every Monday via GitHub Actions).

Input  : /knowledge/subreddit-index.json
Output : /output/raw/raw_YYYY-MM-DD.json

PostRecord schema:
  {
    "post_id":      str,
    "subreddit":    str,
    "title":        str,
    "selftext":     str,
    "url":          str,
    "score":        int,
    "num_comments": int,
    "created_utc":  str,
    "author":       str,
    "flair":        str | None,
    "comments":     [ { comment_id, body, score, author, created_utc } ]
  }
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT           = os.environ.get("REDDIT_USER_AGENT", "TryLinguals_PainPointScanner/1.0")
SUBREDDIT_INDEX_PATH = "knowledge/subreddit-index.json"
RAW_OUTPUT_DIR       = "output/raw"

POSTS_PER_SUBREDDIT  = 25
COMMENTS_PER_POST    = 5
LOOKBACK_DAYS        = 7
REQUEST_PAUSE        = 2.0  # seconds — polite crawl rate for public endpoints


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> dict | None:
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.warning("Request failed %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_subreddit_index(path: str = SUBREDDIT_INDEX_PATH) -> list[str]:
    with open(path, "r", encoding="utf-8") as fh:
        index = json.load(fh)
    names = [sub["name"] for sub in index["subreddits"]]
    logger.info("Loaded %d subreddits from index", len(names))
    return names


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _is_within_lookback(created_utc: float) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    return datetime.fromtimestamp(created_utc, tz=timezone.utc) >= cutoff


def scrape_comments(post_id: str, subreddit: str) -> list[dict]:
    """
    Fetch top comments for a post via the public comments JSON endpoint.
    https://www.reddit.com/r/{sub}/comments/{id}.json?sort=top&limit=5
    """
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    data = _get(url, params={"sort": "top", "limit": COMMENTS_PER_POST, "depth": 1})
    if not data or not isinstance(data, list) or len(data) < 2:
        return []

    comments = []
    children = data[1].get("data", {}).get("children", [])
    for child in children:
        c = child.get("data", {})
        if child.get("kind") != "t1":  # skip MoreComments objects
            continue
        author = c.get("author", "deleted")
        if author == "AutoModerator":
            continue
        comments.append({
            "comment_id":  c.get("id", ""),
            "body":        c.get("body", ""),
            "score":       c.get("score", 0),
            "author":      author,
            "created_utc": _utc_iso(c.get("created_utc", 0)),
        })
        if len(comments) >= COMMENTS_PER_POST:
            break

    time.sleep(REQUEST_PAUSE)
    return comments


def scrape_subreddit(subreddit_name: str) -> list[dict]:
    """
    Scrape new posts from a subreddit using the public /new.json endpoint.
    Filters to LOOKBACK_DAYS window and drops net-downvoted posts.
    """
    url = f"https://www.reddit.com/r/{subreddit_name}/new.json"
    posts = []
    after = None  # pagination cursor

    while len(posts) < POSTS_PER_SUBREDDIT:
        params = {"limit": 100, "sort": "new"}
        if after:
            params["after"] = after

        data = _get(url, params=params)
        if not data:
            break

        children = data.get("data", {}).get("children", [])
        if not children:
            break

        for child in children:
            p = child.get("data", {})
            created = p.get("created_utc", 0)

            # Stop paginating once posts are older than lookback window
            if not _is_within_lookback(created):
                return posts

            if p.get("score", 0) < 0:
                continue

            post_id   = p.get("id", "")
            subreddit = p.get("subreddit", subreddit_name)
            comments  = scrape_comments(post_id, subreddit)

            posts.append({
                "post_id":      post_id,
                "subreddit":    subreddit,
                "title":        p.get("title", ""),
                "selftext":     p.get("selftext", ""),
                "url":          f"https://reddit.com{p.get('permalink', '')}",
                "score":        p.get("score", 0),
                "num_comments": p.get("num_comments", 0),
                "created_utc":  _utc_iso(created),
                "author":       p.get("author", "deleted"),
                "flair":        p.get("link_flair_text"),
                "comments":     comments,
            })

            if len(posts) >= POSTS_PER_SUBREDDIT:
                break

        after = data.get("data", {}).get("after")
        if not after:
            break

        time.sleep(REQUEST_PAUSE)

    logger.info("r/%s — %d posts scraped", subreddit_name, len(posts))
    return posts


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_raw_output(posts: list[dict]) -> str:
    os.makedirs(RAW_OUTPUT_DIR, exist_ok=True)
    date_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = os.path.join(RAW_OUTPUT_DIR, f"raw_{date_str}.json")

    payload = {
        "scraped_at":    datetime.now(timezone.utc).isoformat(),
        "post_count":    len(posts),
        "lookback_days": LOOKBACK_DAYS,
        "posts":         posts,
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    logger.info("Raw output written: %s (%d posts)", output_path, len(posts))
    return output_path


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

def run(subreddit_index_path: str = SUBREDDIT_INDEX_PATH) -> str:
    logger.info("Scraper agent starting (no credentials required)")
    subreddit_names = load_subreddit_index(subreddit_index_path)

    all_posts: list[dict] = []
    for name in subreddit_names:
        posts = scrape_subreddit(name)
        all_posts.extend(posts)
        time.sleep(REQUEST_PAUSE)

    output_path = write_raw_output(all_posts)
    logger.info("Scraper complete. Total posts: %d", len(all_posts))
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
