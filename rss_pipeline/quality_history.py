from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifact_store import read_json, write_json
from .normalization import compact_whitespace

KEY_TREND_METRICS = (
    "issue_item_count",
    "unknown_content_type_items",
    "unsupported_content_type_items",
    "accepted_content_type_filter_items",
    "source_blocked_items",
    "accepted_rss_only_fallback_items",
)


def archive_quality_review(
    review: dict[str, Any],
    *,
    output_path: Path,
    history_dir: Path,
) -> Path:
    generated_at = compact_whitespace(review.get("generated_at"))
    date_stamp = generated_at[:10] if len(generated_at) >= 10 else "unknown-date"
    archive_path = history_dir / f"{output_path.stem}_{date_stamp}.json"
    write_json(archive_path, review)
    return archive_path


def load_quality_review_artifacts(
    *,
    history_dir: Path,
    current_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    artifacts: list[dict[str, Any]] = []
    load_errors: list[dict[str, str]] = []
    candidates = sorted(history_dir.glob("rss_digest_quality_review_*.json"))
    if current_path is not None and current_path.exists():
        candidates.append(current_path)

    for path in candidates:
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            load_errors.append({"path": str(path), "error": str(exc)})
            continue
        if not isinstance(payload, dict):
            load_errors.append({"path": str(path), "error": "review JSON must be an object"})
            continue
        artifacts.append({"path": str(path), "review": payload})

    return artifacts, load_errors


def build_quality_history_report(
    artifacts: list[dict[str, Any]],
    *,
    load_errors: list[dict[str, str]] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    snapshots_by_identity: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    for artifact in artifacts:
        review = artifact.get("review")
        if not isinstance(review, dict):
            continue
        snapshot = _snapshot_from_review(review, path=compact_whitespace(artifact.get("path")))
        identity = (
            snapshot["generated_at"],
            snapshot["run_id"],
            snapshot["metrics"]["total_items"],
            snapshot["metrics"]["issue_item_count"],
        )
        snapshots_by_identity[identity] = snapshot

    snapshots = sorted(snapshots_by_identity.values(), key=_snapshot_sort_key)
    recent_snapshots = snapshots[-limit:]
    latest = snapshots[-1] if snapshots else None
    previous = snapshots[-2] if len(snapshots) > 1 else None
    metric_deltas = _metric_deltas(latest, previous)
    issue_count_deltas = _issue_count_deltas(latest, previous, limit=limit)

    return {
        "status": _history_status(latest, load_errors or []),
        "trend": _trend_label(metric_deltas, snapshots),
        "snapshot_count": len(snapshots),
        "latest": latest,
        "previous": previous,
        "metric_deltas": metric_deltas,
        "issue_count_deltas": issue_count_deltas,
        "recent_snapshots": recent_snapshots,
        "worst_snapshots": _worst_snapshots(snapshots, limit=limit),
        "load_errors": load_errors or [],
    }


def _snapshot_from_review(review: dict[str, Any], *, path: str) -> dict[str, Any]:
    issue_counts = _row_counts(review.get("issue_counts"), "issue")
    metrics = _snapshot_metrics(review)
    return {
        "path": path,
        "generated_at": compact_whitespace(review.get("generated_at")),
        "run_id": compact_whitespace(review.get("run_id")),
        "status": compact_whitespace(review.get("status")) or "unknown",
        "metrics": metrics,
        "issue_item_rate": _rate(metrics["issue_item_count"], metrics["total_items"]),
        "issue_counts": issue_counts,
        "top_issues": _top_count_rows(issue_counts),
    }


def _snapshot_metrics(review: dict[str, Any]) -> dict[str, int]:
    status_counts = review.get("status_counts")
    status_values = status_counts if isinstance(status_counts, dict) else {}
    gate_metrics = review.get("quality_gate_metrics")
    gate_values = gate_metrics if isinstance(gate_metrics, dict) else {}
    return {
        "total_items": _safe_int(review.get("total_items")),
        "issue_item_count": _safe_int(review.get("issue_item_count")),
        "clean_item_count": _safe_int(status_values.get("clean")),
        "warn_item_count": _safe_int(status_values.get("warn")),
        "fail_item_count": _safe_int(status_values.get("fail")),
        "unknown_content_type_items": _safe_int(gate_values.get("unknown_content_type_items")),
        "unsupported_content_type_items": _safe_int(
            gate_values.get("unsupported_content_type_items")
        ),
        "accepted_content_type_filter_items": _safe_int(
            gate_values.get("accepted_content_type_filter_items")
        ),
        "source_blocked_items": _safe_int(gate_values.get("source_blocked_items")),
        "accepted_rss_only_fallback_items": _safe_int(
            gate_values.get("accepted_rss_only_fallback_items")
        ),
    }


def _metric_deltas(
    latest: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> dict[str, dict[str, int]]:
    if latest is None or previous is None:
        return {}
    latest_raw_metrics = latest.get("metrics")
    previous_raw_metrics = previous.get("metrics")
    latest_metrics: dict[str, Any] = (
        latest_raw_metrics if isinstance(latest_raw_metrics, dict) else {}
    )
    previous_metrics: dict[str, Any] = (
        previous_raw_metrics if isinstance(previous_raw_metrics, dict) else {}
    )
    metrics = sorted(set(latest_metrics) | set(previous_metrics))
    return {
        metric: {
            "previous": _safe_int(previous_metrics.get(metric)),
            "latest": _safe_int(latest_metrics.get(metric)),
            "delta": _safe_int(latest_metrics.get(metric))
            - _safe_int(previous_metrics.get(metric)),
        }
        for metric in metrics
    }


def _issue_count_deltas(
    latest: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    *,
    limit: int,
) -> list[dict[str, int | str]]:
    if latest is None or previous is None:
        return []
    latest_counts = _dict_ints(latest.get("issue_counts"))
    previous_counts = _dict_ints(previous.get("issue_counts"))
    rows: list[dict[str, int | str]] = []
    for issue in sorted(set(latest_counts) | set(previous_counts)):
        previous_count = previous_counts.get(issue, 0)
        latest_count = latest_counts.get(issue, 0)
        delta = latest_count - previous_count
        if delta == 0:
            continue
        rows.append(
            {
                "issue": issue,
                "previous": previous_count,
                "latest": latest_count,
                "delta": delta,
            }
        )
    rows.sort(key=lambda row: (abs(int(row["delta"])), str(row["issue"])), reverse=True)
    return rows[:limit]


def _trend_label(
    metric_deltas: dict[str, dict[str, int]],
    snapshots: list[dict[str, Any]],
) -> str:
    if len(snapshots) < 2:
        return "insufficient_history"
    watched_deltas = [metric_deltas.get(metric, {}).get("delta", 0) for metric in KEY_TREND_METRICS]
    if any(delta > 0 for delta in watched_deltas):
        return "worse"
    if any(delta < 0 for delta in watched_deltas):
        return "improved"
    return "unchanged"


def _history_status(latest: dict[str, Any] | None, load_errors: list[dict[str, str]]) -> str:
    if latest is None or load_errors:
        return "fail"
    return compact_whitespace(latest.get("status")) or "unknown"


def _worst_snapshots(snapshots: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return sorted(
        snapshots,
        key=lambda snapshot: (
            snapshot["metrics"]["issue_item_count"],
            snapshot["metrics"]["source_blocked_items"],
            snapshot["generated_at"],
        ),
        reverse=True,
    )[:limit]


def _snapshot_sort_key(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    return (
        compact_whitespace(snapshot.get("generated_at")),
        compact_whitespace(snapshot.get("run_id")),
        compact_whitespace(snapshot.get("path")),
    )


def _row_counts(value: Any, key_name: str) -> dict[str, int]:
    if not isinstance(value, list):
        return {}
    counts: dict[str, int] = {}
    for row in value:
        if not isinstance(row, dict):
            continue
        key = compact_whitespace(row.get(key_name))
        if not key:
            continue
        counts[key] = _safe_int(row.get("count"))
    return counts


def _top_count_rows(counts: dict[str, int], *, limit: int = 5) -> list[dict[str, Any]]:
    return [
        {"issue": issue, "count": count}
        for issue, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _dict_ints(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _safe_int(count) for key, count in value.items() if compact_whitespace(key)}


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
