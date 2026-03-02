#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from analysis_module import load_workspace, pairwise_metric_matrix


def _write_matrix_csv(
    path: Path,
    lens_names: list[str],
    item_ids: list[str],
    values_by_lens: dict[str, dict[str, float]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["lens_name"] + item_ids)
        for lens in lens_names:
            row = [lens]
            for item_id in item_ids:
                value = values_by_lens[lens].get(item_id)
                row.append("" if value is None else f"{value:.6f}")
            writer.writerow(row)


def _write_titles_csv(path: Path, item_titles: dict[str, str], item_ids: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["item_id", "title"])
        for item_id in item_ids:
            writer.writerow([item_id, item_titles.get(item_id, "")])


def _write_pairwise_matrix_csv(
    path: Path,
    lens_names: list[str],
    matrix: list[list[float | None]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([""] + lens_names)
        for lens_name, row_values in zip(lens_names, matrix):
            row = [lens_name]
            for value in row_values:
                row.append("" if value is None else f"{value:.6f}")
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build lens-by-article matrix from scores with labeled lens rows."
    )
    parser.add_argument("--scores", default="data/scores.json", help="Scores JSON file.")
    parser.add_argument(
        "--lenses",
        default="lenses",
        help="Path to lenses file, directory, or glob pattern.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/analysis",
        help="Directory for matrix outputs.",
    )
    parser.add_argument(
        "--rubric-aggregation",
        default="latest",
        choices=["latest", "mean", "median"],
        help="How to collapse multiple rubric scores per item.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    workspace = load_workspace(
        scores_path=args.scores,
        lenses_path=args.lenses,
        aggregation=args.rubric_aggregation,
    )
    lens_totals = workspace.raw_totals
    normalized = workspace.normalized_totals
    item_meta = workspace.item_meta
    item_titles = {item_id: meta.get("title", "") for item_id, meta in item_meta.items()}
    lens_names = workspace.lens_names
    item_ids = workspace.item_ids

    _write_matrix_csv(output_dir / "lens_article_matrix.csv", lens_names, item_ids, lens_totals)
    _write_titles_csv(output_dir / "article_titles.csv", item_titles, item_ids)

    _write_matrix_csv(
        output_dir / "lens_article_matrix_normalized.csv",
        lens_names,
        item_ids,
        normalized,
    )

    corr_raw, _ = pairwise_metric_matrix(
        workspace=workspace,
        metric="correlation",
        normalized=False,
    )
    corr_norm, _ = pairwise_metric_matrix(
        workspace=workspace,
        metric="correlation",
        normalized=True,
    )

    _write_pairwise_matrix_csv(
        output_dir / "lens_row_correlation.csv",
        lens_names,
        corr_raw,
    )
    _write_pairwise_matrix_csv(
        output_dir / "lens_row_correlation_normalized.csv",
        lens_names,
        corr_norm,
    )

    print(f"Wrote lens/article matrices to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
