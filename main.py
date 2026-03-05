"""
TryLinguals Pain Point Scanner — Pipeline Orchestrator
======================================================
Runs the four-agent pipeline in sequence:
  1. Discovery  — SKIPPED in CI. Run locally with --run-discovery to refresh.
                  Subreddit list is hardcoded in knowledge/subreddit-index.json.
  2. Scraper    (weekly)
  3. Classifier (weekly)
  4. Reporter   (weekly)

Usage:
  python main.py                     # weekly run (scrape → classify → report)
  python main.py --run-discovery     # refresh subreddit index (run locally, not in CI)
  python main.py --agent scraper
  python main.py --agent classifier
  python main.py --agent reporter

Environment variables required:
  ANTHROPIC_API_KEY

Optional:
  REDDIT_USER_AGENT  (default: TryLinguals_PainPointScanner/1.0)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent / "agents"))
import discovery
import scraper
import classifier
import reporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("main")

SUBREDDIT_INDEX = "knowledge/subreddit-index.json"


def check_subreddit_index() -> bool:
    """Return True if subreddit index exists and has at least one entry."""
    try:
        import json
        with open(SUBREDDIT_INDEX) as f:
            data = json.load(f)
        return len(data.get("subreddits", [])) > 0
    except Exception:
        return False


def run_weekly_pipeline() -> None:
    if not check_subreddit_index():
        logger.error("No subreddit index found. Cannot continue.")
        sys.exit(1)

    logger.info("=== STAGE 2: Content Scraper ===")
    raw_path = scraper.run()
    logger.info("Raw output: %s", raw_path)

    logger.info("=== STAGE 3: Classifier ===")
    classified_path = classifier.run()
    logger.info("Classified output: %s", classified_path)

    logger.info("=== STAGE 4: Reporter ===")
    report_path = reporter.run()
    logger.info("Report: %s", report_path)

    logger.info("Pipeline complete.")


def run_single_agent(agent_name: str) -> None:
    agents = {
        "discovery":  discovery.run,
        "scraper":    scraper.run,
        "classifier": classifier.run,
        "reporter":   reporter.run,
    }
    if agent_name not in agents:
        logger.error("Unknown agent: %s. Choose from: %s", agent_name, list(agents))
        sys.exit(1)
    logger.info("Running single agent: %s", agent_name)
    agents[agent_name]()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TryLinguals Pain Point Scanner")
    parser.add_argument("--run-discovery", action="store_true",
                        help="Refresh subreddit index (run locally, not in CI)")
    parser.add_argument("--agent",
                        choices=["discovery", "scraper", "classifier", "reporter"],
                        help="Run a single agent")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.agent:
        run_single_agent(args.agent)
    elif args.run_discovery:
        logger.info("Running subreddit discovery (local only — not for CI).")
        discovery.run()
    else:
        run_weekly_pipeline()
