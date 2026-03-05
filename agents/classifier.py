"""
Classifier Agent
================
Purpose : Send scraped posts to Claude (claude-sonnet-4-20250514) for classification
          against the TryLinguals hypothesis and classification schema.
          Every post is classified; comments inform but do not generate separate records.
Schedule: Weekly, immediately after Scraper.

Input  : /output/raw/raw_YYYY-MM-DD.json
Output : /output/classified/classified_YYYY-MM-DD.json
         Format: { "classified_at": ISO-8601, "schema_version": str,
                   "records": [ ClassifiedRecord ] }

ClassifiedRecord schema (extends PostRecord with classification fields):
  {
    # --- all PostRecord fields preserved ---
    "post_id":           str,
    "subreddit":         str,
    "title":             str,
    "selftext":          str,
    "url":               str,
    "score":             int,
    "num_comments":      int,
    "created_utc":       str,
    "author":            str,
    "flair":             str | None,
    "comments":          list,

    # --- classification fields (schema v1.0) ---
    "pain_type_primary":   str,   # from Pain Type enum
    "pain_type_secondary": str | None,
    "child_age_range":     str,   # from Child Age Range enum; IN_TARGET flag appended if 2-6
    "age_in_target":       bool,
    "languages":           list[str],   # all languages named
    "language_count":      int,
    "trilingual_signal_tier": str,      # TIER_1 | TIER_2 | TIER_3 | NONE (see below)
    "underserved_combo":   bool,
    "emotion":             str,   # from Emotion enum
    "hypothesis_matches":  list[str],   # subset of [H1,H2,H3,H4,H5]
    "reachable_parent":    bool,
    "noise_flag":          bool,  # True = classifier assessed as noise
    "classifier_notes":    str,   # brief reasoning from Claude
  }

Trilingual Signal Tier taxonomy (PRIMARY SIGNAL for TryLinguals):
  TIER_1 — Three or more languages explicitly named in a family / child-raising context.
            This is the highest-value signal. These families have no pre-made product.
  TIER_2 — Two languages named, with explicit frustration that a third is missing
            or that the product does not cover their combination.
  TIER_3 — Two languages named, no trilingual frustration signal.
  NONE   — Zero or one language mentioned, or language context is absent.

Batching strategy:
  Posts are sent to Claude in batches of BATCH_SIZE to stay within context limits
  and to limit cost per run. Each batch includes the full schema instructions.
  Failed batches are retried once before marking posts as unclassified.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY       = os.environ["ANTHROPIC_API_KEY"]
RAW_OUTPUT_DIR          = "output/raw"
CLASSIFIED_OUTPUT_DIR   = "output/classified"
SCHEMA_VERSION          = "1.0"
MODEL                   = "claude-sonnet-4-20250514"
MAX_TOKENS              = 4096
BATCH_SIZE              = 10    # Posts per API call
RETRY_ATTEMPTS          = 1
REQUEST_PAUSE_SECONDS   = 1.0

# ---------------------------------------------------------------------------
# Classification prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a market research classifier for TryLinguals, a customizable
multilingual children's book business. You classify Reddit posts from multilingual parenting
communities against a product validation schema.

Your output must be a JSON array. Each element corresponds to one input post.
Return ONLY valid JSON. No markdown, no explanation, no preamble.

=== CLASSIFICATION SCHEMA v1.0 ===

PAIN_TYPE (select one primary, one secondary if applicable):
  LANGUAGE_MAINTENANCE  — keeping a child active in a language they are losing or resisting
  RESOURCE_REQUEST      — asking for books, apps, tools, or any educational material
  BOOK_REQUEST          — specifically requesting books (flag separately from RESOURCE_REQUEST)
  PERSONALIZATION_REQUEST — asking for custom or personalized content
  PURCHASE_DISCUSSION   — discussing cost, where to buy, or sharing a purchase
  FRUSTRATION_GAP       — expressing that nothing exists for their specific situation
  SUCCESS_SHARE         — sharing something that worked
  RECOMMENDATION        — recommending a specific product or resource
  OTHER                 — does not fit above categories

CHILD_AGE_RANGE (select one):
  0-2 | 2-4 | 4-6 | 6-8 | 8+ | unknown
  Set age_in_target = true if range is 2-4 or 4-6.

TRILINGUAL_SIGNAL_TIER — THIS IS THE PRIMARY SIGNAL FOR TRYLINGUALS. Assign carefully:
  TIER_1 — Three or more languages explicitly named in a family or child-raising context.
            Example: "We speak English, Japanese, and Portuguese at home — can't find any books."
            This is the highest-value record. These families have no existing product.
            IMPORTANT: Parents rarely use the word "trilingual". Detect language combination
            patterns instead. All of the following are TIER_1 signals:
              - "English + Japanese household, she also goes to a French school"
              - "we speak German at home and she attends a French immersion school, plus English"
              - "my wife speaks Mandarin, I speak Portuguese, we live in the US"
              - Any post naming three or more distinct languages in a parenting/family context.
  TIER_2 — Two languages named, with explicit frustration that a third is missing or that
            existing products don't cover their specific combination.
  TIER_3 — Two languages named, no trilingual frustration signal.
  NONE   — Zero or one language mentioned, or language context is clearly absent.

UNDERSERVED_COMBO — set true when the language combination is NOT one of:
  English-Spanish, English-French, English-Mandarin, English-German.
  Common combinations have existing products. Underserved combos are TryLinguals opportunity.

EMOTION (select one):
  FRUSTRATION | CONFUSION | CURIOSITY | SATISFACTION | URGENCY
  Frustration and Urgency are the strongest validation signals.

HYPOTHESIS_MATCHES — list all that apply:
  H1 — Parent is actively struggling and seeking solutions (not academic discussion)
  H2 — Books are mentioned as a desired format
  H3 — Parent needs content spanning multiple languages simultaneously
  H4 — Parent mentions personalization, child's name in book, or custom language combo
  H5 — Parent mentions purchasing, price, budget, or has bought multilingual materials

REACHABLE_PARENT — set true only when ALL of the following hold:
  1. Post is a question or request (not a recommendation or complaint with no ask)
  2. Child age is in 0-6 range OR unknown
  3. Post is less than 30 days old (assume true unless evidence otherwise)
  4. Post is from a non-commercial account (no giveaways, no store links)

FRUSTRATION PHRASE DETECTION — The following phrases are high-signal indicators of
  FRUSTRATION_GAP or LANGUAGE_MAINTENANCE pain types. When present, weight hypothesis
  matches toward H1, H2, and H3:
    - "can't find books in"
    - "no resources for"
    - "books for [language combination]"
    - "my kid stopped speaking"
    - "losing their [language]"
    - "heritage language loss"
    - "only speaks English now"
    - "refuses to speak"
    - "nothing exists for"
    - "does anyone know of books"

NOISE_FLAG — set true when:
  - Post has academic/theoretical discussion with no practical parent question
  - Post is clearly commercial (promoting a product, affiliate links)
  - Post topic is not related to children's language learning or multilingual parenting

=== OUTPUT FORMAT ===

Return a JSON array where each element is:
{
  "post_id":               "<same as input>",
  "pain_type_primary":     "<enum value>",
  "pain_type_secondary":   "<enum value or null>",
  "child_age_range":       "<enum value>",
  "age_in_target":         <true|false>,
  "languages":             ["<lang1>", "<lang2>", ...],
  "language_count":        <int>,
  "trilingual_signal_tier": "<TIER_1|TIER_2|TIER_3|NONE>",
  "underserved_combo":     <true|false>,
  "emotion":               "<enum value>",
  "hypothesis_matches":    ["H1", "H2", ...],
  "reachable_parent":      <true|false>,
  "noise_flag":            <true|false>,
  "classifier_notes":      "<one sentence rationale for tier and hypothesis assignments>"
}

If a field cannot be determined, use null for strings and false for booleans.
Do not skip any post. Array length must equal input length.
"""


def build_user_message(posts: list[dict]) -> str:
    """
    Serialize post batch into the user turn.
    Includes title, body, and top 3 comment bodies to give context
    without exceeding token budget.
    """
    items = []
    for post in posts:
        comment_bodies = [c["body"][:200] for c in post.get("comments", [])[:3]]
        items.append({
            "post_id":   post["post_id"],
            "subreddit": post["subreddit"],
            "title":     post["title"],
            "body":      post["selftext"][:500],
            "comments":  comment_bodies,
            "score":     post["score"],
            "num_comments": post["num_comments"],
        })
    return json.dumps(items, ensure_ascii=False)


# ---------------------------------------------------------------------------
# API call + retry
# ---------------------------------------------------------------------------

def classify_batch(client: anthropic.Anthropic, posts: list[dict]) -> list[dict]:
    """
    Send one batch to Claude. Returns list of classification dicts.
    Retries once on failure. Returns empty-classification stubs on persistent failure.
    """
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": build_user_message(posts)}
                ],
            )
            raw_text = response.content[0].text.strip()
            classifications = json.loads(raw_text)

            if not isinstance(classifications, list):
                raise ValueError("Expected JSON array from classifier")
            if len(classifications) != len(posts):
                logger.warning(
                    "Batch size mismatch: sent %d, got %d. Using partial results.",
                    len(posts), len(classifications)
                )

            return classifications

        except Exception as exc:
            logger.error("Classify batch attempt %d failed: %s", attempt + 1, exc)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(5.0)
            else:
                # Return stub records so pipeline continues
                return [_stub_classification(p["post_id"]) for p in posts]

    return []


def _stub_classification(post_id: str) -> dict:
    """Fallback classification for posts that could not be classified."""
    return {
        "post_id":               post_id,
        "pain_type_primary":     "OTHER",
        "pain_type_secondary":   None,
        "child_age_range":       "unknown",
        "age_in_target":         False,
        "languages":             [],
        "language_count":        0,
        "trilingual_signal_tier": "NONE",
        "underserved_combo":     False,
        "emotion":               "CURIOSITY",
        "hypothesis_matches":    [],
        "reachable_parent":      False,
        "noise_flag":            True,
        "classifier_notes":      "Classification failed — stub record.",
    }


# ---------------------------------------------------------------------------
# Merging and output
# ---------------------------------------------------------------------------

def merge_classifications(posts: list[dict], classifications: list[dict]) -> list[dict]:
    """
    Join classification fields onto PostRecord fields.
    Uses post_id as join key; any unmatched classifications are logged and skipped.
    """
    class_by_id = {c["post_id"]: c for c in classifications}
    merged = []

    for post in posts:
        pid = post["post_id"]
        classification = class_by_id.get(pid, _stub_classification(pid))
        record = {**post, **classification}
        # Ensure post_id isn't duplicated by the merge
        record["post_id"] = pid
        merged.append(record)

    return merged


def load_latest_raw(raw_dir: str = RAW_OUTPUT_DIR) -> tuple[str, list[dict]]:
    """Load the most recent raw output file. Returns (date_str, posts)."""
    raw_files = sorted(Path(raw_dir).glob("raw_*.json"), reverse=True)
    if not raw_files:
        raise FileNotFoundError(f"No raw output files found in {raw_dir}")

    path = raw_files[0]
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    date_str = path.stem.replace("raw_", "")
    logger.info("Loaded raw file: %s (%d posts)", path.name, data["post_count"])
    return date_str, data["posts"]


def write_classified_output(records: list[dict], date_str: str) -> str:
    """Write classified records to /output/classified/classified_YYYY-MM-DD.json."""
    os.makedirs(CLASSIFIED_OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(CLASSIFIED_OUTPUT_DIR, f"classified_{date_str}.json")

    payload = {
        "classified_at":  datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "model":          MODEL,
        "record_count":   len(records),
        "records":        records,
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    logger.info("Classified output written: %s (%d records)", output_path, len(records))
    return output_path


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

def run(raw_dir: str = RAW_OUTPUT_DIR) -> str:
    """
    Execute classification pipeline end-to-end.
    Returns path to classified output file.
    """
    logger.info("Classifier agent starting (model: %s)", MODEL)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    date_str, posts = load_latest_raw(raw_dir)

    # Filter noise pre-classification: drop posts with score < 3 AND num_comments < 2
    # (matches schema signal/noise rule: 3+ upvotes OR 2+ comments)
    eligible = [
        p for p in posts
        if p["score"] >= 3 or p["num_comments"] >= 2
    ]
    logger.info("Posts eligible for classification: %d / %d", len(eligible), len(posts))

    # Classify in batches
    all_classifications: list[dict] = []
    for i in range(0, len(eligible), BATCH_SIZE):
        batch = eligible[i : i + BATCH_SIZE]
        logger.info("Classifying batch %d/%d (%d posts)",
                    (i // BATCH_SIZE) + 1,
                    (len(eligible) + BATCH_SIZE - 1) // BATCH_SIZE,
                    len(batch))
        classifications = classify_batch(client, batch)
        all_classifications.extend(classifications)
        time.sleep(REQUEST_PAUSE_SECONDS)

    records = merge_classifications(eligible, all_classifications)
    output_path = write_classified_output(records, date_str)

    tier1_count = sum(1 for r in records if r.get("trilingual_signal_tier") == "TIER_1")
    reachable_count = sum(1 for r in records if r.get("reachable_parent"))
    logger.info("Classification complete. TIER_1 posts: %d | Reachable parents: %d",
                tier1_count, reachable_count)

    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
