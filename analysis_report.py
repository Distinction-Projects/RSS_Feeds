#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

from analysis_module import (
    build_lens_totals,
    load_lenses,
    load_scores,
    normalize_values,
)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _pairwise_matrix(
    lens_names: list[str],
    values_by_lens: dict[str, dict[str, float]],
    item_ids: list[str],
    fn,
) -> tuple[list[list[float | None]], list[list[int]]]:
    size = len(lens_names)
    matrix: list[list[float | None]] = [[None for _ in range(size)] for _ in range(size)]
    counts: list[list[int]] = [[0 for _ in range(size)] for _ in range(size)]

    for i, lens_a in enumerate(lens_names):
        for j, lens_b in enumerate(lens_names):
            xs: list[float] = []
            ys: list[float] = []
            for item_id in item_ids:
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


def _write_matrix_csv(path: Path, headers: list[str], matrix: list[list[float | None]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([""] + headers)
        for name, row in zip(headers, matrix):
            writer.writerow([name] + ["" if v is None else f"{v:.6f}" for v in row])


def _write_counts_csv(path: Path, headers: list[str], counts: list[list[int]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([""] + headers)
        for name, row in zip(headers, counts):
            writer.writerow([name] + row)


def _write_matrix_long_csv(
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


def _write_article_metadata(path: Path, item_meta: dict[str, dict[str, str]], item_ids: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["item_id", "title", "source"])
        for item_id in item_ids:
            meta = item_meta.get(item_id, {})
            writer.writerow([item_id, meta.get("title", ""), meta.get("source", "")])


def _source_counts(labels: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return counts


def _multivariate_source_separation(
    matrix: list[list[float]],
    source_labels: list[str],
) -> dict[str, Any] | None:
    if not matrix or len(matrix) != len(source_labels):
        return None
    dims = len(matrix[0])
    if dims == 0:
        return None
    if any(len(row) != dims for row in matrix):
        raise ValueError("All source separation rows must have the same dimensionality.")

    n = len(matrix)
    by_source: dict[str, list[int]] = {}
    for idx, label in enumerate(source_labels):
        by_source.setdefault(label, []).append(idx)

    k = len(by_source)
    if k < 2 or n <= k:
        return None

    grand_mean = [0.0 for _ in range(dims)]
    for row in matrix:
        for dim_idx, value in enumerate(row):
            grand_mean[dim_idx] += value
    grand_mean = [value / n for value in grand_mean]

    ss_total = 0.0
    for row in matrix:
        ss_total += sum((value - grand_mean[dim_idx]) ** 2 for dim_idx, value in enumerate(row))

    ss_within = 0.0
    for row_indexes in by_source.values():
        group_size = len(row_indexes)
        if group_size == 0:
            continue
        group_mean = [0.0 for _ in range(dims)]
        for row_index in row_indexes:
            row = matrix[row_index]
            for dim_idx, value in enumerate(row):
                group_mean[dim_idx] += value
        group_mean = [value / group_size for value in group_mean]

        for row_index in row_indexes:
            row = matrix[row_index]
            ss_within += sum(
                (value - group_mean[dim_idx]) ** 2 for dim_idx, value in enumerate(row)
            )

    ss_between = max(0.0, ss_total - ss_within)
    df_between = k - 1
    df_within = n - k
    if df_between <= 0 or df_within <= 0:
        return None

    ms_between = ss_between / df_between
    ms_within = ss_within / df_within
    if ms_within <= 0:
        ms_within = 1e-12

    f_stat = ms_between / ms_within
    r_squared = ss_between / ss_total if ss_total > 0 else 0.0
    return {
        "f_stat": f_stat,
        "r_squared": r_squared,
        "df_between": df_between,
        "df_within": df_within,
        "ss_between": ss_between,
        "ss_within": ss_within,
    }


def _oneway_source_anova(
    values: list[float],
    source_labels: list[str],
) -> dict[str, Any] | None:
    if not values or len(values) != len(source_labels):
        return None

    by_source: dict[str, list[float]] = {}
    for value, label in zip(values, source_labels):
        by_source.setdefault(label, []).append(value)

    n = len(values)
    k = len(by_source)
    if k < 2 or n <= k:
        return None

    grand_mean = statistics.mean(values)
    source_means: dict[str, float] = {}
    ss_between = 0.0
    ss_within = 0.0
    for source, group_values in by_source.items():
        group_mean = statistics.mean(group_values)
        source_means[source] = group_mean
        ss_between += len(group_values) * (group_mean - grand_mean) ** 2
        ss_within += sum((value - group_mean) ** 2 for value in group_values)

    df_between = k - 1
    df_within = n - k
    if df_between <= 0 or df_within <= 0:
        return None

    ms_between = ss_between / df_between
    ms_within = ss_within / df_within
    if ms_within <= 0:
        ms_within = 1e-12
    f_stat = ms_between / ms_within

    ss_total = ss_between + ss_within
    eta_sq = ss_between / ss_total if ss_total > 0 else 0.0
    return {
        "f_stat": f_stat,
        "eta_sq": eta_sq,
        "df_between": df_between,
        "df_within": df_within,
        "n": n,
        "source_means": source_means,
    }


def _nearest_centroid_loocv(
    matrix: list[list[float]],
    source_labels: list[str],
) -> dict[str, Any] | None:
    if not matrix or len(matrix) != len(source_labels):
        return None
    dims = len(matrix[0])
    if dims == 0:
        return None
    if any(len(row) != dims for row in matrix):
        raise ValueError("All classification rows must have the same dimensionality.")

    n = len(matrix)
    unique_sources = sorted(set(source_labels))
    if len(unique_sources) < 2:
        return None

    correct = 0
    evaluated = 0
    for holdout_idx in range(n):
        sums: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        for idx, row in enumerate(matrix):
            if idx == holdout_idx:
                continue
            label = source_labels[idx]
            sums.setdefault(label, [0.0 for _ in range(dims)])
            counts[label] = counts.get(label, 0) + 1
            for dim_idx, value in enumerate(row):
                sums[label][dim_idx] += value

        centroids: dict[str, list[float]] = {}
        for label, vector_sums in sums.items():
            count = counts[label]
            if count <= 0:
                continue
            centroids[label] = [value / count for value in vector_sums]

        true_label = source_labels[holdout_idx]
        if true_label not in centroids:
            continue

        row = matrix[holdout_idx]
        best_label = ""
        best_dist = math.inf
        for label, centroid in centroids.items():
            dist = sum((value - centroid[dim_idx]) ** 2 for dim_idx, value in enumerate(row))
            if dist < best_dist:
                best_dist = dist
                best_label = label

        evaluated += 1
        if best_label == true_label:
            correct += 1

    if evaluated == 0:
        return None

    source_size = _source_counts(source_labels)
    baseline_accuracy = max(source_size.values()) / n if n else 0.0
    accuracy = correct / evaluated
    return {
        "accuracy": accuracy,
        "baseline_accuracy": baseline_accuracy,
        "evaluated": evaluated,
        "total": n,
    }


def _permutation_pvalue(
    observed: float | None,
    source_labels: list[str],
    permutations: int,
    seed: int,
    stat_fn,
) -> float | None:
    if observed is None or permutations <= 0:
        return None

    rng = random.Random(seed)
    permuted_labels = source_labels.copy()
    valid = 0
    extreme = 0
    for _ in range(permutations):
        rng.shuffle(permuted_labels)
        permuted_value = stat_fn(permuted_labels)
        if permuted_value is None:
            continue
        valid += 1
        if permuted_value >= observed - 1e-12:
            extreme += 1

    if valid == 0:
        return None
    return (extreme + 1) / (valid + 1)


def _compute_source_differentiation_stats(
    matrix: list[list[float]],
    lens_names: list[str],
    source_labels: list[str],
    permutations: int,
    random_seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_counts = _source_counts(source_labels)
    summary: dict[str, Any] = {
        "status": "unavailable",
        "reason": "",
        "n_articles": len(matrix),
        "n_lenses": len(lens_names),
        "n_sources": len(source_counts),
        "source_counts": source_counts,
        "permutations": permutations,
        "multivariate": None,
        "classification": None,
    }
    lens_anova_results: list[dict[str, Any]] = []

    if not matrix:
        summary["reason"] = "No complete article rows available for source tests."
        return summary, lens_anova_results
    if len(matrix) != len(source_labels):
        summary["reason"] = "Matrix/source label size mismatch."
        return summary, lens_anova_results
    if len(set(source_labels)) < 2:
        summary["reason"] = "Need at least 2 sources with complete article rows."
        return summary, lens_anova_results
    if not lens_names:
        summary["reason"] = "Need at least 1 lens dimension for source tests."
        return summary, lens_anova_results

    multivariate = _multivariate_source_separation(matrix, source_labels)
    if multivariate:
        observed_f = float(multivariate["f_stat"])
        multivariate["p_perm"] = _permutation_pvalue(
            observed=observed_f,
            source_labels=source_labels,
            permutations=permutations,
            seed=random_seed,
            stat_fn=lambda labels: (
                _multivariate_source_separation(matrix, labels) or {}
            ).get("f_stat"),
        )
        summary["multivariate"] = multivariate

    classification = _nearest_centroid_loocv(matrix, source_labels)
    if classification:
        observed_acc = _safe_float(classification.get("accuracy"))
        classification["p_perm"] = _permutation_pvalue(
            observed=observed_acc,
            source_labels=source_labels,
            permutations=permutations,
            seed=random_seed + 1,
            stat_fn=lambda labels: (_nearest_centroid_loocv(matrix, labels) or {}).get("accuracy"),
        )
        summary["classification"] = classification

    for lens_idx, lens_name in enumerate(lens_names):
        values = [row[lens_idx] for row in matrix]
        one_way = _oneway_source_anova(values, source_labels)
        if not one_way:
            continue
        observed_f = float(one_way["f_stat"])
        p_value = _permutation_pvalue(
            observed=observed_f,
            source_labels=source_labels,
            permutations=permutations,
            seed=random_seed + 10 + lens_idx,
            stat_fn=lambda labels: (_oneway_source_anova(values, labels) or {}).get("f_stat"),
        )
        result = {
            "lens_name": lens_name,
            "f_stat": observed_f,
            "eta_sq": float(one_way["eta_sq"]),
            "p_perm": p_value,
            "n": int(one_way["n"]),
            "df_between": int(one_way["df_between"]),
            "df_within": int(one_way["df_within"]),
            "source_means": one_way["source_means"],
        }
        lens_anova_results.append(result)

    lens_anova_results.sort(
        key=lambda row: (
            1.0 if row["p_perm"] is None else float(row["p_perm"]),
            -float(row["eta_sq"]),
        )
    )

    if summary["multivariate"] or summary["classification"] or lens_anova_results:
        summary["status"] = "ok"
        summary["reason"] = ""
    else:
        summary["reason"] = "Insufficient degrees of freedom for source tests."

    return summary, lens_anova_results


def _write_source_differentiation_outputs(
    output_dir: Path,
    summary: dict[str, Any],
    lens_anova_results: list[dict[str, Any]],
) -> None:
    summary_path = output_dir / "source_differentiation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    anova_path = output_dir / "source_lens_anova.csv"
    with anova_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "lens_name",
                "f_stat",
                "eta_sq",
                "p_perm",
                "n",
                "df_between",
                "df_within",
                "source_means",
            ]
        )
        for row in lens_anova_results:
            writer.writerow(
                [
                    row["lens_name"],
                    f"{float(row['f_stat']):.6f}",
                    f"{float(row['eta_sq']):.6f}",
                    "" if row["p_perm"] is None else f"{float(row['p_perm']):.6f}",
                    row["n"],
                    row["df_between"],
                    row["df_within"],
                    json.dumps(row["source_means"], ensure_ascii=False, sort_keys=True),
                ]
            )


def _pca(
    matrix: list[list[float]],
    standardize: bool,
) -> tuple[list[list[float]], list[float], list[list[float]]]:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "numpy is required for PCA. Install with `pip install numpy`."
        ) from exc

    X = np.array(matrix, dtype=float)
    if X.ndim != 2:
        raise ValueError("PCA input must be a 2D matrix.")

    # Center (and optionally scale) features
    means = X.mean(axis=0)
    X = X - means
    if standardize:
        stds = X.std(axis=0, ddof=1)
        stds[stds == 0] = 1.0
        X = X / stds

    # SVD for PCA
    U, S, Vt = np.linalg.svd(X, full_matrices=False)
    scores = U * S

    # Explained variance
    n_samples = X.shape[0]
    if n_samples > 1:
        explained_variance = (S**2) / (n_samples - 1)
        total_variance = explained_variance.sum()
        explained_ratio = (explained_variance / total_variance).tolist()
    else:
        explained_ratio = [0.0 for _ in range(Vt.shape[0])]

    components = Vt.tolist()
    return scores.tolist(), explained_ratio, components


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _shorten(text: str, max_len: int = 80) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _write_pca_csv_outputs(
    output_dir: Path,
    article_points: list[dict[str, Any]],
    lens_points: list[dict[str, Any]],
    explained_ratio: list[float],
) -> None:
    with (output_dir / "pca_articles.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["item_id", "source", "title", "pc1", "pc2"])
        for point in article_points:
            writer.writerow(
                [
                    point["id"],
                    point.get("source", ""),
                    point.get("title", ""),
                    f"{point['x']:.6f}",
                    f"{point['y']:.6f}",
                ]
            )

    with (output_dir / "pca_lenses.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["lens_name", "pc1_loading", "pc2_loading"])
        for point in lens_points:
            writer.writerow([point["name"], f"{point['x']:.6f}", f"{point['y']:.6f}"])

    with (output_dir / "pca_explained_variance.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["component", "explained_ratio"])
        for idx, ratio in enumerate(explained_ratio):
            writer.writerow([f"PC{idx + 1}", f"{ratio:.6f}"])


def _print_pca_summary(
    pca_articles: dict[str, Any] | None,
    pca_lenses: dict[str, Any] | None,
    explained_ratio: list[float] | None,
    item_meta: dict[str, dict[str, str]],
    pca_error: str | None,
) -> None:
    if pca_error:
        print(f"PCA unavailable: {pca_error}")
        return

    if not pca_articles or not pca_lenses or not explained_ratio:
        print("PCA unavailable: no PCA outputs were generated.")
        return

    article_points = pca_articles.get("points", [])
    lens_points = pca_lenses.get("points", [])
    if not isinstance(article_points, list) or not isinstance(lens_points, list):
        print("PCA unavailable: malformed PCA outputs.")
        return

    explained_text = ", ".join(
        f"PC{idx + 1}={ratio * 100:.1f}%"
        for idx, ratio in enumerate(explained_ratio[:5])
    )

    print("PCA results:")
    print(
        f"  article_points={len(article_points)}, lens_dimensions={len(lens_points)}, "
        f"explained_variance={explained_text}"
    )

    if lens_points:
        print("  lens loadings (sorted by |PC1|):")
        for point in sorted(lens_points, key=lambda p: abs(float(p.get("x", 0.0))), reverse=True):
            name = str(point.get("name", "(unnamed lens)"))
            x = float(point.get("x", 0.0))
            y = float(point.get("y", 0.0))
            print(f"    {name}: PC1={x:.3f}, PC2={y:.3f}")

    if article_points:
        max_pc1 = max(article_points, key=lambda p: float(p.get("x", 0.0)))
        min_pc1 = min(article_points, key=lambda p: float(p.get("x", 0.0)))
        max_pc2 = max(article_points, key=lambda p: float(p.get("y", 0.0)))
        min_pc2 = min(article_points, key=lambda p: float(p.get("y", 0.0)))

        def article_label(item_id: str) -> str:
            meta = item_meta.get(item_id, {})
            title = _shorten(str(meta.get("title", "")), max_len=70)
            source = str(meta.get("source", "")).strip()
            if title and source:
                return f"{item_id} | {source} | {title}"
            if title:
                return f"{item_id} | {title}"
            return item_id

        print("  article extremes:")
        print(
            f"    max PC1: {article_label(str(max_pc1.get('id', '')))} "
            f"({float(max_pc1.get('x', 0.0)):.3f})"
        )
        print(
            f"    min PC1: {article_label(str(min_pc1.get('id', '')))} "
            f"({float(min_pc1.get('x', 0.0)):.3f})"
        )
        print(
            f"    max PC2: {article_label(str(max_pc2.get('id', '')))} "
            f"({float(max_pc2.get('y', 0.0)):.3f})"
        )
        print(
            f"    min PC2: {article_label(str(min_pc2.get('id', '')))} "
            f"({float(min_pc2.get('y', 0.0)):.3f})"
        )


def _print_source_differentiation_summary(
    summary: dict[str, Any],
    lens_anova_results: list[dict[str, Any]],
) -> None:
    status = str(summary.get("status", "unavailable"))
    if status != "ok":
        reason = str(summary.get("reason", "")).strip() or "unavailable"
        print(f"Source differentiation unavailable: {reason}")
        return

    n_articles = int(summary.get("n_articles", 0))
    n_sources = int(summary.get("n_sources", 0))
    permutations = int(summary.get("permutations", 0))
    print(
        "Source differentiation:"
        f" articles={n_articles}, sources={n_sources}, permutations={permutations}"
    )

    multivariate = summary.get("multivariate")
    if isinstance(multivariate, dict):
        f_stat = _safe_float(multivariate.get("f_stat"))
        r2 = _safe_float(multivariate.get("r_squared"))
        p_perm = _safe_float(multivariate.get("p_perm"))
        line = "  multivariate permutation test:"
        line += f" F={f_stat:.3f}" if f_stat is not None else " F=n/a"
        if r2 is not None:
            line += f", R^2={r2:.3f}"
        if p_perm is not None:
            line += f", p_perm={p_perm:.4f}"
        print(line)

    classification = summary.get("classification")
    if isinstance(classification, dict):
        accuracy = _safe_float(classification.get("accuracy"))
        baseline = _safe_float(classification.get("baseline_accuracy"))
        p_perm = _safe_float(classification.get("p_perm"))
        evaluated = int(classification.get("evaluated", 0))
        total = int(classification.get("total", 0))
        line = "  leave-one-out nearest-centroid:"
        line += f" accuracy={accuracy:.3f}" if accuracy is not None else " accuracy=n/a"
        if baseline is not None:
            line += f", baseline={baseline:.3f}"
        line += f", evaluated={evaluated}/{total}"
        if p_perm is not None:
            line += f", p_perm={p_perm:.4f}"
        print(line)

    if lens_anova_results:
        print("  strongest lens-level separators (by permutation p-value):")
        for row in lens_anova_results[:5]:
            lens_name = str(row.get("lens_name", "(unnamed lens)"))
            eta_sq = _safe_float(row.get("eta_sq")) or 0.0
            p_perm = _safe_float(row.get("p_perm"))
            if p_perm is None:
                print(f"    {lens_name}: eta^2={eta_sq:.3f}, p_perm=n/a")
            else:
                print(f"    {lens_name}: eta^2={eta_sq:.3f}, p_perm={p_perm:.4f}")


def _write_html_report(
    path: Path,
    lens_names: list[str],
    item_ids: list[str],
    item_meta: dict[str, dict[str, str]],
    corr_norm: list[list[float | None]],
    corr_norm_counts: list[list[int]],
    cov_norm: list[list[float | None]],
    cov_norm_counts: list[list[int]],
    corr_raw: list[list[float | None]],
    corr_raw_counts: list[list[int]],
    cov_raw: list[list[float | None]],
    cov_raw_counts: list[list[int]],
    pca_articles: dict[str, Any] | None,
    pca_lenses: dict[str, Any] | None,
    explained_ratio: list[float] | None,
    pca_error: str | None,
    source_stats_summary: dict[str, Any] | None,
    source_lens_anova: list[dict[str, Any]] | None,
    title: str,
) -> None:
    corr_norm_z = [[None if v is None else float(v) for v in row] for row in corr_norm]
    cov_norm_z = [[None if v is None else float(v) for v in row] for row in cov_norm]
    corr_raw_z = [[None if v is None else float(v) for v in row] for row in corr_raw]
    cov_raw_z = [[None if v is None else float(v) for v in row] for row in cov_raw]

    article_points = []
    if pca_articles:
        for entry in pca_articles.get("points", []):
            meta = item_meta.get(entry["id"], {})
            source = str(meta.get("source", "")).strip() or "Unknown Source"
            article_points.append(
                {
                    "id": entry["id"],
                    "x": entry["x"],
                    "y": entry["y"],
                    "title": meta.get("title", ""),
                    "source": source,
                }
            )

    lens_points = []
    if pca_lenses:
        for entry in pca_lenses.get("points", []):
            lens_points.append(
                {"name": entry["name"], "x": entry["x"], "y": entry["y"]}
            )

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>{title}</title>
  <script src=\"https://cdn.plot.ly/plotly-2.27.0.min.js\"></script>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    h1 {{ margin-bottom: 8px; }}
    .grid {{ display: grid; gap: 24px; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }}
    .chart {{ border: 1px solid #e0e0e0; padding: 12px; border-radius: 8px; }}
    .note {{ color: #555; font-size: 12px; margin-top: 6px; }}
    .summary-table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    .summary-table th, .summary-table td {{ border-bottom: 1px solid #eee; padding: 6px; text-align: left; }}
    .summary-table th {{ width: 48%; color: #333; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class=\"grid\">
    <div class=\"chart\">
      <div id=\"corr-heatmap-norm\" style=\"height:420px;\"></div>
      <div class=\"note\">Lens correlation (normalized). Hover for sample counts.</div>
    </div>
    <div class=\"chart\">
      <div id=\"cov-heatmap-norm\" style=\"height:420px;\"></div>
      <div class=\"note\">Lens covariance (normalized). Hover for sample counts.</div>
    </div>
    <div class=\"chart\">
      <div id=\"corr-heatmap-raw\" style=\"height:420px;\"></div>
      <div class=\"note\">Lens correlation (raw scores). Hover for sample counts.</div>
    </div>
    <div class=\"chart\">
      <div id=\"cov-heatmap-raw\" style=\"height:420px;\"></div>
      <div class=\"note\">Lens covariance (raw scores). Hover for sample counts.</div>
    </div>
    <div class=\"chart\">
      <div id=\"pca-articles\" style=\"height:420px;\"></div>
      <div class=\"note\">PCA of articles in lens space.</div>
    </div>
    <div class=\"chart\">
      <div id=\"pca-lenses\" style=\"height:420px;\"></div>
      <div class=\"note\">PCA loadings for lenses.</div>
    </div>
    <div class=\"chart\">
      <div id=\"pca-variance\" style=\"height:360px;\"></div>
      <div class=\"note\">Explained variance by principal component.</div>
    </div>
    <div class=\"chart\">
      <div id=\"source-summary\" style=\"min-height:240px;\"></div>
      <div class=\"note\">Permutation-based tests for source separation in lens space.</div>
    </div>
    <div class=\"chart\">
      <div id=\"source-lens-anova\" style=\"height:420px;\"></div>
      <div class=\"note\">Lens-level source effect sizes (eta^2), sorted by permutation p-value.</div>
    </div>
  </div>

<script>
  const lensNames = {_safe_json(lens_names)};
  const corrNormZ = {_safe_json(corr_norm_z)};
  const corrNormCounts = {_safe_json(corr_norm_counts)};
  const covNormZ = {_safe_json(cov_norm_z)};
  const covNormCounts = {_safe_json(cov_norm_counts)};
  const corrRawZ = {_safe_json(corr_raw_z)};
  const corrRawCounts = {_safe_json(corr_raw_counts)};
  const covRawZ = {_safe_json(cov_raw_z)};
  const covRawCounts = {_safe_json(cov_raw_counts)};
  const articlePoints = {_safe_json(article_points)};
  const lensPoints = {_safe_json(lens_points)};
  const explained = {_safe_json(explained_ratio or [])};
  const pcaError = {_safe_json(pca_error or "")};
  const sourceStats = {_safe_json(source_stats_summary or {})};
  const sourceLensAnova = {_safe_json(source_lens_anova or [])};

  const corrNormTrace = {{
    z: corrNormZ,
    x: lensNames,
    y: lensNames,
    type: 'heatmap',
    colorscale: 'RdBu',
    zmin: -1,
    zmax: 1,
    text: corrNormCounts,
    hovertemplate: 'Lens A: %{{x}}<br>Lens B: %{{y}}<br>Corr: %{{z:.3f}}<br>n=%{{text}}<extra></extra>'
  }};
  Plotly.newPlot('corr-heatmap-norm', [corrNormTrace], {{margin: {{t: 20}} }});

  const covNormTrace = {{
    z: covNormZ,
    x: lensNames,
    y: lensNames,
    type: 'heatmap',
    colorscale: 'RdBu',
    zmid: 0,
    text: covNormCounts,
    hovertemplate: 'Lens A: %{{x}}<br>Lens B: %{{y}}<br>Cov: %{{z:.3f}}<br>n=%{{text}}<extra></extra>'
  }};
  Plotly.newPlot('cov-heatmap-norm', [covNormTrace], {{margin: {{t: 20}} }});

  const corrRawTrace = {{
    z: corrRawZ,
    x: lensNames,
    y: lensNames,
    type: 'heatmap',
    colorscale: 'RdBu',
    zmin: -1,
    zmax: 1,
    text: corrRawCounts,
    hovertemplate: 'Lens A: %{{x}}<br>Lens B: %{{y}}<br>Corr: %{{z:.3f}}<br>n=%{{text}}<extra></extra>'
  }};
  Plotly.newPlot('corr-heatmap-raw', [corrRawTrace], {{margin: {{t: 20}} }});

  const covRawTrace = {{
    z: covRawZ,
    x: lensNames,
    y: lensNames,
    type: 'heatmap',
    colorscale: 'RdBu',
    zmid: 0,
    text: covRawCounts,
    hovertemplate: 'Lens A: %{{x}}<br>Lens B: %{{y}}<br>Cov: %{{z:.3f}}<br>n=%{{text}}<extra></extra>'
  }};
  Plotly.newPlot('cov-heatmap-raw', [covRawTrace], {{margin: {{t: 20}} }});

  if (pcaError) {{
    document.getElementById('pca-articles').innerText = pcaError;
  }} else if (articlePoints.length) {{
    const colorPalette = [
      '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
    ];
    const sourceOrder = [...new Set(articlePoints.map(p => p.source || 'Unknown Source'))].sort();
    const articleTraces = sourceOrder.map((sourceName, idx) => {{
      const sourcePoints = articlePoints.filter(p => (p.source || 'Unknown Source') === sourceName);
      return {{
        name: sourceName,
        x: sourcePoints.map(p => p.x),
        y: sourcePoints.map(p => p.y),
        mode: 'markers+text',
        type: 'scatter',
        text: sourcePoints.map(p => p.id),
        textposition: 'top center',
        textfont: {{size: 10}},
        hovertemplate: 'ID: %{{customdata[0]}}<br>Source: %{{customdata[1]}}<br>Title: %{{customdata[2]}}<extra></extra>',
        customdata: sourcePoints.map(p => [p.id, p.source, p.title || '(untitled)']),
        marker: {{
          size: 10,
          color: colorPalette[idx % colorPalette.length],
          line: {{width: 1, color: '#ffffff'}}
        }}
      }};
    }});

    Plotly.newPlot('pca-articles', articleTraces, {{
      margin: {{t: 20}},
      xaxis: {{title: 'PC1'}},
      yaxis: {{title: 'PC2'}},
      legend: {{orientation: 'h', y: -0.2}},
    }});
  }} else {{
    document.getElementById('pca-articles').innerText = 'PCA not available (insufficient complete rows).';
  }}

  if (pcaError) {{
    document.getElementById('pca-lenses').innerText = pcaError;
  }} else if (lensPoints.length) {{
    const lensTrace = {{
      x: lensPoints.map(p => p.x),
      y: lensPoints.map(p => p.y),
      mode: 'markers+text',
      type: 'scatter',
      text: lensPoints.map(p => p.name),
      textposition: 'top center',
      marker: {{ size: 12, color: '#d1495b' }}
    }};
    Plotly.newPlot('pca-lenses', [lensTrace], {{
      margin: {{t: 20}},
      xaxis: {{title: 'PC1 loading'}},
      yaxis: {{title: 'PC2 loading'}},
    }});
  }} else {{
    document.getElementById('pca-lenses').innerText = 'PCA not available (insufficient lens dimensions).';
  }}

  if (pcaError) {{
    document.getElementById('pca-variance').innerText = pcaError;
  }} else if (explained.length) {{
    const x = explained.map((_, i) => `PC${{i + 1}}`);
    const y = explained.map(v => v * 100);
    const trace = {{ type: 'bar', x, y, marker: {{ color: '#7a8f30' }} }};
    Plotly.newPlot('pca-variance', [trace], {{
      margin: {{t: 20}},
      yaxis: {{title: 'Explained variance (%)'}}
    }});
  }} else {{
    document.getElementById('pca-variance').innerText = 'Explained variance unavailable.';
  }}

  const fmt = (value, digits=3) => Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
  const sourceSummaryEl = document.getElementById('source-summary');
  if (!sourceStats || sourceStats.status !== 'ok') {{
    const reason = (sourceStats && sourceStats.reason) ? sourceStats.reason : 'Source differentiation unavailable.';
    sourceSummaryEl.innerText = reason;
  }} else {{
    const sourceCounts = sourceStats.source_counts || {{}};
    const sourceCountText = Object.keys(sourceCounts)
      .sort()
      .map(name => `${{name}} (${{sourceCounts[name]}})`)
      .join(', ');
    const multivariate = sourceStats.multivariate || {{}};
    const classification = sourceStats.classification || {{}};
    sourceSummaryEl.innerHTML = `
      <table class="summary-table">
        <tr><th>Complete Articles</th><td>${{sourceStats.n_articles ?? 'n/a'}}</td></tr>
        <tr><th>Sources</th><td>${{sourceStats.n_sources ?? 'n/a'}}</td></tr>
        <tr><th>Source Counts</th><td>${{sourceCountText || 'n/a'}}</td></tr>
        <tr><th>Multivariate F</th><td>${{fmt(multivariate.f_stat)}}</td></tr>
        <tr><th>Multivariate R^2</th><td>${{fmt(multivariate.r_squared)}}</td></tr>
        <tr><th>Multivariate p (perm)</th><td>${{fmt(multivariate.p_perm, 4)}}</td></tr>
        <tr><th>LOO Centroid Accuracy</th><td>${{fmt(classification.accuracy)}}</td></tr>
        <tr><th>Majority Baseline</th><td>${{fmt(classification.baseline_accuracy)}}</td></tr>
        <tr><th>Accuracy p (perm)</th><td>${{fmt(classification.p_perm, 4)}}</td></tr>
        <tr><th>Permutations</th><td>${{sourceStats.permutations ?? 'n/a'}}</td></tr>
      </table>
    `;
  }}

  if (sourceLensAnova.length) {{
    const ranked = [...sourceLensAnova].sort((a, b) => {{
      const ap = Number.isFinite(a.p_perm) ? a.p_perm : 1;
      const bp = Number.isFinite(b.p_perm) ? b.p_perm : 1;
      return ap - bp;
    }});
    const top = ranked.slice(0, Math.min(15, ranked.length));
    const trace = {{
      type: 'bar',
      x: top.map(row => row.lens_name),
      y: top.map(row => row.eta_sq),
      customdata: top.map(row => [row.f_stat, row.p_perm, row.n]),
      text: top.map(row => `p=${{fmt(row.p_perm, 4)}}`),
      textposition: 'outside',
      marker: {{ color: '#4c956c' }},
      hovertemplate: 'Lens: %{{x}}<br>eta²: %{{y:.3f}}<br>F: %{{customdata[0]:.3f}}<br>p_perm: %{{customdata[1]:.4f}}<br>n=%{{customdata[2]}}<extra></extra>'
    }};
    Plotly.newPlot('source-lens-anova', [trace], {{
      margin: {{t: 20, b: 170}},
      xaxis: {{tickangle: -35}},
      yaxis: {{title: 'eta²'}},
    }});
  }} else {{
    document.getElementById('source-lens-anova').innerText = 'Lens-level source tests unavailable.';
  }}
</script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build lens/article matrices, correlations, and PCA charts in a single report."
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
        help="Directory for analysis outputs.",
    )
    parser.add_argument(
        "--rubric-aggregation",
        default="latest",
        choices=["latest", "mean", "median"],
        help="How to collapse multiple rubric scores per item.",
    )
    parser.add_argument(
        "--pca-input",
        default="normalized",
        choices=["normalized", "raw"],
        help="Use normalized or raw lens totals for PCA.",
    )
    parser.add_argument(
        "--no-pca-standardize",
        action="store_true",
        help="Disable z-score standardization before PCA.",
    )
    parser.add_argument(
        "--source-permutations",
        type=int,
        default=1000,
        help="Permutation count for source differentiation tests.",
    )
    parser.add_argument(
        "--source-random-seed",
        type=int,
        default=42,
        help="Random seed for source differentiation permutations.",
    )
    args = parser.parse_args()

    scores_path = Path(args.scores)
    lenses_path = Path(args.lenses)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    lens_defs, rubric_to_lens = load_lenses(lenses_path)
    scores = load_scores(scores_path, rubric_to_lens)
    if not scores:
        raise SystemExit("No usable scores found. Check scores.json and lenses path.")

    lens_totals, item_meta = build_lens_totals(scores, lens_defs, args.rubric_aggregation)
    lens_names = [lens.name for lens in lens_defs]
    item_ids = sorted(item_meta.keys())

    normalized = normalize_values(lens_defs, lens_totals)

    # Output matrices
    _write_matrix_long_csv(output_dir / "lens_article_matrix.csv", lens_names, item_ids, lens_totals)
    _write_matrix_long_csv(
        output_dir / "lens_article_matrix_normalized.csv",
        lens_names,
        item_ids,
        normalized,
    )
    _write_article_metadata(output_dir / "article_metadata.csv", item_meta, item_ids)

    # Correlation/covariance
    corr_raw, corr_counts = _pairwise_matrix(lens_names, lens_totals, item_ids, _correlation)
    cov_raw, cov_counts = _pairwise_matrix(lens_names, lens_totals, item_ids, _covariance)
    corr_norm, corr_norm_counts = _pairwise_matrix(lens_names, normalized, item_ids, _correlation)
    cov_norm, cov_norm_counts = _pairwise_matrix(lens_names, normalized, item_ids, _covariance)

    _write_matrix_csv(output_dir / "lens_correlation_raw.csv", lens_names, corr_raw)
    _write_matrix_csv(output_dir / "lens_covariance_raw.csv", lens_names, cov_raw)
    _write_counts_csv(output_dir / "lens_pairwise_counts_raw.csv", lens_names, corr_counts)

    _write_matrix_csv(output_dir / "lens_correlation_normalized.csv", lens_names, corr_norm)
    _write_matrix_csv(output_dir / "lens_covariance_normalized.csv", lens_names, cov_norm)
    _write_counts_csv(
        output_dir / "lens_pairwise_counts_normalized.csv", lens_names, corr_norm_counts
    )

    # PCA on articles in lens space
    pca_matrix = normalized if args.pca_input == "normalized" else lens_totals
    complete_article_rows: list[list[float]] = []
    complete_article_ids: list[str] = []
    for item_id in item_ids:
        row = []
        missing = False
        for lens in lens_names:
            value = pca_matrix[lens].get(item_id)
            if value is None:
                missing = True
                break
            row.append(value)
        if not missing:
            complete_article_rows.append(row)
            complete_article_ids.append(item_id)

    complete_sources = [
        str(item_meta.get(item_id, {}).get("source", "")).strip() or "Unknown Source"
        for item_id in complete_article_ids
    ]
    source_stats_summary, source_lens_anova = _compute_source_differentiation_stats(
        matrix=complete_article_rows,
        lens_names=lens_names,
        source_labels=complete_sources,
        permutations=max(0, int(args.source_permutations)),
        random_seed=int(args.source_random_seed),
    )
    _write_source_differentiation_outputs(
        output_dir=output_dir,
        summary=source_stats_summary,
        lens_anova_results=source_lens_anova,
    )

    pca_articles: dict[str, Any] | None = None
    pca_lenses: dict[str, Any] | None = None
    explained_ratio: list[float] | None = None
    pca_error: str | None = None

    if len(complete_article_rows) < 2 or len(lens_names) < 2:
        pca_error = (
            "need at least 2 complete articles and 2 lenses "
            f"(got complete_articles={len(complete_article_rows)}, lenses={len(lens_names)})"
        )
    else:
        try:
            scores_2d, explained_ratio, components = _pca(
                complete_article_rows,
                standardize=not args.no_pca_standardize,
            )
        except RuntimeError as exc:
            pca_error = str(exc)
        else:
            article_points = []
            for idx, item_id in enumerate(complete_article_ids):
                meta = item_meta.get(item_id, {})
                article_points.append(
                    {
                        "id": item_id,
                        "x": scores_2d[idx][0],
                        "y": scores_2d[idx][1] if len(scores_2d[idx]) > 1 else 0.0,
                        "title": str(meta.get("title", "")),
                        "source": str(meta.get("source", "")).strip() or "Unknown Source",
                    }
                )
            pca_articles = {"points": article_points}

            lens_points = []
            for lens_idx, lens_name in enumerate(lens_names):
                lens_points.append(
                    {
                        "name": lens_name,
                        "x": components[0][lens_idx],
                        "y": components[1][lens_idx] if len(components) > 1 else 0.0,
                    }
                )
            pca_lenses = {"points": lens_points}

            _write_pca_csv_outputs(
                output_dir=output_dir,
                article_points=article_points,
                lens_points=lens_points,
                explained_ratio=explained_ratio,
            )

    # HTML report (use normalized matrices for display)
    _write_html_report(
        output_dir / "analysis_report.html",
        lens_names,
        item_ids,
        item_meta,
        corr_norm,
        corr_norm_counts,
        cov_norm,
        cov_norm_counts,
        corr_raw,
        corr_counts,
        cov_raw,
        cov_counts,
        pca_articles,
        pca_lenses,
        explained_ratio,
        pca_error,
        source_stats_summary,
        source_lens_anova,
        title="Lens & Article Analysis Report",
    )

    _print_pca_summary(
        pca_articles=pca_articles,
        pca_lenses=pca_lenses,
        explained_ratio=explained_ratio,
        item_meta=item_meta,
        pca_error=pca_error,
    )
    _print_source_differentiation_summary(
        summary=source_stats_summary,
        lens_anova_results=source_lens_anova,
    )

    print(f"Wrote analysis outputs to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
