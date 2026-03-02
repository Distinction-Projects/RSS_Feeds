from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .core import (
    LensDefinition,
    build_lens_totals,
    load_lenses,
    load_scores,
    normalize_values,
)

AggregationMethod = Literal["latest", "mean", "median"]
MetricName = Literal["correlation", "covariance"]


@dataclass(frozen=True)
class AnalysisWorkspace:
    lens_definitions: list[LensDefinition]
    lens_names: list[str]
    item_ids: list[str]
    item_meta: dict[str, dict[str, str]]
    raw_totals: dict[str, dict[str, float]]
    normalized_totals: dict[str, dict[str, float]]
    aggregation: AggregationMethod

    def values(self, normalized: bool = True) -> dict[str, dict[str, float]]:
        return self.normalized_totals if normalized else self.raw_totals


def load_workspace(
    scores_path: str | Path = "scores.json",
    lenses_path: str | Path = "lenses",
    aggregation: AggregationMethod = "latest",
) -> AnalysisWorkspace:
    if aggregation not in ("latest", "mean", "median"):
        raise ValueError(f"Unknown aggregation method: {aggregation}")

    score_file = Path(scores_path)
    lens_file = Path(lenses_path)

    lens_defs, rubric_to_lens = load_lenses(lens_file)
    scores = load_scores(score_file, rubric_to_lens)
    if not scores:
        raise ValueError("No usable scores found. Check scores.json and lenses path.")

    raw_totals, item_meta = build_lens_totals(scores, lens_defs, aggregation)
    normalized = normalize_values(lens_defs, raw_totals)
    item_ids = sorted(item_meta.keys())
    lens_names = [lens.name for lens in lens_defs]

    return AnalysisWorkspace(
        lens_definitions=lens_defs,
        lens_names=lens_names,
        item_ids=item_ids,
        item_meta=item_meta,
        raw_totals=raw_totals,
        normalized_totals=normalized,
        aggregation=aggregation,
    )


def article_records(
    workspace: AnalysisWorkspace,
    normalized: bool = True,
    include_incomplete: bool = True,
) -> list[dict[str, Any]]:
    values_by_lens = workspace.values(normalized=normalized)
    rows: list[dict[str, Any]] = []

    for item_id in workspace.item_ids:
        meta = workspace.item_meta.get(item_id, {})
        row: dict[str, Any] = {
            "item_id": item_id,
            "title": meta.get("title", ""),
            "source": meta.get("source", ""),
        }

        missing = False
        for lens_name in workspace.lens_names:
            value = values_by_lens[lens_name].get(item_id)
            row[lens_name] = value
            if value is None:
                missing = True

        if include_incomplete or not missing:
            rows.append(row)

    return rows


def lens_item_matrix(
    workspace: AnalysisWorkspace,
    normalized: bool = True,
) -> tuple[list[str], list[str], list[list[float | None]]]:
    values_by_lens = workspace.values(normalized=normalized)
    matrix: list[list[float | None]] = []

    for lens_name in workspace.lens_names:
        row = [values_by_lens[lens_name].get(item_id) for item_id in workspace.item_ids]
        matrix.append(row)

    return workspace.lens_names, workspace.item_ids, matrix


def complete_item_rows(
    workspace: AnalysisWorkspace,
    normalized: bool = True,
) -> tuple[list[str], list[str], list[list[float]]]:
    values_by_lens = workspace.values(normalized=normalized)
    complete_ids: list[str] = []
    source_labels: list[str] = []
    matrix: list[list[float]] = []

    for item_id in workspace.item_ids:
        row: list[float] = []
        for lens_name in workspace.lens_names:
            value = values_by_lens[lens_name].get(item_id)
            if value is None:
                row = []
                break
            row.append(value)
        if not row:
            continue

        complete_ids.append(item_id)
        source = str(workspace.item_meta.get(item_id, {}).get("source", "")).strip() or "Unknown Source"
        source_labels.append(source)
        matrix.append(row)

    return complete_ids, source_labels, matrix


def _covariance(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) <= 1:
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / (len(xs) - 1)


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) <= 1:
        return None
    cov = _covariance(xs, ys)
    if cov is None:
        return None
    std_x = statistics.stdev(xs)
    std_y = statistics.stdev(ys)
    if std_x == 0 or std_y == 0:
        return None
    return cov / (std_x * std_y)


def pairwise_metric_matrix(
    workspace: AnalysisWorkspace,
    metric: MetricName = "correlation",
    normalized: bool = True,
) -> tuple[list[list[float | None]], list[list[int]]]:
    if metric == "correlation":
        fn = _correlation
    elif metric == "covariance":
        fn = _covariance
    else:
        raise ValueError(f"Unknown metric: {metric}")

    values_by_lens = workspace.values(normalized=normalized)
    names = workspace.lens_names
    size = len(names)
    matrix: list[list[float | None]] = [[None for _ in range(size)] for _ in range(size)]
    counts: list[list[int]] = [[0 for _ in range(size)] for _ in range(size)]

    for i, lens_a in enumerate(names):
        for j, lens_b in enumerate(names):
            xs: list[float] = []
            ys: list[float] = []
            for item_id in workspace.item_ids:
                a = values_by_lens[lens_a].get(item_id)
                b = values_by_lens[lens_b].get(item_id)
                if a is None or b is None:
                    continue
                xs.append(a)
                ys.append(b)
            counts[i][j] = len(xs)
            if xs:
                matrix[i][j] = fn(xs, ys)

    return matrix, counts


def lens_coverage(workspace: AnalysisWorkspace, normalized: bool = False) -> dict[str, int]:
    values_by_lens = workspace.values(normalized=normalized)
    return {lens_name: len(values_by_lens[lens_name]) for lens_name in workspace.lens_names}


def source_lens_means(
    workspace: AnalysisWorkspace,
    normalized: bool = True,
) -> dict[str, dict[str, float]]:
    values_by_lens = workspace.values(normalized=normalized)
    by_source: dict[str, dict[str, list[float]]] = {}

    for item_id in workspace.item_ids:
        source = str(workspace.item_meta.get(item_id, {}).get("source", "")).strip() or "Unknown Source"
        for lens_name in workspace.lens_names:
            value = values_by_lens[lens_name].get(item_id)
            if value is None:
                continue
            by_source.setdefault(source, {}).setdefault(lens_name, []).append(value)

    result: dict[str, dict[str, float]] = {}
    for source, source_values in by_source.items():
        result[source] = {
            lens_name: statistics.mean(values)
            for lens_name, values in source_values.items()
            if values
        }
    return result


def to_pandas_articles(
    workspace: AnalysisWorkspace,
    normalized: bool = True,
    include_incomplete: bool = True,
):
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("pandas is required for DataFrame exports. Install with `pip install pandas`.") from exc

    rows = article_records(
        workspace=workspace,
        normalized=normalized,
        include_incomplete=include_incomplete,
    )
    return pd.DataFrame(rows)

