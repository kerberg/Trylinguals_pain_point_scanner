"""
Content Scraper Agent
=====================
Purpose : Pull posts and top comments from the ranked subreddit list.
          Produces structured JSON consumed by the Classifier.
Schedule: Weekly (runs every Monday via GitHub Actions).

Input  : /knowledge/subreddit-index.json  (produced by Discovery Agent)
Output : /output/raw/raw_YYYY-MM-DD.json
         Format: { "scraped_at": ISO-8601, "posts": [ PostRecord ] }

PostRecord schema:
  {
    "post_id":        str,          # Reddit post ID (t3_ prefix stripped)
    "subreddit":      str,          # r/name
    "title":          str,
    "selftext":       str,          # post body; empty string if link post
    "url":            str,          # full permalink
    "score":          int,          # upvotes
    "num_comments":   int,
    "created_utc":    str,          # ISO-8601
    "author":         str,          # 'deleted' if account removed
    "flair":          str | None,
    "comments": [
      {
        "comment_id": str,
        "body":       str,
        "score":      int,
        "author":     str,
        "created_utc": str,
      }
    ]
  }

Signal/noise pre-filter applied here (not in classifier):
  - Posts with score < 0 are dropped (net-downvoted)
  - Posts older than LOOKBACK_DAYS are dropped
  - Posts from AutoModerator are dropped
  - Commercial-looking accounts (karma > 50k with no post history in sub) are not filtered
    here — left to classifier to flag.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import praw

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDDIT_CLIENT_ID     = os.environ["REDDIT_CLIENT_ID"]
REDDIT_CLIENT_SECRET = os.environ["REDDIT_CLIENT_SECRET"]
REDDIT_USER_AGENT    = os.environ.get("REDDIT_USER_AGENT", "TryLinguals_PainPointScanner/1.0")

SUBREDDIT_INDEX_PATH = "knowledge/subreddit-index.json"
RAW_OUTPUT_DIR       = "output/raw"

POSTS_PER_SUBREDDIT  = 25   # Top N posts from past LOOKBACK_DAYS
COMMENTS_PER_POST    = 5    # Top N comments by score
LOOKBACK_DAYS        = 7    # Only posts from last 7 days

# Pause between subreddit requests to stay within Reddit rate limits
REQUEST_PAUSE_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Reddit client
# ---------------------------------------------------------------------------

def build_reddit_client() -> praw.Reddit:
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
        read_only=True,
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_subreddit_index(path: str = SUBREDDIT_INDEX_PATH) -> list[str]:
    """
    Load the ranked subreddit list and return display names only.
    Raises FileNotFoundError if Discovery Agent has not been run.
    """
    with open(path, "r", encoding="utf-8") as fh:
        index = json.load(fh)
    names = [sub["name"] for sub in index["subreddits"]]
    logger.info("Loaded %d subreddits from index", len(names))
    return names


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def _utc_iso(timestamp: float) -> str:
    """Convert Unix UTC timestamp to ISO-8601 string."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _is_within_lookback(created_utc: float) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    return datetime.fromtimestamp(created_utc, tz=timezone.utc) >= cutoff


def scrape_comments(submission: Any) -> list[dict]:
    """
    Pull top COMMENTS_PER_POST comments by score.
    Replaces MoreComments objects without additional API calls (load=False).
    Drops AutoModerator comments.
    """
    try:
        submission.comments.replace_more(limit=0)
    except Exception as exc:
        logger.warning("replace_more failed for %s: %s", submission.id, exc)
        return []

    comments = []
    sorted_comments = sorted(
        [c for c in submission.comments.list()
         if hasattr(c, "body") and getattr(c, "author", None) not in (None,) and
            str(getattr(c, "author", "")) != "AutoModerator"],
        key=lambda c: getattr(c, "score", 0),
        reverse=True,
    )

    for comment in sorted_comments[:COMMENTS_PER_POST]:
        comments.append({
            "comment_id":   comment.id,
            "body":         comment.body,
            "score":        comment.score,
            "author":       str(comment.author) if comment.author else "deleted",
            "created_utc":  _utc_iso(comment.created_utc),
        })

    return comments


def scrape_subreddit(reddit: praw.Reddit, subreddit_name: str) -> list[dict]:
    """
    Scrape top POSTS_PER_SUBREDDIT posts from the past LOOKBACK_DAYS.

    Uses 'new' sort to catch recent posts, then filters by date.
    'Hot' would bias toward older viral posts; 'new' is more aligned
    with weekly freshness requirements.
    """
    posts = []
    try:
        subreddit = reddit.subreddit(subreddit_name)
        # Fetch more than needed to account for date filtering
        for submission in subreddit.new(limit=POSTS_PER_SUBREDDIT * 3):
            if not _is_within_lookback(submission.created_utc):
                continue
            if submission.score < 0:
                continue
            if len(posts) >= POSTS_PER_SUBREDDIT:
                break

            comments = scrape_comments(submission)

            posts.append({
                "post_id":      submission.id,
                "subreddit":    subreddit_name,
                "title":        submission.title,
                "selftext":     submission.selftext or "",
                "url":          f"https://reddit.com{submission.permalink}",
                "score":        submission.score,
                "num_comments": submission.num_comments,
                "created_utc":  _utc_iso(submission.created_utc),
                "author":       str(submission.author) if submission.author else "deleted",
                "flair":        submission.link_flair_text,
                "comments":     comments,
            })

    except Exception as exc:
        logger.error("Failed to scrape r/%s: %s", subreddit_name, exc)

    logger.info("r/%s — %d posts scraped", subreddit_name, len(posts))
    return posts


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_raw_output(posts: list[dict]) -> str:
    """Write raw posts to /output/raw/raw_YYYY-MM-DD.json. Returns file path."""
    os.makedirs(RAW_OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = os.path.join(RAW_OUTPUT_DIR, f"raw_{date_str}.json")

    payload = {
        "scraped_at":   datetime.now(timezone.utc).isoformat(),
        "post_count":   len(posts),
        "lookback_days": LOOKBACK_DAYS,
        "posts":        posts,
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    logger.info("Raw output written: %s (%d posts total)", output_path, len(posts))
    return output_path


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

def run(subreddit_index_path: str = SUBREDDIT_INDEX_PATH) -> str:
    """
    Execute the scraping pipeline end-to-end.
    Returns path to raw output file.
    """
    logger.info("Scraper agent starting")
    reddit = build_reddit_client()
    subreddit_names = load_subreddit_index(subreddit_index_path)

    all_posts: list[dict] = []
    for name in subreddit_names:
        posts = scrape_subreddit(reddit, name)
        all_posts.extend(posts)
        time.sleep(REQUEST_PAUSE_SECONDS)

    output_path = write_raw_output(all_posts)
    logger.info("Scraper complete. Total posts: %d", len(all_posts))
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
