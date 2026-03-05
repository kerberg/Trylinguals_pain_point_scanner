"""
TryLinguals Pain Point Scanner — Pipeline Orchestrator
======================================================
Runs the four-agent pipeline in sequence:
  1. Discovery  (monthly — skipped in weekly run unless --run-discovery flag passed)
  2. Scraper    (weekly)
  3. Classifier (weekly)
  4. Reporter   (weekly)

Usage:
  python main.py                     # weekly run (scrape → classify → report)
  python main.py --run-discovery     # full run including subreddit discovery
  python main.py --agent discovery   # run a single agent
  python main.py --agent scraper
  python main.py --agent classifier
  python main.py --agent reporter

Environment variables required:
  REDDIT_CLIENT_ID
  REDDIT_CLIENT_SECRET
  ANTHROPIC_API_KEY

Optional:
  REDDIT_USER_AGENT  (default: TryLinguals_PainPointScanner/1.0)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Load .env in local development. In GitHub Actions, secrets are injected as env vars.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — fine in CI

# Add agents directory to path
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


def run_weekly_pipeline(run_discovery: bool = False) -> None:
    """Standard weekly execution path."""

    if run_discovery:
        logger.info("=== STAGE 1: Subreddit Discovery ===")
        discovery.run()
    else:
        logger.info("=== STAGE 1: Subreddit Discovery (skipped — monthly only) ===")

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
    """Run one agent in isolation for debugging or re-runs."""
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
    parser = argparse.ArgumentParser(
        description="TryLinguals Pain Point Scanner"
    )
    parser.add_argument(
        "--run-discovery",
        action="store_true",
        help="Include subreddit discovery stage (monthly; skipped by default)",
    )
    parser.add_argument(
        "--agent",
        choices=["discovery", "scraper", "classifier", "reporter"],
        help="Run a single agent instead of the full pipeline",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.agent:
        run_single_agent(args.agent)
    else:
        run_weekly_pipeline(run_discovery=args.run_discovery)
