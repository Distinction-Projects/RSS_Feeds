#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from analysis_module import (
    LensDefinition,
    load_workspace,
    pairwise_metric_matrix,
)

def _write_matrix_csv(path: Path, lens_names: list[str], matrix: list[list[float | None]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([""] + lens_names)
        for name, row in zip(lens_names, matrix):
            writer.writerow([name] + ["" if v is None else f"{v:.6f}" for v in row])


def _write_counts_csv(path: Path, lens_names: list[str], counts: list[list[int]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([""] + lens_names)
        for name, row in zip(lens_names, counts):
            writer.writerow([name] + row)


def _write_scores_csv(
    path: Path,
    lens_names: list[str],
    item_titles: dict[str, str],
    values_by_lens: dict[str, dict[str, float]],
) -> None:
    items_sorted = sorted(item_titles.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["item_id", "title"] + lens_names)
        for item_id in items_sorted:
            row = [item_id, item_titles[item_id]]
            for lens in lens_names:
                value = values_by_lens[lens].get(item_id)
                row.append("" if value is None else f"{value:.6f}")
            writer.writerow(row)


def _color_for_value(value: float | None, max_abs: float) -> str:
    if value is None:
        return "#f0f0f0"
    if max_abs <= 0:
        return "#ffffff"
    ratio = min(abs(value) / max_abs, 1.0)
    tint = int(255 * (1 - ratio))
    if value >= 0:
        return f"rgb(255,{tint},{tint})"
    return f"rgb({tint},{tint},255)"


def _write_heatmap_html(
    path: Path,
    title: str,
    lens_names: list[str],
    matrix: list[list[float | None]],
    counts: list[list[int]],
    value_label: str,
    max_abs: float | None = None,
) -> None:
    values = [
        v
        for row in matrix
        for v in row
        if v is not None and not math.isnan(v)
    ]
    if max_abs is None:
        max_abs = max((abs(v) for v in values), default=0.0)

    rows_html = []
    header = "".join(f"<th>{name}</th>" for name in lens_names)
    rows_html.append(f"<tr><th></th>{header}</tr>")

    for i, name in enumerate(lens_names):
        cells = [f"<th>{name}</th>"]
        for j, _ in enumerate(lens_names):
            value = matrix[i][j]
            count = counts[i][j]
            label = "" if value is None else f"{value:.3f}"
            title_attr = f"{value_label}: {label or 'n/a'} | n={count}"
            color = _color_for_value(value, max_abs)
            cells.append(
                "<td style=\"background-color: {}\" title=\"{}\">{}</td>".format(
                    color, title_attr, label
                )
            )
        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    table_rows = "\n".join(rows_html)
    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; padding: 24px; }}
    table {{ border-collapse: collapse; }}
    th, td {{ border: 1px solid #ccc; padding: 8px 10px; text-align: center; }}
    th {{ background: #f7f7f7; }}
    td {{ min-width: 90px; }}
    .note {{ color: #555; font-size: 12px; margin-top: 8px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <table>
    {table_rows}
  </table>
  <div class=\"note\">Color scale uses max absolute value {max_abs:.3f}. Hover cells for sample count.</div>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def _write_summary(
    path: Path,
    lens_defs: list[LensDefinition],
    item_titles: dict[str, str],
    lens_totals: dict[str, dict[str, float]],
    aggregation: str,
) -> None:
    payload = {
        "items_total": len(item_titles),
        "aggregation": aggregation,
        "lenses": [
            {
                "name": lens.name,
                "rubric_count": len(lens.rubric_names),
                "max_total": lens.max_total,
                "items_with_scores": len(lens_totals.get(lens.name, {})),
            }
            for lens in lens_defs
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze lens-level scores and compute covariance/correlation matrices."
    )
    parser.add_argument("--scores", default="data/scores.json", help="Scores JSON file.")
    parser.add_argument(
        "--lenses",
        default="lenses",
        help="Path to lenses file, directory, or glob pattern.",
    )
    parser.add_argument(
        "--output-dir", default="data/analysis", help="Directory for analysis outputs."
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
    lens_defs = workspace.lens_definitions
    lens_totals = workspace.raw_totals
    normalized = workspace.normalized_totals
    item_meta = workspace.item_meta
    item_titles = {item_id: meta.get("title", "") for item_id, meta in item_meta.items()}
    lens_names = workspace.lens_names

    # Raw outputs
    _write_scores_csv(output_dir / "lens_scores_raw.csv", lens_names, item_titles, lens_totals)

    cov_raw, counts = pairwise_metric_matrix(
        workspace=workspace,
        metric="covariance",
        normalized=False,
    )
    corr_raw, _ = pairwise_metric_matrix(
        workspace=workspace,
        metric="correlation",
        normalized=False,
    )

    _write_matrix_csv(output_dir / "lens_covariance_raw.csv", lens_names, cov_raw)
    _write_matrix_csv(output_dir / "lens_correlation_raw.csv", lens_names, corr_raw)
    _write_counts_csv(output_dir / "lens_pairwise_counts.csv", lens_names, counts)

    _write_heatmap_html(
        output_dir / "lens_covariance_raw.html",
        "Lens Covariance (Raw Scores)",
        lens_names,
        cov_raw,
        counts,
        "cov",
    )
    _write_heatmap_html(
        output_dir / "lens_correlation_raw.html",
        "Lens Correlation (Raw Scores)",
        lens_names,
        corr_raw,
        counts,
        "corr",
        max_abs=1.0,
    )

    # Normalized outputs
    _write_scores_csv(
        output_dir / "lens_scores_normalized.csv",
        lens_names,
        item_titles,
        normalized,
    )

    cov_norm, counts_norm = pairwise_metric_matrix(
        workspace=workspace,
        metric="covariance",
        normalized=True,
    )
    corr_norm, _ = pairwise_metric_matrix(
        workspace=workspace,
        metric="correlation",
        normalized=True,
    )

    _write_matrix_csv(
        output_dir / "lens_covariance_normalized.csv", lens_names, cov_norm
    )
    _write_matrix_csv(
        output_dir / "lens_correlation_normalized.csv", lens_names, corr_norm
    )

    _write_heatmap_html(
        output_dir / "lens_covariance_normalized.html",
        "Lens Covariance (Normalized Scores)",
        lens_names,
        cov_norm,
        counts_norm,
        "cov",
    )
    _write_heatmap_html(
        output_dir / "lens_correlation_normalized.html",
        "Lens Correlation (Normalized Scores)",
        lens_names,
        corr_norm,
        counts_norm,
        "corr",
        max_abs=1.0,
    )

    _write_summary(
        output_dir / "lens_summary.json",
        lens_defs,
        item_titles,
        lens_totals,
        args.rubric_aggregation,
    )

    print(f"Wrote analysis outputs to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
