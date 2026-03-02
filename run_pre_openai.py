#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(command: list[str]) -> None:
    print("$ " + " ".join(command))
    subprocess.run(command, check=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run all pre-OpenAI steps: experiment validation/summary and optional "
            "scrape enrichment."
        )
    )
    parser.add_argument(
        "--experiment",
        default="data/rss_openai_daily.json",
        help="Experiment file, directory, or glob passed to loaders/scraper.",
    )
    parser.add_argument(
        "--skip-summary",
        action="store_true",
        help="Skip load_experiment summary output.",
    )
    parser.add_argument(
        "--scrape",
        action="store_true",
        help="Run scrape_experiment_links.py after summary.",
    )
    parser.add_argument(
        "--scrape-output",
        default=None,
        help="Optional output path when scraping a single experiment input.",
    )
    parser.add_argument(
        "--scrape-output-dir",
        default=None,
        help="Optional output directory for batch scrape mode.",
    )
    parser.add_argument(
        "--scrape-output-suffix",
        default="_scraped",
        help="Output suffix for batch scrape outputs.",
    )
    parser.add_argument(
        "--scrape-limit",
        type=int,
        default=None,
        help="Optional max links to scrape.",
    )
    parser.add_argument(
        "--scrape-sleep-seconds",
        type=float,
        default=None,
        help="Optional delay between scrape requests.",
    )
    parser.add_argument(
        "--scrape-timeout-seconds",
        type=float,
        default=None,
        help="Optional timeout per scrape request.",
    )
    parser.add_argument(
        "--scrape-user-agent",
        default=None,
        help="Optional user-agent override for scraping.",
    )
    parser.add_argument(
        "--force-rescrape",
        action="store_true",
        help="Rescrape even when item.scraped already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = Path(__file__).resolve().parent
    python = sys.executable

    if not args.skip_summary:
        _run([python, str(root / "load_experiment.py"), args.experiment])

    if args.scrape:
        scrape_cmd = [
            python,
            str(root / "scrape_experiment_links.py"),
            "--input",
            args.experiment,
            "--output-suffix",
            args.scrape_output_suffix,
        ]
        if args.scrape_output:
            scrape_cmd.extend(["--output", args.scrape_output])
        if args.scrape_output_dir:
            scrape_cmd.extend(["--output-dir", args.scrape_output_dir])
        if args.scrape_limit is not None:
            scrape_cmd.extend(["--limit", str(args.scrape_limit)])
        if args.scrape_sleep_seconds is not None:
            scrape_cmd.extend(["--sleep-seconds", str(args.scrape_sleep_seconds)])
        if args.scrape_timeout_seconds is not None:
            scrape_cmd.extend(["--timeout-seconds", str(args.scrape_timeout_seconds)])
        if args.scrape_user_agent:
            scrape_cmd.extend(["--user-agent", args.scrape_user_agent])
        if args.force_rescrape:
            scrape_cmd.append("--force-rescrape")
        _run(scrape_cmd)

    print("Pre-OpenAI stage complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
