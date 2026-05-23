from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import read_json, write_json
from .normalization import compact_whitespace

SOURCE_HEALTH_STATUSES = ("healthy", "watch", "hold_candidate", "unknown")
SOURCE_HEALTH_STATUS_RANK = {
    "healthy": 0,
    "watch": 1,
    "hold_candidate": 2,
    "unknown": 0,
}


def load_feed_audit_artifacts(
    *,
    history_dir: Path,
    current_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    artifacts: list[dict[str, Any]] = []
    load_errors: list[dict[str, str]] = []
    candidates = sorted(history_dir.glob("rss_feed_audit_*.json"))
    if current_path is not None and current_path.exists():
        candidates.append(current_path)

    for path in candidates:
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            load_errors.append({"path": str(path), "error": str(exc)})
            continue
        if not isinstance(payload, dict):
            load_errors.append({"path": str(path), "error": "feed-audit JSON must be an object"})
            continue
        artifacts.append({"path": str(path), "audit": payload})

    return artifacts, load_errors


def build_source_health_trend_report(
    artifacts: list[dict[str, Any]],
    *,
    load_errors: list[dict[str, str]] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    snapshots_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for artifact in artifacts:
        audit = artifact.get("audit")
        if not isinstance(audit, dict):
            continue
        snapshot = _snapshot_from_feed_audit(
            audit,
            path=compact_whitespace(artifact.get("path")),
        )
        identity = (
            compact_whitespace(snapshot.get("generated_at")),
            compact_whitespace(snapshot.get("run_id")) or compact_whitespace(snapshot.get("path")),
        )
        snapshots_by_identity[identity] = snapshot

    snapshots = sorted(snapshots_by_identity.values(), key=_snapshot_sort_key)
    source_snapshots = [snapshot for snapshot in snapshots if snapshot["source_health_available"]]
    source_rows = _source_trend_rows(source_snapshots)
    source_rows.sort(key=_source_trend_sort_key)

    latest = source_snapshots[-1] if source_snapshots else snapshots[-1] if snapshots else None
    latest_status_counts = _latest_status_counts(source_rows)
    degraded_sources = [row for row in source_rows if row["trend"] == "worse"]
    improved_sources = [row for row in source_rows if row["trend"] == "improved"]
    hold_candidates = [row for row in source_rows if row["latest_status"] == "hold_candidate"]
    watch_sources = [row for row in source_rows if row["latest_status"] == "watch"]
    attention_sources = [
        row
        for row in source_rows
        if row["latest_status"] != "healthy"
        or row["trend"] == "worse"
        or _safe_int(row.get("latest_issue_count")) > 0
    ]

    return {
        "schema_version": "1.0",
        "generated_at": _now_utc(),
        "status": "fail" if load_errors else "pass",
        "snapshot_count": len(snapshots),
        "source_health_snapshot_count": len(source_snapshots),
        "source_count": len(source_rows),
        "latest": latest,
        "latest_status_counts": {
            status: latest_status_counts.get(status, 0) for status in SOURCE_HEALTH_STATUSES
        },
        "attention_summary": {
            "hold_candidates": len(hold_candidates),
            "watch_sources": len(watch_sources),
            "degraded_sources": len(degraded_sources),
            "improved_sources": len(improved_sources),
        },
        "sources_needing_attention": attention_sources[:limit],
        "hold_candidates": hold_candidates[:limit],
        "degraded_sources": degraded_sources[:limit],
        "improved_sources": improved_sources[:limit],
        "sources": source_rows,
        "load_errors": load_errors or [],
    }


def write_source_health_trend_report(output_path: Path, report: dict[str, Any]) -> None:
    write_json(output_path, report)


def _snapshot_from_feed_audit(audit: dict[str, Any], *, path: str) -> dict[str, Any]:
    run = audit.get("run")
    run_values = run if isinstance(run, dict) else {}
    generated_at = compact_whitespace(run_values.get("generated_at")) or compact_whitespace(
        audit.get("generated_at")
    )
    run_id = compact_whitespace(run_values.get("id"))
    source_health_raw = audit.get("source_health")
    source_health = source_health_raw if isinstance(source_health_raw, list) else []
    return {
        "path": path,
        "generated_at": generated_at,
        "run_id": run_id,
        "status": compact_whitespace(audit.get("status")) or "unknown",
        "source_health_available": bool(source_health),
        "source_health": [_normalize_source_health_row(row) for row in source_health],
    }


def _normalize_source_health_row(row: Any) -> dict[str, Any]:
    row_values = row if isinstance(row, dict) else {}
    source_id = compact_whitespace(row_values.get("source_id"))
    source_name = compact_whitespace(row_values.get("source_name")) or source_id or "unknown"
    status = compact_whitespace(row_values.get("status")) or "unknown"
    if status not in SOURCE_HEALTH_STATUS_RANK:
        status = "unknown"
    return {
        "source_id": source_id or source_name,
        "source_name": source_name,
        "selected_feeds": _safe_int(row_values.get("selected_feeds")),
        "feed_fetch_failed": _safe_int(row_values.get("feed_fetch_failed")),
        "raw_items": _safe_int(row_values.get("raw_items")),
        "typical_newsfeed_items": _safe_int(row_values.get("typical_newsfeed_items")),
        "newsfeed_excluded": _safe_int(row_values.get("newsfeed_excluded")),
        "quality_warn_items": _safe_int(row_values.get("quality_warn_items")),
        "quality_fail_items": _safe_int(row_values.get("quality_fail_items")),
        "info_issue_count": _safe_int(row_values.get("info_issue_count")),
        "warn_issue_count": _safe_int(row_values.get("warn_issue_count")),
        "fail_issue_count": _safe_int(row_values.get("fail_issue_count")),
        "missing_rss_content_items": _safe_int(row_values.get("missing_rss_content_items")),
        "accepted_content_type_filter_items": _safe_int(
            row_values.get("accepted_content_type_filter_items")
        ),
        "issue_count": _safe_int(row_values.get("issue_count")),
        "issue_rate": _safe_float(row_values.get("issue_rate")),
        "status": status,
        "recommended_action": compact_whitespace(row_values.get("recommended_action")),
        "issue_counts": _row_counts(row_values.get("issue_counts"), "issue"),
    }


def _source_trend_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        for row in snapshot["source_health"]:
            source_key = compact_whitespace(row.get("source_id")) or compact_whitespace(
                row.get("source_name")
            )
            if not source_key:
                continue
            enriched = {
                **row,
                "snapshot_generated_at": snapshot["generated_at"],
                "snapshot_run_id": snapshot["run_id"],
            }
            rows_by_source.setdefault(source_key, []).append(enriched)

    trend_rows: list[dict[str, Any]] = []
    for source_key, source_rows in rows_by_source.items():
        ordered_rows = sorted(source_rows, key=_source_snapshot_sort_key)
        latest = ordered_rows[-1]
        previous = ordered_rows[-2] if len(ordered_rows) > 1 else None
        issue_counts_total = _combined_issue_counts(ordered_rows)
        status_counts = Counter(str(row["status"]) for row in ordered_rows)
        trend_rows.append(
            {
                "source_id": source_key,
                "source_name": latest["source_name"],
                "snapshot_count": len(ordered_rows),
                "first_seen": ordered_rows[0]["snapshot_generated_at"],
                "latest_seen": latest["snapshot_generated_at"],
                "previous_status": previous["status"] if previous else None,
                "latest_status": latest["status"],
                "trend": _trend_label(previous, latest),
                "recommended_action": latest["recommended_action"],
                "latest_issue_count": latest["issue_count"],
                "latest_issue_rate": latest["issue_rate"],
                "latest_newsfeed_excluded": latest["newsfeed_excluded"],
                "latest_feed_fetch_failed": latest["feed_fetch_failed"],
                "latest_info_issue_count": latest["info_issue_count"],
                "latest_warn_issue_count": latest["warn_issue_count"],
                "latest_fail_issue_count": latest["fail_issue_count"],
                "latest_missing_rss_content_items": latest["missing_rss_content_items"],
                "total_issue_count": sum(_safe_int(row.get("issue_count")) for row in ordered_rows),
                "total_feed_fetch_failed": sum(
                    _safe_int(row.get("feed_fetch_failed")) for row in ordered_rows
                ),
                "total_info_issue_count": sum(
                    _safe_int(row.get("info_issue_count")) for row in ordered_rows
                ),
                "total_warn_issue_count": sum(
                    _safe_int(row.get("warn_issue_count")) for row in ordered_rows
                ),
                "total_fail_issue_count": sum(
                    _safe_int(row.get("fail_issue_count")) for row in ordered_rows
                ),
                "total_missing_rss_content_items": sum(
                    _safe_int(row.get("missing_rss_content_items")) for row in ordered_rows
                ),
                "total_accepted_content_type_filter_items": sum(
                    _safe_int(row.get("accepted_content_type_filter_items")) for row in ordered_rows
                ),
                "status_counts": {
                    status: status_counts.get(status, 0) for status in SOURCE_HEALTH_STATUSES
                },
                "issue_counts": _top_count_rows(issue_counts_total),
            }
        )
    return trend_rows


def _trend_label(previous: dict[str, Any] | None, latest: dict[str, Any]) -> str:
    if previous is None:
        return "new"
    previous_rank = SOURCE_HEALTH_STATUS_RANK.get(str(previous.get("status")), 0)
    latest_rank = SOURCE_HEALTH_STATUS_RANK.get(str(latest.get("status")), 0)
    if latest_rank > previous_rank:
        return "worse"
    if latest_rank < previous_rank:
        return "improved"
    previous_issue_count = _safe_int(previous.get("issue_count"))
    latest_issue_count = _safe_int(latest.get("issue_count"))
    if latest_issue_count > previous_issue_count:
        return "worse"
    if latest_issue_count < previous_issue_count:
        return "improved"
    return "unchanged"


def _source_trend_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, int, int, str]:
    status_rank = SOURCE_HEALTH_STATUS_RANK.get(str(row.get("latest_status")), 0)
    degraded_rank = 1 if row.get("trend") == "worse" else 0
    return (
        -status_rank,
        -degraded_rank,
        -_safe_int(row.get("latest_feed_fetch_failed")),
        -_safe_int(row.get("latest_fail_issue_count")),
        -_safe_int(row.get("latest_warn_issue_count")),
        -_safe_int(row.get("latest_issue_count")),
        str(row.get("source_name")),
    )


def _source_snapshot_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        compact_whitespace(row.get("snapshot_generated_at")),
        compact_whitespace(row.get("snapshot_run_id")),
    )


def _snapshot_sort_key(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    return (
        compact_whitespace(snapshot.get("generated_at")),
        compact_whitespace(snapshot.get("run_id")),
        compact_whitespace(snapshot.get("path")),
    )


def _latest_status_counts(source_rows: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in source_rows:
        counter[str(row.get("latest_status") or "unknown")] += 1
    return counter


def _combined_issue_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        raw_counts = row.get("issue_counts")
        if isinstance(raw_counts, dict):
            counter.update({issue: count for issue, count in raw_counts.items() if count})
    return counter


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


def _top_count_rows(counts: Counter[str], *, limit: int = 5) -> list[dict[str, Any]]:
    return [
        {"issue": issue, "count": count}
        for issue, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
