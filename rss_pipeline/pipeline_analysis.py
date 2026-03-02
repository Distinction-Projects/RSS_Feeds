from __future__ import annotations

import sys
from pathlib import Path

from .config import AnalysisRunConfig
from .process import run_command


def run_pre_openai(
    *,
    experiment: str,
    skip_summary: bool,
    scrape: bool,
    scrape_output: str | None,
    scrape_output_dir: str | None,
    scrape_output_suffix: str,
    scrape_limit: int | None,
    scrape_sleep_seconds: float | None,
    scrape_timeout_seconds: float | None,
    scrape_user_agent: str | None,
    force_rescrape: bool,
    root: Path,
) -> None:
    python = sys.executable

    if not skip_summary:
        run_command([python, str(root / "load_experiment.py"), experiment])

    if scrape:
        scrape_cmd = [
            python,
            str(root / "scrape_experiment_links.py"),
            "--input",
            experiment,
            "--output-suffix",
            scrape_output_suffix,
        ]
        if scrape_output:
            scrape_cmd.extend(["--output", scrape_output])
        if scrape_output_dir:
            scrape_cmd.extend(["--output-dir", scrape_output_dir])
        if scrape_limit is not None:
            scrape_cmd.extend(["--limit", str(scrape_limit)])
        if scrape_sleep_seconds is not None:
            scrape_cmd.extend(["--sleep-seconds", str(scrape_sleep_seconds)])
        if scrape_timeout_seconds is not None:
            scrape_cmd.extend(["--timeout-seconds", str(scrape_timeout_seconds)])
        if scrape_user_agent:
            scrape_cmd.extend(["--user-agent", scrape_user_agent])
        if force_rescrape:
            scrape_cmd.append("--force-rescrape")
        run_command(scrape_cmd)


def run_analysis(config: AnalysisRunConfig, *, root: Path) -> dict[str, str]:
    python = sys.executable
    matrix_out = config.output_root / "matrix"
    lens_stats_out = config.output_root / "lens_stats"
    report_out = config.output_root / "report"

    run_command(
        [
            python,
            str(root / "build_lens_article_matrix.py"),
            "--scores",
            str(config.scores),
            "--lenses",
            str(config.lenses_path),
            "--output-dir",
            str(matrix_out),
            "--rubric-aggregation",
            config.rubric_aggregation,
        ]
    )
    run_command(
        [
            python,
            str(root / "analyze_lens_scores.py"),
            "--scores",
            str(config.scores),
            "--lenses",
            str(config.lenses_path),
            "--output-dir",
            str(lens_stats_out),
            "--rubric-aggregation",
            config.rubric_aggregation,
        ]
    )
    run_command(
        [
            python,
            str(root / "analysis_report.py"),
            "--scores",
            str(config.scores),
            "--lenses",
            str(config.lenses_path),
            "--output-dir",
            str(report_out),
            "--rubric-aggregation",
            config.rubric_aggregation,
            "--source-permutations",
            str(config.source_permutations),
            "--source-random-seed",
            str(config.source_random_seed),
        ]
    )

    return {
        "matrix": str(matrix_out),
        "lens_stats": str(lens_stats_out),
        "report": str(report_out),
    }
