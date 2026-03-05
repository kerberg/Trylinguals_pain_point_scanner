# TryLinguals Pain Point Scanner

Weekly agentic pipeline that validates five product hypotheses for TryLinguals by scanning multilingual parenting communities on Reddit.

## Architecture

Four agents run in sequence:

| Agent | Schedule | Input | Output |
|-------|----------|-------|--------|
| `discovery.py` | Monthly | Keyword clusters | `knowledge/subreddit-index.json` |
| `scraper.py` | Weekly | Subreddit index | `output/raw/raw_YYYY-MM-DD.json` |
| `classifier.py` | Weekly | Raw posts | `output/classified/classified_YYYY-MM-DD.json` |
| `reporter.py` | Weekly | Classified records | `output/reports/report_YYYY-MM-DD.md` |

## Setup

### 1. Reddit API credentials
Create a Reddit app at https://www.reddit.com/prefs/apps (script type, read-only).

### 2. Anthropic API key
Get from https://console.anthropic.com/

### 3. GitHub Secrets
Add these to your repo under Settings → Secrets → Actions:

```
REDDIT_CLIENT_ID
REDDIT_CLIENT_SECRET
REDDIT_USER_AGENT      (e.g. TryLinguals_PainPointScanner/1.0)
ANTHROPIC_API_KEY
```

### 4. Local development
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
python main.py --run-discovery   # first run: build subreddit index
python main.py                   # weekly run
```

## Running individual agents
```bash
python main.py --agent discovery
python main.py --agent scraper
python main.py --agent classifier
python main.py --agent reporter
```

## Schema
Classification schema and hypothesis definitions: [`knowledge/hypothesis-classification-schema.md`](knowledge/hypothesis-classification-schema.md)

**TIER_1 posts** — families with three or more languages named in context — are the primary TryLinguals validation signal. Every report surfaces TIER_1 count in every section.

## Reports
Weekly reports commit automatically to [`output/reports/`](output/reports/).

## GitHub Actions
Runs every Monday at 06:00 UTC. Trigger manually from the Actions tab.
To include subreddit discovery (monthly refresh), select `run_discovery: true` in the manual trigger.
