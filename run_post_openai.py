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
            "Run all post-OpenAI analysis steps from scores.json and lenses."
        )
    )
    parser.add_argument("--scores", default="data/scores.json", help="Scores JSON input file.")
    parser.add_argument(
        "--lenses",
        default="lenses",
        help="Lenses file, directory, or glob.",
    )
    parser.add_argument(
        "--output-root",
        default="data/analysis",
        help="Root directory where grouped analysis outputs are written.",
    )
    parser.add_argument(
        "--rubric-aggregation",
        default="latest",
        choices=["latest", "mean", "median"],
        help="How to collapse repeated rubric scores.",
    )
    parser.add_argument(
        "--source-permutations",
        type=int,
        default=1000,
        help="Permutation count for source differentiation in analysis_report.py.",
    )
    parser.add_argument(
        "--source-random-seed",
        type=int,
        default=42,
        help="Random seed for source differentiation permutations.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = Path(__file__).resolve().parent
    python = sys.executable
    output_root = Path(args.output_root)
    matrix_out = output_root / "matrix"
    lens_stats_out = output_root / "lens_stats"
    report_out = output_root / "report"

    _run(
        [
            python,
            str(root / "build_lens_article_matrix.py"),
            "--scores",
            args.scores,
            "--lenses",
            args.lenses,
            "--output-dir",
            str(matrix_out),
            "--rubric-aggregation",
            args.rubric_aggregation,
        ]
    )
    _run(
        [
            python,
            str(root / "analyze_lens_scores.py"),
            "--scores",
            args.scores,
            "--lenses",
            args.lenses,
            "--output-dir",
            str(lens_stats_out),
            "--rubric-aggregation",
            args.rubric_aggregation,
        ]
    )
    _run(
        [
            python,
            str(root / "analysis_report.py"),
            "--scores",
            args.scores,
            "--lenses",
            args.lenses,
            "--output-dir",
            str(report_out),
            "--rubric-aggregation",
            args.rubric_aggregation,
            "--source-permutations",
            str(args.source_permutations),
            "--source-random-seed",
            str(args.source_random_seed),
        ]
    )

    print("Post-OpenAI stage complete.")
    print(f"  matrix: {matrix_out}")
    print(f"  lens_stats: {lens_stats_out}")
    print(f"  report: {report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
